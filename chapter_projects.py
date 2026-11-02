"""Create independent, persistent video projects from saved chapter ranges."""
import asyncio
import zipfile
import copy
import json
import logging
import math
import re
import shutil
import threading
import time
import uuid
import subprocess
import tempfile
import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse
from caption_exports import video_duration
from clip_arrangement import section_arrangement
from export_jobs import ExportJobStore, cleanup_export_workspace
from ffmpeg_tools import encode_export_clip, media_executable
from processors import transcript_versions
from project_paths import (
    EXPORTS_DIR, FULLSIZE_IMAGES_DIR, SCENES_DIR, SUMMARIES_DIR, THUMBNAILS_DIR,
    TRANSCRIPTS_DIR, VIDEO_DIR,
)
from slide_exports import slide_seconds
from video_ranges import clip_timestamp, split_ranges
from resource_links import retime_resources
from video_privacy import EFFECTS, privacy_regions, privacy_filter, video_dimensions, scan_people


router = APIRouter()
logger = logging.getLogger(__name__)
jobs = ExportJobStore(EXPORTS_DIR / "chapter-project-jobs")
_metadata_lock = threading.Lock()
_tasks = set()
_active_jobs = {}
video_jobs = ExportJobStore(EXPORTS_DIR / "chapter-video-jobs")
privacy_jobs = ExportJobStore(EXPORTS_DIR / "privacy-scan-jobs")


async def extract_caption_tracks(video_path, project_id):
    probe = await asyncio.to_thread(
        subprocess.run, [media_executable("ffprobe"), "-v", "error", "-select_streams", "s",
                         "-show_entries", "stream=index:stream_tags=language,title:stream_disposition=default",
                         "-of", "json", str(video_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if probe.returncode:
        raise RuntimeError(f"Could not inspect chapter captions: {probe.stderr[-2000:]}")
    tracks = []
    for index, stream in enumerate(json.loads(probe.stdout).get("streams", [])):
        path = TRANSCRIPTS_DIR / f"{project_id}_caption_{index}.vtt"
        result = await asyncio.to_thread(
            subprocess.run, [media_executable("ffmpeg"), "-nostdin", "-y", "-v", "error",
                             "-i", str(video_path), "-map", f"0:{stream['index']}", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode:
            raise RuntimeError(f"Could not preserve chapter captions: {result.stderr[-2000:]}")
        tags = stream.get("tags", {})
        tracks.append({"label": tags.get("title") or tags.get("language") or f"Captions {index + 1}",
                       "language": tags.get("language", "und"), "index": index,
                       "default": bool(stream.get("disposition", {}).get("default"))})
    return tracks

def save_video_metadata(video_id, entry):
    with _metadata_lock:
        path = VIDEO_DIR / "metadata.json"
        metadata = _read(path, {})
        metadata[video_id] = {**metadata.get(video_id, {}), **entry}
        transcript_versions._write_json(path, metadata)


def _read(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def _seconds(value):
    parts = str(value).split(":")
    if len(parts) not in (2, 3):
        raise ValueError("Invalid saved chapter timestamp.")
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + float(part)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Invalid saved chapter timestamp.")
    return seconds


def _stamp(value):
    total = int(value)
    return f"{total // 3600:02d}:{total // 60 % 60:02d}:{total % 60:02d}"


def clip_transcript(transcript, start, end):
    """Keep overlapping cues with clipped durations and chapter-relative timing."""
    ordered = sorted(transcript, key=lambda item: item["start"])
    result = []
    for index, item in enumerate(ordered):
        cue_start = float(item["start"])
        following = float(ordered[index + 1]["start"]) if index + 1 < len(ordered) else end
        duration = item.get("duration")
        cue_end = cue_start + float(duration) if duration else following
        if cue_start >= end or cue_end <= start:
            continue
        clipped_start, clipped_end = max(start, cue_start), min(end, cue_end)
        if clipped_end <= clipped_start:
            continue
        cue = {**item, "start": round(clipped_start - start, 6),
               "duration": round(clipped_end - clipped_start, 6)}
        if "end" in item:
            cue["end"] = round(clipped_end - start, 6)
        if isinstance(item.get("words"), list):
            cue["words"] = [
                {**word, "start": max(0, float(word["start"]) - start),
                 "end": min(end, float(word["end"])) - start}
                for word in item["words"]
                if float(word["start"]) < end and float(word["end"]) > start
            ]
        result.append(cue)
    return result


def offset_cues(cues, offset):
    for cue in cues:
        for item in [cue, *cue.get("words", [])]:
            for key in ("start", "end"):
                if key in item:
                    item[key] = round(item[key] + offset, 6)
    return cues


def copy_slides(video_id, project_id, scenes, start, end, index_offset=0):
    ordered = sorted(enumerate(scenes), key=lambda item: slide_seconds(item[1]))
    active = [item for item in ordered if slide_seconds(item[1]) <= start]
    selected = (active[-1:] if active else []) + [
        item for item in ordered if start < slide_seconds(item[1]) < end
    ]
    result = []
    for index, (old_index, original) in enumerate(selected, index_offset):
        scene = copy.deepcopy(original)
        scene.pop("index", None)
        relative = max(0, slide_seconds(scene) - start)
        scene.update(time_seconds=relative, timestamp=_stamp(relative))
        for key, directory, route in (
            ("thumbnail", THUMBNAILS_DIR, "thumbnails"),
            ("fullsize", FULLSIZE_IMAGES_DIR, "fullsize_images"),
        ):
            source = directory / video_id / f"{old_index}.jpg"
            if not source.is_file():
                raise ValueError(f"Screenshot for slide {old_index + 1} is missing. Reload or detect slides again.")
            target = directory / project_id / f"{index}.jpg"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            scene[key] = f"/{route}/{project_id}/{index}.jpg"
        result.append(scene)
    for index, scene in enumerate(result):
        following = result[index + 1]["time_seconds"] if index + 1 < len(result) else end - start
        scene["duration"] = following - scene["time_seconds"]
    return result


def _media_source(video_id, data):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", video_id):
        raise HTTPException(400, "Invalid video ID.")
    videos = [path for path in VIDEO_DIR.glob(f"{video_id}.*")
              if path.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi")]
    if not videos:
        raise HTTPException(404, "Source video not found.")
    media_id = data.get("media_export_id")
    if not media_id:
        return videos[0]
    if not isinstance(media_id, str):
        raise HTTPException(400, "Invalid caption video ID.")
    store = ExportJobStore(EXPORTS_DIR / "jobs")
    try:
        job = store.read(media_id)
        if (job["video_id"] != video_id or job["status"] != "complete"
                or not job["title"].startswith("Captioned video - ")
                or not job.get("filename", "").lower().endswith(".mp4")):
            raise HTTPException(400, "Choose a completed caption copy of this video.")
        return store.file(media_id, job["download_file"])
    except (ValueError, FileNotFoundError, KeyError) as error:
        raise HTTPException(404, "The selected captioned video is missing.") from error


def _snapshot(video_id, data, *, full_video=False):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", video_id):
        raise HTTPException(400, "Invalid video ID.")
    if not isinstance(data, dict):
        raise HTTPException(400, "Chapter project settings must be an object.")
    index = 0 if full_video else data.get("chapter_index")
    caption_index = data.get("caption_track_index")
    if caption_index is not None and (type(caption_index) is not int or not 0 <= caption_index < 32):
        raise HTTPException(400, "Invalid caption track selection.")
    language, source = data.get("language", ""), data.get("source", "whisper")
    if (type(index) is not int or index < 0 or not isinstance(language, str)
            or language not in ("", "en", "de", "ar", "pl")
            or not isinstance(source, str) or source not in ("whisper", "uploaded", "youtube")):
        raise HTTPException(400, "Invalid chapter, language or transcript source.")
    chapter_language = data.get("chapter_language", language)
    if chapter_language not in ("", "en", "de", "ar", "pl"):
        raise HTTPException(400, "Invalid chapter language.")
    chapters = _read(SUMMARIES_DIR / (f"{video_id}_summary_{chapter_language}.json" if chapter_language else f"{video_id}.json"), [])
    if not chapters and chapter_language:
        chapters = _read(SUMMARIES_DIR / f"{video_id}.json", [])
    if chapters and isinstance(chapters[0], list):
        chapters = [chapter for group in chapters for chapter in group]
    if not isinstance(chapters, list) or (not full_video and index >= len(chapters)):
        raise HTTPException(409, "Chapters changed. Reload them before opening a chapter project.")
    saved_arrangement = chapters[0].get("clip_arrangement") if chapters else None
    if not full_video and data.get("section_index") is None and data.get("arrangement") != saved_arrangement:
        raise HTTPException(409, "Clip order changed. Reload the outline before exporting.")
    chapter = {"timestamp": "00:00", "title": "Speed-adjusted video"} if full_video else chapters[index]
    next_timestamp = chapters[index + 1]["timestamp"] if not full_video and index + 1 < len(chapters) else None
    section_index = None if full_video else data.get("section_index")
    if section_index is not None:
        if type(section_index) is not int or section_index < 0:
            raise HTTPException(400, "Invalid section selection.")
        sections = chapter.get("sections", [])
        if section_index >= len(sections):
            raise HTTPException(409, "Sections changed. Reload them before creating a project.")
        chapter = sections[section_index]
        if section_index + 1 < len(sections):
            next_timestamp = sections[section_index + 1]["timestamp"]
    start = _seconds(chapter["timestamp"])
    end = _seconds(next_timestamp) if next_timestamp is not None else None
    if not full_video and (data.get("timestamp") != chapter["timestamp"] or data.get("title") != chapter["title"]
                           or data.get("next_timestamp") != next_timestamp):
        raise HTTPException(409, "Chapter or section boundaries changed. Reload them before creating a project.")
    source_path = _media_source(video_id, data)
    transcript_path = TRANSCRIPTS_DIR / (
        f"{video_id}_translated_{language}.json" if language else f"{video_id}_{source}.json"
    )
    transcript = _read(transcript_path)
    if transcript is None and source == "youtube" and not language:
        transcript = _read(TRANSCRIPTS_DIR / f"{video_id}.json", [])
    transcript = transcript or []
    if not isinstance(transcript, list):
        raise ValueError("Saved transcript has an invalid format.")
    for item in transcript:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError("Saved transcript has an invalid format.")
        for key in ("start", "duration"):
            value = item.get(key, 0) or 0
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("Saved transcript timing is invalid.")
    version_id = data.get("transcript_version_id", "")
    if not isinstance(version_id, str):
        raise HTTPException(400, "Invalid transcript version.")
    if version_id:
        entry, saved = transcript_versions.read_version(video_id, version_id)
        if (entry.get("language") or "") != language or transcript_versions.content_hash(saved) != transcript_versions.content_hash(transcript):
            raise HTTPException(409, "The selected transcript version changed. Reload it before creating a project.")
    scenes = _read(SCENES_DIR / f"{video_id}.json", [])
    if not isinstance(scenes, list) or any(not isinstance(scene, dict) for scene in scenes):
        raise ValueError("Saved slides have an invalid format.")
    metadata = _read(TRANSCRIPTS_DIR / f"{video_id}_whisper_meta.json", {})
    baseline = _read(TRANSCRIPTS_DIR / f"{video_id}_whisper.json", []) if language else transcript
    return {
        "video_id": video_id, "video_path": source_path, "start": start, "end": end,
        "title": chapter["title"], "transcript": transcript, "language": language,
        "source": source, "scenes": scenes, "metadata": metadata, "baseline": baseline or transcript,
        "caption_track_index": caption_index,
        "chapters": chapters, "chapter_language": chapter_language,
    }


def caption_disposition(snapshot):
    index = snapshot.get("caption_track_index")
    return ["-disposition:s", "0", f"-disposition:s:{index}", "default"] if index is not None else []


def precise_stamp(seconds):
    millis = round(seconds * 1000)
    return f"{millis // 3600000:02d}:{millis // 60000 % 60:02d}:{millis % 60000 / 1000:06.3f}"


def scale_cues(cues, rate):
    result = copy.deepcopy(cues)
    for cue in result:
        for key in ("start", "duration", "end"):
            if key in cue:
                cue[key] = round(float(cue[key]) / rate, 6)
        for word in cue.get("words", []):
            for key in ("start", "end", "duration"):
                if key in word:
                    word[key] = round(float(word[key]) / rate, 6)
    return result


def clip_speed_chapters(chapters, start, end, rate):
    result = []
    for index, original in enumerate(chapters):
        left = _seconds(original["timestamp"])
        right = _seconds(chapters[index + 1]["timestamp"]) if index + 1 < len(chapters) else end
        if left >= end or right <= start:
            continue
        chapter = copy.deepcopy(original)
        chapter.pop("clip_arrangement", None)
        chapter["timestamp"] = precise_stamp((max(left, start) - start) / rate)
        if "resources" in chapter:
            chapter["resources"] = retime_resources(chapter["resources"], start, end, rate)
        sections = []
        parts = original.get("sections", [])
        for part_index, part in enumerate(parts):
            part_start = _seconds(part["timestamp"])
            part_end = _seconds(parts[part_index + 1]["timestamp"]) if part_index + 1 < len(parts) else right
            if part_start < end and part_end > start:
                sections.append({**copy.deepcopy(part),
                                 "timestamp": precise_stamp((max(part_start, start) - start) / rate)})
                if "resources" in part:
                    sections[-1]["resources"] = retime_resources(part["resources"], start, end, rate)
        if "sections" in original:
            chapter["sections"] = sections
        result.append(chapter)
    return result


def speed_vtt(content, start, end, rate, offset=0):
    blocks = ["WEBVTT"]
    for block in re.split(r"\r?\n\s*\r?\n", content):
        lines = block.splitlines()
        timing = next((index for index, line in enumerate(lines) if " --> " in line), None)
        if timing is None:
            continue
        match = re.fullmatch(r"([\d:.]+)\s+-->\s+([\d:.]+)(.*)", lines[timing])
        if not match:
            raise ValueError("Could not retime an embedded subtitle cue.")
        left, right = _seconds(match[1]), _seconds(match[2])
        if left >= end or right <= start:
            continue
        lines[timing] = (f"{precise_stamp((max(left, start) - start) / rate + offset)} --> "
                         f"{precise_stamp((min(right, end) - start) / rate + offset)}{match[3]}")
        # WebVTT inline karaoke timestamps must follow the same time transform.
        for index in range(timing + 1, len(lines)):
            lines[index] = re.sub(r"<((?:\d+:)?\d+:\d+\.\d+)>",
                lambda m: f"<{precise_stamp(max(0, min(end, _seconds(m[1])) - start) / rate + offset)}>", lines[index])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n\n"


async def remux_arranged_captions(snapshot, clips, media, target, step):
    probe = await asyncio.to_thread(subprocess.run, [
        media_executable("ffprobe"), "-v", "error", "-select_streams", "s",
        "-show_entries", "stream=index,codec_name:stream_tags=language,title:stream_disposition=default",
        "-of", "json", str(snapshot["video_path"])],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    if probe.returncode:
        raise RuntimeError(f"Could not inspect video captions: {probe.stderr[-2000:]}")
    streams = json.loads(probe.stdout).get("streams", [])
    tracks = []
    for index, stream in enumerate(streams):
        if stream.get("codec_name") not in {"mov_text", "subrip", "webvtt", "ass", "ssa", "text"}:
            raise ValueError("Reordered clips require text subtitles. Choose the original video or a text-caption version.")
        original = media.parent / f"source-{index}.vtt"
        await encode_export_clip([
            media_executable("ffmpeg"), "-y", "-v", "error", "-i", str(snapshot["video_path"]),
            "-map", f"0:{stream['index']}", "-f", "webvtt", str(original)], step, "Reading source captions")
        content = original.read_text(encoding="utf-8-sig")
        blocks, offset = ["WEBVTT"], 0
        for clip in clips:
            transformed = speed_vtt(content, clip["start"], clip["end"], 1, offset)
            blocks.append(transformed.split("\n\n", 1)[1].strip())
            offset += clip["end"] - clip["start"]
        transformed = "\n\n".join(block for block in blocks if block) + "\n\n"
        if "-->" in transformed:
            path = media.parent / f"arranged-{index}.vtt"
            path.write_text(transformed, encoding="utf-8")
            tracks.append((path, stream, index))
    if not tracks:
        media.replace(target)
        return
    command = [media_executable("ffmpeg"), "-y", "-v", "error", "-i", str(media)]
    for path, _, _ in tracks:
        command += ["-i", str(path)]
    command += ["-map", "0:v:0", "-map", "0:a?"]
    for index in range(len(tracks)):
        command += ["-map", f"{index + 1}:0"]
    command += ["-c", "copy", "-c:s", "mov_text"]
    for index, (_, stream, source_index) in enumerate(tracks):
        for key in ("language", "title"):
            if stream.get("tags", {}).get(key):
                command += [f"-metadata:s:s:{index}", f"{key}={stream['tags'][key]}"]
        selected = snapshot.get("caption_track_index")
        default = source_index == selected if selected is not None else stream.get("disposition", {}).get("default")
        command += [f"-disposition:s:{index}", "default" if default else "0"]
    command += ["-movflags", "+faststart", "-f", "mp4", str(target)]
    await encode_export_clip(command, step, "Saving reordered captions")


async def encode_speed_video(snapshot, target, step):
    start, end, rate = snapshot["start"], snapshot["end"], snapshot["speed"]
    probe = await asyncio.to_thread(subprocess.run, [
        media_executable("ffprobe"), "-v", "error", "-select_streams", "s",
        "-show_entries", "stream=index,codec_name:stream_tags=language,title:stream_disposition=default",
        "-of", "json", str(snapshot["video_path"])],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    if probe.returncode:
        raise RuntimeError(f"Could not inspect video captions: {probe.stderr[-2000:]}")
    streams = json.loads(probe.stdout).get("streams", [])
    with tempfile.TemporaryDirectory(prefix="lecture-speed-") as directory:
        workspace = Path(directory)
        tracks = []
        for index, stream in enumerate(streams):
            if stream.get("codec_name") not in {"mov_text", "subrip", "webvtt", "ass", "ssa", "text"}:
                raise ValueError("This video has image-based subtitles that cannot be retimed. Select a video version with text subtitles.")
            step(f"Retiming caption track {index + 1}")
            original = workspace / f"source-{index}.vtt"
            await encode_export_clip([
                media_executable("ffmpeg"), "-y", "-v", "error", "-i", str(snapshot["video_path"]),
                "-map", f"0:{stream['index']}", "-f", "webvtt", str(original)],
                step, "Extracting captions")
            transformed = speed_vtt(original.read_text(encoding="utf-8-sig"), start, end, rate)
            if "-->" not in transformed:
                continue
            path = workspace / f"track-{index}.vtt"
            path.write_text(transformed, encoding="utf-8")
            tracks.append((path, stream, index))
        factors = []
        remaining = rate
        while remaining > 2:
            factors.append(2)
            remaining /= 2
        while remaining < 0.5:
            factors.append(0.5)
            remaining /= 0.5
        factors.append(remaining)
        command = [media_executable("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                   "-ss", str(start), "-t", str(end - start), "-i", str(snapshot["video_path"])]
        for path, _, _ in tracks:
            command += ["-i", str(path)]
        command += ["-map", "0:v:0", "-map", "0:a?"]
        for index in range(len(tracks)):
            command += ["-map", f"{index + 1}:0"]
        command += ["-vf", f"setpts=(PTS-STARTPTS)/{rate}",
                    "-af", "asetpts=PTS-STARTPTS," + ",".join(f"atempo={factor}" for factor in factors),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-c:s", "mov_text"]
        for index, (_, stream, original_index) in enumerate(tracks):
            for key in ("language", "title"):
                if stream.get("tags", {}).get(key):
                    command += [f"-metadata:s:s:{index}", f"{key}={stream['tags'][key]}"]
            selected = snapshot.get("caption_track_index")
            default = original_index == selected if selected is not None else stream.get("disposition", {}).get("default")
            command += [f"-disposition:s:{index}", "default" if default else "0"]
        command += ["-movflags", "+faststart", "-f", "mp4", str(target)]
        await encode_export_clip(command, step, "Changing video speed", (end - start) / rate)


@router.post("/speed_video/{video_id}")
async def start_speed_video(video_id: str, request: Request):
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(400, "Speed settings must be an object.")
    rate = data.get("speed")
    if type(rate) not in (int, float) or not math.isfinite(rate) or not 0.25 <= rate <= 4:
        raise HTTPException(400, "Choose a speed between 0.25 and 4.")
    if data.get("output", "video") not in {"video", "project"}:
        raise HTTPException(400, "Choose MP4 or a new project.")
    try:
        snapshot = _snapshot(video_id, data, full_video=True)
        duration = await video_duration(snapshot["video_path"])
        start = clip_timestamp(data.get("start", "00:00"))
        end = clip_timestamp(data["end"]) if data.get("end") else duration
        if not 0 <= start < end <= duration + 0.05:
            raise ValueError("Start and End must be inside the selected video.")
        end = min(end, duration)
        filename = _read(VIDEO_DIR / "metadata.json", {}).get(video_id, {}).get("filename", "Video")
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(filename).stem).strip(" .")[:90] or "Video"
        snapshot.update(start=start, end=end, speed=rate, title=f"{title} {rate:g}x",
                        chapter_language=data.get("chapter_language", data.get("language", "")))
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(400, f"Could not prepare speed change: {error}") from error
    if data.get("output") == "project":
        return {**start_project_job(snapshot), "kind": "project"}

    async def generate(parent, options, step, artifacts, workspace):
        target = workspace / f"{snapshot['title']}.mp4"
        await encode_speed_video(snapshot, target, step)
        return FileResponse(target)
    return {"job_id": video_jobs.start(video_id, {"document_title": snapshot["title"]}, generate),
            "kind": "video"}


@router.get("/speed_project_jobs/{job_id}/download")
def download_speed_project(job_id: str):
    state = chapter_project_status(job_id)
    if state["status"] != "complete":
        raise HTTPException(409, "The new project is not ready.")
    project = state["project"]
    metadata = _read(VIDEO_DIR / "metadata.json", {}).get(project["video_id"], {})
    if "speed" not in metadata.get("chapter_project", {}):
        raise HTTPException(400, "This is not a speed-adjusted project.")
    return FileResponse(VIDEO_DIR / f"{project['video_id']}.mp4", media_type="video/mp4",
                        filename=project["filename"])


async def create_project(snapshot, project_id, step):
    video_id = snapshot["video_id"]
    start = snapshot["start"]
    duration = await video_duration(snapshot["video_path"])
    end = snapshot["end"] if snapshot["end"] is not None else duration
    if not 0 <= start < end <= duration + 0.1:
        raise ValueError("Chapter timestamps are outside the source video's duration.")
    end = min(end, duration)
    video_path = VIDEO_DIR / f"{project_id}.pending"
    published_path = VIDEO_DIR / f"{project_id}.mp4"
    committed = False
    try:
        command = [
            media_executable("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
            "-ss", str(start), "-i", str(snapshot["video_path"]), "-t", str(end - start),
            "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?", "-c:s", "mov_text",
            "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-c:a", "aac", *caption_disposition(snapshot),
            "-movflags", "+faststart", "-f", "mp4", str(video_path),
        ]
        step("Creating an independent chapter video")
        rate = snapshot.get("speed", 1)
        if "clips" in snapshot:
            with tempfile.TemporaryDirectory(prefix="lecture-arrangement-") as directory:
                paths = []
                for index, clip in enumerate(snapshot["clips"]):
                    target = Path(directory) / f"part-{index:04d}.mp4"
                    step(f"Preparing arranged clip {index + 1} of {len(snapshot['clips'])}")
                    await _encode_video_download({**clip, "omit_captions": True}, duration, target, step)
                    paths.append(target)
                merged = Path(directory) / "merged.mp4"
                await concatenate_clips(paths, merged, step,
                                        [clip["end"] - clip["start"] for clip in snapshot["clips"]])
                await remux_arranged_captions(snapshot, snapshot["clips"], merged, video_path, step)
        elif "speed" in snapshot:
            await encode_speed_video(snapshot, video_path, step)
        else:
            await encode_export_clip(command, step, "Chapter project", end - start)
        step("Copying transcript, speakers and available slides")
        caption_tracks = await extract_caption_tracks(video_path, project_id)
        transcript = clip_transcript(snapshot["transcript"], start, end)
        baseline = clip_transcript(snapshot["baseline"], start, end)
        scenes = await asyncio.to_thread(
            copy_slides, video_id, project_id, snapshot["scenes"], start, end,
        ) if "clips" not in snapshot else []
        if "clips" in snapshot:
            transcript, baseline, chapters = [], [], []
            offset = 0
            for clip in snapshot["clips"]:
                left, right = clip["start"], clip["end"]
                transcript.extend(offset_cues(clip_transcript(snapshot["transcript"], left, right), offset))
                baseline.extend(offset_cues(clip_transcript(snapshot["baseline"], left, right), offset))
                copied = await asyncio.to_thread(copy_slides, video_id, project_id, snapshot["scenes"],
                                                 left, right, len(scenes))
                for scene in copied:
                    scene["time_seconds"] += offset
                    scene["timestamp"] = precise_stamp(scene["time_seconds"])
                scenes.extend(copied)
                owner = clip["output_chapter"]
                if not chapters or chapters[-1]["source_timestamp"] != owner["timestamp"]:
                    chapter = {**copy.deepcopy(owner), "timestamp": precise_stamp(offset),
                               "source_timestamp": owner["timestamp"], "sections": []}
                    if "resources" in owner:
                        chapter["resources"] = []
                    chapter.pop("clip_arrangement", None)
                    chapters.append(chapter)
                if "section_content" in clip:
                    parts = [{**clip["section_content"], "timestamp": precise_stamp(offset)}]
                    if "resources" in parts[0]:
                        parts[0]["resources"] = retime_resources(parts[0]["resources"], left, right, offset=offset)
                else:
                    clipped = clip_speed_chapters([owner], left, right, 1)
                    parts = clipped[0].get("sections", []) if clipped else []
                    for part in parts:
                        part["timestamp"] = precise_stamp(_seconds(part["timestamp"]) + offset)
                        if "resources" in part:
                            part["resources"] = retime_resources(part["resources"], 0, right - left, offset=offset)
                if "resources" in owner:
                    for link in retime_resources(owner["resources"], left, right, offset=offset):
                        if link not in chapters[-1]["resources"]:
                            chapters[-1]["resources"].append(link)
                chapters[-1]["sections"].extend(parts)
                offset += right - left
            for chapter in chapters:
                chapter.pop("source_timestamp")
            output_duration = offset
        if "clips" not in snapshot:
            source_chapters = snapshot["chapters"]
            has_resources = any(row.get("resources") for chapter in source_chapters
                                for row in [chapter, *chapter.get("sections", [])])
            chapters = clip_speed_chapters(source_chapters, start, end, rate) if "speed" in snapshot or has_resources else []
        if chapters or "speed" in snapshot or "clips" in snapshot:
            if "speed" in snapshot:
                transcript = scale_cues(transcript, rate)
                baseline = scale_cues(baseline, rate)
                for scene in scenes:
                    scene["time_seconds"] /= rate
                    scene["duration"] /= rate
                    scene["timestamp"] = precise_stamp(scene["time_seconds"])
            language = snapshot["language"]
            chapter_language = snapshot.get("chapter_language", language)
            transcript_versions._write_json(SUMMARIES_DIR / f"{project_id}.json", chapters)
            if chapter_language:
                transcript_versions._write_json(SUMMARIES_DIR / f"{project_id}_summary_{chapter_language}.json", chapters)
        provenance = {
            "parent_video_id": video_id, "start": start, "end": end,
            "title": snapshot["title"], "source": snapshot["source"], "language": snapshot["language"],
        }
        if "speed" in snapshot:
            provenance["speed"] = rate
        if "clips" in snapshot:
            provenance["clips"] = [{"start": clip["start"], "end": clip["end"]} for clip in snapshot["clips"]]
        if baseline or transcript:
            baseline = baseline or transcript
            metadata = {**snapshot["metadata"], "chapter_project": provenance}
            transcript_versions._write_json(TRANSCRIPTS_DIR / f"{project_id}_whisper.json", baseline)
            transcript_versions._write_json(TRANSCRIPTS_DIR / f"{project_id}_whisper_meta.json", metadata)
            transcript_versions.save_version(
                project_id, "transcript", baseline, source="whisper",
                model=metadata.get("model") or "chapter:excerpt", metadata=metadata,
                chapters=chapters,
            )
            language = snapshot["language"]
            if language and transcript:
                transcript_versions._write_json(TRANSCRIPTS_DIR / f"{project_id}_translated_{language}.json", transcript)
                transcript_versions.save_version(project_id, "translation", transcript, language=language,
                    chapters=chapters)
                transcript_versions.set_active_source(project_id, "translation", language=language)
            else:
                transcript_versions.set_active_source(project_id, "transcript", source="whisper")
        transcript_versions._write_json(SCENES_DIR / f"{project_id}.json", scenes)
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", snapshot["title"]).strip(" .")[:100] or "Chapter"
        filename += ".mp4"
        video_path.replace(published_path)
        save_video_metadata(project_id, {"filename": filename, "chapter_project": provenance,
                                         "caption_tracks": caption_tracks})
        committed = True
        return {"video_id": project_id, "filename": filename,
                "duration": output_duration if "clips" in snapshot else (end - start) / rate,
                "transcript_segments": len(transcript), "slides": len(scenes)}
    finally:
        if not committed:
            video_path.unlink(missing_ok=True)
            published_path.unlink(missing_ok=True)
            for path in TRANSCRIPTS_DIR.glob(f"{project_id}_*.json"):
                path.unlink()
            (SCENES_DIR / f"{project_id}.json").unlink(missing_ok=True)
            for path in SUMMARIES_DIR.glob(f"{project_id}*.json"):
                path.unlink()
            for path in TRANSCRIPTS_DIR.glob(f"{project_id}_caption_*.vtt"):
                path.unlink()
            for directory in (TRANSCRIPTS_DIR / "versions", THUMBNAILS_DIR, FULLSIZE_IMAGES_DIR):
                target = directory / project_id
                if target.is_dir():
                    cleanup_export_workspace(target)


@router.post("/chapter_projects/{video_id}")
async def start_chapter_project(video_id: str, request: Request):
    try:
        data = await request.json()
        snapshot = _snapshot(video_id, data)
        base = _snapshot(video_id, data, full_video=True)
        if base["chapters"] and base["chapters"][0].get("clip_arrangement"):
            snapshot = await prepare_arranged_project(video_id, {**data, "selections": [data]})
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(400, f"Could not read chapter project content: {error}") from error
    return start_project_job(snapshot)


def start_project_job(snapshot):
    video_id = snapshot["video_id"]
    job_id, project_id = uuid.uuid4().hex, uuid.uuid4().hex
    state = {
        "id": job_id, "video_id": video_id, "instance": jobs.instance, "status": "queued",
        "created_at": time.time(), "steps": [], "title": snapshot["title"],
    }

    def step(message):
        if message == "Copying transcript, speakers and available slides":
            state["phase"] = "finalizing"
        state["steps"].append({"elapsed": round(time.time() - state["created_at"], 1), "message": str(message)})
        jobs._write(job_id, state)

    async def work():
        try:
            if state.get("cancel_requested"):
                raise asyncio.CancelledError()
            state["status"] = "running"
            state["phase"] = "encoding"
            step("Chapter project queued for encoding")
            state["project"] = await create_project(snapshot, project_id, step)
            state["status"] = "complete"
            step("Chapter project ready")
        except asyncio.CancelledError:
            state["status"] = "cancelled"
            step("Chapter project cancelled; incomplete output removed")
        except Exception as error:
            logger.exception("Chapter project creation failed")
            state["status"], state["error"] = "error", str(error)
        finally:
            state["finished_at"] = time.time()
            jobs._write(job_id, state)
            _active_jobs.pop(job_id, None)

    jobs._write(job_id, state)
    task = asyncio.create_task(work())
    _tasks.add(task)
    _active_jobs[job_id] = (task, state)
    task.add_done_callback(_tasks.discard)
    return {"job_id": job_id}


@router.post("/chapter_project_jobs/{job_id}/cancel")
async def cancel_chapter_project(job_id: str):
    current = _active_jobs.get(job_id)
    if not current:
        state = chapter_project_status(job_id)
        if state["status"] == "cancelled":
            return {"status": "cancelled"}
        raise HTTPException(409, "This chapter project is no longer encoding.")
    task, state = current
    if state.get("cancel_requested"):
        return {"status": "cancelling"}
    if state.get("phase") == "finalizing":
        raise HTTPException(409, "Encoding has finished and the project is being saved. It can no longer be cancelled.")
    state["cancel_requested"] = True
    # A queued task must enter its try/finally before it can be cancelled.
    if state["status"] == "running":
        task.cancel()
    return {"status": "cancelling"}


@router.post("/chapter_video/{video_id}")
async def start_chapter_video(video_id: str, request: Request):
    try:
        data = await request.json()
        snapshot = _snapshot(video_id, data)
        base = _snapshot(video_id, data, full_video=True)
        if base["chapters"] and base["chapters"][0].get("clip_arrangement") and data.get("section_index") is None:
            return await _start_selected_videos(video_id, {**data, "selections": [data], "combine": True})
        if base["chapters"] and base["chapters"][0].get("clip_arrangement"):
            snapshot["retime_captions"] = True
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(400, f"Could not read chapter video: {error}") from error

    return _start_video_download(video_id, snapshot)


@router.post("/custom_video/{video_id}")
async def start_custom_video(video_id: str, request: Request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("Clip settings must be an object.")
        start, end = clip_timestamp(data.get("start")), clip_timestamp(data.get("end"))
        caption_index = data.get("caption_track_index")
        if caption_index is not None and (type(caption_index) is not int or not 0 <= caption_index < 32):
            raise ValueError("Invalid caption track selection.")
        source = _media_source(video_id, data)
        duration = await video_duration(source)
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration:
            raise ValueError("Start must be before end, and both must be inside the video.")
        regions = privacy_regions(data.get("privacy_regions", []), duration)
        if regions and not any(region["start"] < end and region["end"] > start for region in regions):
            raise ValueError("No privacy regions overlap this clip. Adjust their timing or turn masking off.")
        snapshot = {"video_path": source, "start": start, "end": end,
                    "caption_track_index": caption_index,
                    "privacy_regions": regions,
                    "title": f"Clip_{data['start'].replace(':', '-')}_to_{data['end'].replace(':', '-')}"}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(400, f"Could not prepare custom clip: {error}") from error
    return _start_video_download(video_id, snapshot)


@router.post("/privacy_scans/{video_id}")
async def start_privacy_scan(video_id: str, request: Request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("Privacy scan settings must be an object.")
        source = _media_source(video_id, data)
        duration = await video_duration(source)
        start, end = clip_timestamp(data.get("start")), clip_timestamp(data.get("end"))
        effect = data.get("effect", "blur")
        if effect not in EFFECTS or not 0 <= start < end <= duration:
            raise ValueError("Choose a valid privacy effect and a range inside the video.")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(400, f"Could not prepare privacy scan: {error}") from error

    async def generate(parent, options, step, artifacts, workspace):
        regions = await scan_people(source, start, end, effect, step)
        target = workspace / "privacy-regions.json"
        target.write_text(json.dumps({"regions": regions}), encoding="utf-8")
        return FileResponse(target)

    return {"job_id": privacy_jobs.start(video_id, {"document_title": "People privacy scan"}, generate)}


@router.get("/privacy_scan_jobs/{job_id}")
def privacy_scan_status(job_id: str):
    try:
        state = privacy_jobs.read(job_id)
        if state["status"] == "complete":
            state["regions"] = json.loads(privacy_jobs.file(job_id, state["download_file"]).read_text(
                encoding="utf-8"))["regions"]
        return state
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, "Privacy scan not found.") from error


@router.post("/privacy_scan_jobs/{job_id}/cancel")
def cancel_privacy_scan(job_id: str):
    try:
        return privacy_jobs.cancel(job_id)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, "Privacy scan not found.") from error


@router.post("/audio_info/{video_id}")
async def audio_info(video_id: str, request: Request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("Audio settings must be an object.")
        source = _media_source(video_id, data)
        probe = await asyncio.to_thread(
            subprocess.run, [media_executable("ffprobe"), "-v", "error", "-select_streams", "a:0",
                             "-show_entries", "stream=codec_name,profile,sample_rate,channels",
                             "-of", "json", str(source)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if probe.returncode:
            raise ValueError(f"Could not inspect audio: {probe.stderr[-1000:]}")
        streams = json.loads(probe.stdout).get("streams", [])
        return {"has_audio": bool(streams), "stream": streams[0] if streams else None}
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as error:
        raise HTTPException(400, f"Could not check playback audio: {error}") from error


@router.post("/compatible_audio/{video_id}")
async def compatible_audio(video_id: str, request: Request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("Audio settings must be an object.")
        source = _media_source(video_id, data)
        stat = source.stat()
        key = hashlib.sha256(f"{source}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()[:20]
        title = f"Compatible audio {key}"
        for job in video_jobs.list(video_id):
            if job["title"] == title and job["status"] in {"queued", "running", "complete"}:
                if job["status"] == "complete":
                    video_jobs.file(job["id"], job["download_file"])
                return {"job_id": job["id"]}
        duration = await video_duration(source)
    except (OSError, ValueError, TypeError) as error:
        raise HTTPException(400, f"Could not prepare compatible audio: {error}") from error

    async def generate(parent, options, step, artifacts, workspace):
        target = workspace / "Compatible_audio.wav"
        await encode_export_clip([
            media_executable("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source), "-map", "0:a:0", "-vn", "-c:a", "pcm_s16le",
            "-ar", "16000", "-ac", "1", str(target),
        ], step, "Compatible audio", duration)
        return FileResponse(target, media_type="audio/wav")

    return {"job_id": video_jobs.start(video_id, {"document_title": title}, generate)}


def _start_video_download(video_id, snapshot):
    async def generate(parent, options, step, artifacts, workspace):
        duration = await video_duration(snapshot["video_path"])
        filename = re.sub(r'[^A-Za-z0-9_-]+', "_", snapshot["title"]).strip("_")[:100] or "Chapter"
        target = workspace / f"{filename}.mp4"
        await _encode_video_download(snapshot, duration, target, step)
        return FileResponse(target)

    job_id = video_jobs.start(video_id, {"document_title": snapshot["title"]}, generate)
    return {"job_id": job_id}


async def _encode_video_download(snapshot, duration, target, step):
    start = snapshot["start"]
    end = snapshot["end"] if snapshot["end"] is not None else duration
    if not 0 <= start < min(end, duration) or end > duration + 0.1:
        raise ValueError("Clip timestamps are outside the source video's duration.")
    end = min(end, duration)
    if snapshot.get("retime_captions"):
        await encode_speed_video({**snapshot, "end": end, "speed": 1}, target, step)
        return
    video_options = ["-map", "0:v:0"]
    if snapshot.get("privacy_regions"):
        width, height = await video_dimensions(snapshot["video_path"])
        graph, output = privacy_filter(snapshot["privacy_regions"], width, height, start, end)
        video_options = ["-filter_complex_threads", "1", "-filter_complex", graph,
                         "-map", f"[{output}]"]
        step("Applying reviewed privacy regions; audio and subtitle tracks are retained")
    command = [
        media_executable("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(start), "-i", str(snapshot["video_path"]), "-t", str(end - start),
        *video_options, "-map", "0:a?",
        *([] if snapshot.get("omit_captions") else ["-map", "0:s?", "-c:s", "mov_text"]),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
        *([] if snapshot.get("omit_captions") else caption_disposition(snapshot)),
        "-movflags", "+faststart", str(target),
    ]
    await encode_export_clip(command, step, "Video clip", end - start)


def _selected_ranges(snapshots, duration, merge, preserve_order=False):
    ordered = []
    for snapshot in snapshots:
        end = snapshot["end"] if snapshot["end"] is not None else duration
        if not 0 <= snapshot["start"] < min(end, duration) or end > duration + 0.1:
            raise ValueError("Selected timestamps are outside the source video's duration.")
        ordered.append({**snapshot, "end": min(end, duration)})
    if not preserve_order:
        ordered.sort(key=lambda clip: (clip["start"], clip["end"]))
    result = []
    for clip in ordered:
        if merge and not preserve_order and result and clip["start"] <= result[-1]["end"]:
            result[-1]["end"] = max(result[-1]["end"], clip["end"])
        else:
            result.append(clip)
    return result


def selection_snapshots(video_id, data, *, expand_chapters=False):
    if not isinstance(data, dict):
        raise ValueError("Selection settings must be an object.")
    selections = data.get("selections")
    if (not isinstance(selections, list) or not 1 <= len(selections) <= 500
            or any(not isinstance(item, dict) for item in selections)):
        raise ValueError("Choose between 1 and 500 chapters or sections.")
    def context(item):
        language = item.get("language", "")
        return (language, item.get("chapter_language", language), item.get("source", "whisper"),
                item.get("transcript_version_id", ""))
    if any(context(item) != context(selections[0]) for item in selections[1:]):
        raise ValueError("Choose clips from one saved outline and transcript version.")
    shared = {key: data[key] for key in ("media_export_id", "caption_track_index", "arrangement") if key in data}
    base = _snapshot(video_id, {**selections[0], **shared}, full_video=True)
    chapters = base["chapters"]
    arranged = bool(chapters and chapters[0].get("clip_arrangement"))
    if data.get("arrangement") != (chapters[0].get("clip_arrangement") if chapters else None):
        raise HTTPException(409, "Clip order changed. Reload the outline before exporting.")
    order = section_arrangement(chapters)
    sources = {part["timestamp"]: (ci, si) for ci, chapter in enumerate(chapters)
               for si, part in enumerate(chapter.get("sections", []))}
    rank = {entry["timestamp"]: index for index, entry in enumerate(order)}
    owners = {entry["timestamp"]: entry["chapter"] for entry in order}
    snapshots = []
    seen = set()
    for selection in selections:
        clip = _snapshot(video_id, {**selection, **shared})
        ci, si = selection["chapter_index"], selection.get("section_index")
        selected = [(ci, si)]
        if (arranged or expand_chapters) and si is None and chapters[ci].get("sections"):
            selected = [sources[entry["timestamp"]] for entry in order
                        if entry["chapter"] == chapters[ci]["timestamp"]]
        for source_chapter, source_section in selected:
            if source_section is not None:
                parent = chapters[source_chapter]
                part = parent["sections"][source_section]
                next_stamp = (parent["sections"][source_section + 1]["timestamp"]
                              if source_section + 1 < len(parent["sections"])
                              else chapters[source_chapter + 1]["timestamp"] if source_chapter + 1 < len(chapters) else None)
                item = {**clip, "start": _seconds(part["timestamp"]),
                        "end": _seconds(next_stamp) if next_stamp else None,
                        "title": part["title"], "section_content": copy.deepcopy(part),
                        "order_rank": rank[part["timestamp"]]}
                owner = owners[part["timestamp"]]
            else:
                item = {**clip, "order_rank": -1}
                owner = chapters[source_chapter]["timestamp"]
            item["output_chapter"] = next(chapter for chapter in chapters if chapter["timestamp"] == owner)
            key = (item["start"], item["end"])
            if key in seen:
                continue
            seen.add(key)
            snapshots.append(item)
            if len(snapshots) > 500:
                raise ValueError("Choose at most 500 output clips.")
    if not snapshots:
        raise ValueError("This arranged chapter contains no clips.")
    if arranged:
        chapter_rank = {chapter["timestamp"]: index for index, chapter in enumerate(chapters)}
        snapshots.sort(key=lambda clip: (chapter_rank[clip["output_chapter"]["timestamp"]], clip["order_rank"]))
    return snapshots, base, arranged


async def prepare_arranged_project(video_id, data):
    clips, base, arranged = selection_snapshots(video_id, data, expand_chapters=True)
    duration = await video_duration(base["video_path"])
    ranges = _selected_ranges(clips, duration, False, preserve_order=arranged)
    if any(left["start"] < right["end"] and right["start"] < left["end"]
           for index, left in enumerate(ranges) for right in ranges[index + 1:]):
        raise ValueError("Choose non-overlapping clips for a new project.")
    title = data["selections"][0]["title"] if len(data["selections"]) == 1 else (
        "Reordered clips" if arranged else "Selected clips")
    return {**base, "clips": ranges, "title": title,
            "chapter_language": data["selections"][0].get("chapter_language", base["language"])}


@router.post("/selected_clip_projects/{video_id}")
async def start_selected_clip_project(video_id: str, request: Request):
    try:
        snapshot = await prepare_arranged_project(video_id, await request.json())
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(400, f"Could not prepare selected clip project: {error}") from error
    return start_project_job(snapshot)


async def concatenate_clips(paths, target, step, durations=None):
    listing = paths[0].parent / "concat.txt"
    entries = [str(path.resolve()).replace("'", "'\\''") for path in paths]
    listing.write_text("".join(f"file '{path}'\n" +
                       (f"duration {durations[index]}\n" if durations is not None else "")
                       for index, path in enumerate(entries)), encoding="utf-8")
    await encode_export_clip([
        media_executable("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(listing), "-map", "0", "-c", "copy",
        "-movflags", "+faststart", "-f", "mp4", str(target),
    ], step, "Merging clips")


@router.post("/selected_videos/{video_id}")
async def start_selected_videos(video_id: str, request: Request):
    return await _start_selected_videos(video_id, await request.json())


async def _start_selected_videos(video_id, data):
    try:
        if not isinstance(data, dict):
            raise ValueError("Selection settings must be an object.")
        selections = data.get("selections")
        merge = data.get("merge_consecutive", False)
        combine = data.get("combine", False)
        if (not isinstance(selections, list) or not 1 <= len(selections) <= 500
                or not all(isinstance(item, dict) for item in selections)
                or type(merge) is not bool or type(combine) is not bool):
            raise ValueError("Choose between 1 and 500 chapters or sections and a valid merge option.")
        snapshots, _, arranged = selection_snapshots(video_id, data)
        duration = await video_duration(snapshots[0]["video_path"])
        ranges = _selected_ranges(snapshots, duration, merge or combine, preserve_order=arranged)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(400, f"Could not prepare selected clips: {error}") from error

    async def generate(parent, options, step, artifacts, workspace):
        if combine:
            paths = []
            for index, clip in enumerate(ranges):
                path = workspace / f"part-{index:04d}.mp4"
                step(f"Preparing selected part {index + 1} of {len(ranges)}")
                await _encode_video_download({**clip, "omit_captions": arranged}, duration, path, step)
                paths.append(path)
            target = workspace / "Selected_parts.mp4"
            if arranged:
                merged = workspace / "joined.mp4"
                await concatenate_clips(paths, merged, step, [clip["end"] - clip["start"] for clip in ranges])
                await remux_arranged_captions(ranges[0], ranges, merged, target, step)
            else:
                await concatenate_clips(paths, target, step)
            return FileResponse(target)
        target = workspace / "Selected_clips.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
            for index, clip in enumerate(ranges, 1):
                title = re.sub(r"[^A-Za-z0-9_-]+", "_", clip["title"]).strip("_")[:70] or "Clip"
                path = workspace / f"{index:02d}-{title}.mp4"
                await _encode_video_download({**clip, "retime_captions": arranged}, duration, path, step)
                await asyncio.to_thread(archive.write, path, path.name)
                path.unlink()
        return FileResponse(target)

    return {"job_id": video_jobs.start(video_id, {"document_title": "Selected video clips"}, generate)}


@router.post("/merge_uploaded_clips")
async def merge_uploaded_clips(files: list[UploadFile] = File(...), title: str = Form("Merged clips"),
                               save_project: bool = Form(False), trim_ranges: str = Form("")):
    return await process_uploaded_clips(files, title, save_project, trim_ranges, minimum=2)


@router.post("/trim_uploaded_clips")
async def trim_uploaded_clips(files: list[UploadFile] = File(...), title: str = Form("Trimmed clip"),
                              save_project: bool = Form(False), trim_ranges: str = Form("")):
    return await process_uploaded_clips(files, title, save_project, trim_ranges, minimum=1)


async def _save_clip_uploads(files, workspace):
    paths, names, total = [], [], 0
    for index, upload in enumerate(files):
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"}:
            raise HTTPException(400, "Upload MP4, MKV, WebM, MOV, AVI or M4V clips.")
        path = workspace / f"upload-{index:03d}{suffix}"
        size = 0
        with path.open("wb") as destination:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                total += len(chunk)
                if size > 2 * 1024 ** 3 or total > 5 * 1024 ** 3:
                    raise HTTPException(413, "Clips are limited to 2 GB each and 5 GB in total.")
                destination.write(chunk)
        if not size:
            raise HTTPException(400, "An uploaded clip is empty.")
        paths.append(path)
        names.append(Path(upload.filename).name)
    return paths, names


def _start_split_download(video_id, source, ranges, on_finish=None):
    async def generate(parent, options, step, artifacts, output):
        clips = []
        for index, (start, end) in enumerate(ranges, 1):
            step(f"Creating part {index} of {len(ranges)}")
            target = output / f"Part_{index:03d}.mp4"
            await _encode_video_download({"video_path": source, "start": start, "end": end},
                                         ranges[-1][1], target, step)
            clips.append(target)
        step("Packaging video parts into ZIP")
        target = output / "Video_parts.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
            for clip in clips:
                step(f"Adding {clip.name}")
                archive.write(clip, clip.name)
        return FileResponse(target)

    job_id = video_jobs.start(video_id, {"document_title": "Split video"}, generate, on_finish=on_finish)
    return {"job_id": job_id, "parts": len(ranges)}


@router.post("/split_video/{video_id}")
async def split_project_video(video_id: str, request: Request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("Split settings must be an object.")
        source = _media_source(video_id, data)
        ranges = split_ranges(data.get("cut_points"), await video_duration(source))
    except (OSError, ValueError, TypeError) as error:
        raise HTTPException(400, f"Could not split video: {error}") from error
    return _start_split_download(video_id, source, ranges)


@router.post("/split_uploaded_video")
async def split_uploaded_video(file: UploadFile = File(...), cut_points: str = Form(...)):
    workspace = Path(tempfile.mkdtemp(prefix="lecture-split-upload-"))
    try:
        cuts = json.loads(cut_points)
        paths, _ = await _save_clip_uploads([file], workspace)
        ranges = split_ranges(cuts, await video_duration(paths[0]))
        return _start_split_download(uuid.uuid4().hex, paths[0], ranges,
                                     on_finish=lambda: cleanup_export_workspace(workspace))
    except BaseException as error:
        cleanup_export_workspace(workspace)
        if isinstance(error, (OSError, ValueError, TypeError)):
            raise HTTPException(400, f"Could not split uploaded video: {error}") from error
        raise
    finally:
        await file.close()


async def process_uploaded_clips(files, title, save_project, trim_ranges, minimum):
    if not minimum <= len(files) <= 30:
        raise HTTPException(400, f"Choose between {minimum} and 30 clips.")
    title = title.strip()
    if not title or len(title) > 150:
        raise HTTPException(400, "Give the output video a title of at most 150 characters.")
    try:
        trims = json.loads(trim_ranges) if trim_ranges else [{} for _ in files]
        if (not isinstance(trims, list) or len(trims) != len(files)
                or any(not isinstance(trim, dict) for trim in trims)):
            raise ValueError("Provide one trim range per clip, in upload order.")
        ranges = [(clip_timestamp(trim.get("start", "00:00")),
                   clip_timestamp(trim["end"]) if trim.get("end") not in ("", None) else None) for trim in trims]
    except (ValueError, TypeError) as error:
        raise HTTPException(400, f"Invalid trim ranges: {error}") from error
    workspace = Path(tempfile.mkdtemp(prefix="lecture-merge-upload-"))
    try:
        paths, names = await _save_clip_uploads(files, workspace)
        # Reject invalid media before creating a job; never publish partial projects.
        try:
            durations = [await video_duration(path) for path in paths]
        except (ValueError, OSError) as error:
            raise HTTPException(400, f"Could not read an uploaded video: {error}") from error
        if any(not math.isfinite(duration) or duration <= 0 for duration in durations):
            raise HTTPException(400, "Each clip must have a valid, positive duration.")
        ranges = [(start, duration if end is None else end) for (start, end), duration in zip(ranges, durations)]
        for index, ((start, end), duration) in enumerate(zip(ranges, durations)):
            if not 0 <= start < end <= duration:
                raise HTTPException(400, f"Clip {index + 1}: Start must be before End and both must be inside the clip.")
        merge_id = uuid.uuid4().hex
        project_id = merge_id if save_project else None

        async def generate(parent, options, step, artifacts, output):
            published = VIDEO_DIR / f"{project_id}.mp4" if project_id else None
            committed = False
            try:
                normalized = []
                for index, (path, (start, end)) in enumerate(zip(paths, ranges)):
                    duration = end - start
                    probe = await asyncio.to_thread(subprocess.run, [
                        media_executable("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(path),
                    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
                    if probe.returncode:
                        raise ValueError(f"Could not inspect clip {index + 1}: {probe.stderr[-1000:]}")
                    streams = json.loads(probe.stdout).get("streams", [])
                    if not any(stream.get("codec_type") == "video" for stream in streams):
                        raise ValueError(f"Clip {index + 1} has no video stream.")
                    audio = any(stream.get("codec_type") == "audio" for stream in streams)
                    target = output / f"normalized-{index:03d}.mp4"
                    step(f"Preparing clip {index + 1} of {len(paths)}: {names[index]}")
                    command = [media_executable("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
                               "-ss", str(start), "-i", str(path)]
                    if not audio:
                        command += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
                    command += ["-map", "0:v:0", "-map", "0:a:0" if audio else "1:a:0",
                                "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                                "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,setpts=PTS-STARTPTS",
                                "-af", "asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0,apad",
                                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                                "-c:a", "aac", "-ar", "48000", "-ac", "2", "-t", str(duration),
                                "-movflags", "+faststart", str(target)]
                    await encode_export_clip(command, step, f"Clip {index + 1}", duration)
                    normalized.append(target)
                if len(normalized) == 1:
                    merged = output / "Trimmed_clip.mp4"
                    normalized[0].replace(merged)
                else:
                    merged = output / "Merged_clips.mp4"
                    await concatenate_clips(normalized, merged, step)
                if published is not None:
                    step("Saving the merged video as a new independent project")
                    shutil.copy2(merged, published)
                    save_video_metadata(project_id, {
                        "filename": title + ".mp4", "duration": sum(end - start for start, end in ranges),
                        "upload_time": time.time(), "merged_clips": names,
                        "clip_ranges": [{"start": start, "end": end} for start, end in ranges],
                    })
                committed = True
                return FileResponse(merged)
            finally:
                if not committed and published is not None:
                    published.unlink(missing_ok=True)

        job_id = video_jobs.start(merge_id, {"document_title": title}, generate,
                                  on_finish=lambda: cleanup_export_workspace(workspace))
        return {"job_id": job_id, "project_id": project_id}
    except BaseException:
        cleanup_export_workspace(workspace)
        raise
    finally:
        for upload in files:
            await upload.close()


@router.get("/chapter_video_jobs/{job_id}")
def chapter_video_status(job_id: str):
    try:
        state = video_jobs.read(job_id)
        if state["status"] == "complete":
            state["download_url"] = f"/chapter_video_jobs/{job_id}/download"
        return state
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, "Chapter video not found.") from error


@router.post("/chapter_video_jobs/{job_id}/cancel")
def cancel_video_merge(job_id: str):
    try:
        return video_jobs.cancel(job_id)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, "Video job not found.") from error


@router.get("/chapter_video_jobs/{job_id}/download")
def download_chapter_video(job_id: str):
    state = chapter_video_status(job_id)
    if state["status"] != "complete":
        raise HTTPException(409, "Chapter video is not ready.")
    media_type = ("audio/wav" if state["filename"].lower().endswith(".wav") else
                  "application/zip" if state["filename"].lower().endswith(".zip") else "video/mp4")
    return FileResponse(video_jobs.file(job_id, state["download_file"]),
                        media_type=media_type,
                        filename=state["filename"])


@router.get("/chapter_captions/{video_id}")
def chapter_captions(video_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", video_id):
        raise HTTPException(400, "Invalid video ID.")
    metadata = _read(VIDEO_DIR / "metadata.json", {}).get(video_id, {})
    return {"tracks": [{**track, "url": f"/chapter_captions/{video_id}/{track['index']}"}
                       for track in metadata.get("caption_tracks", [])]}


@router.get("/chapter_captions/{video_id}/{index}")
def chapter_caption_file(video_id: str, index: int):
    tracks = chapter_captions(video_id)["tracks"]
    if not 0 <= index < len(tracks):
        raise HTTPException(404, "Caption track not found.")
    path = TRANSCRIPTS_DIR / f"{video_id}_caption_{index}.vtt"
    if not path.is_file():
        raise HTTPException(404, "Caption track is missing.")
    return FileResponse(path, media_type="text/vtt")


@router.get("/chapter_project_jobs/{job_id}")
def chapter_project_status(job_id: str):
    try:
        state = jobs.read(job_id)
        if state["status"] == "error" and state.get("instance") != jobs.instance:
            state["error"] = "Chapter project creation was interrupted by a server restart. Create it again."
        return state
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, "Chapter project job not found.") from error
