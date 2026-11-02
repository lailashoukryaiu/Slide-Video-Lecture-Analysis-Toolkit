import asyncio
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import chapter_projects
from export_jobs import ExportJobStore
from processors import transcript_versions


class ChapterProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ("VIDEO_DIR", "TRANSCRIPTS_DIR", "SUMMARIES_DIR", "SCENES_DIR",
                     "THUMBNAILS_DIR", "FULLSIZE_IMAGES_DIR"):
            directory = self.root / name
            directory.mkdir()
            setattr(self, name, directory)
            patch = mock.patch.object(chapter_projects, name, directory)
            patch.start()
            self.addCleanup(patch.stop)
            if hasattr(transcript_versions, name):
                patch = mock.patch.object(transcript_versions, name, directory)
                patch.start()
                self.addCleanup(patch.stop)
        patch = mock.patch.object(chapter_projects, "jobs", ExportJobStore(self.root / "jobs"))
        patch.start()
        self.addCleanup(patch.stop)
        self.chapters = [{"timestamp": "00:02", "title": "Chapter one"}, {"timestamp": "00:06", "title": "Chapter two"}]
        self.transcript = [
            {"start": 0, "duration": 1, "text": "Before."},
            {"start": 1, "duration": 3, "end": 4, "text": "Overlapping.", "speaker": "A",
             "words": [{"start": 1, "end": 2.5, "word": "Overlapping."}]},
            {"start": 4, "duration": 4, "text": "Ends later.", "speaker": "B"},
            {"start": 8, "duration": 2, "text": "After."},
        ]
        self.scenes = [
            {"time_seconds": 0, "timestamp": "00:00", "duration": 4, "title": "Carry-in",
             "yolo_detections": {"success": True, "detections": [{"ocr_text": "Saved OCR"}]}},
            {"time_seconds": 4, "timestamp": "00:04", "duration": 2, "title": "Next"},
            {"time_seconds": 6, "timestamp": "00:06", "duration": 4, "title": "Outside"},
        ]
        self.write(self.SUMMARIES_DIR / "parent.json", self.chapters)
        self.write(self.TRANSCRIPTS_DIR / "parent_whisper.json", self.transcript)
        self.write(self.TRANSCRIPTS_DIR / "parent_whisper_meta.json", {"model": "turbo", "speaker_names": {"A": "Alice"}})
        self.write(self.SCENES_DIR / "parent.json", self.scenes)
        (self.VIDEO_DIR / "parent.mp4").write_bytes(b"source video")
        for directory in (self.THUMBNAILS_DIR, self.FULLSIZE_IMAGES_DIR):
            (directory / "parent").mkdir()
            for index in range(3):
                Image.new("RGB", (16, 9), "white").save(directory / "parent" / f"{index}.jpg")
        self.data = {"chapter_index": 0, "timestamp": "00:02", "title": "Chapter one", "next_timestamp": "00:06"}
        self.commands = []

        async def encode(command, step, label, duration=None):
            self.commands.append(command)
            Path(command[-1]).write_bytes(b"encoded chapter")
        self.encode = encode
        self.real_extract = chapter_projects.extract_caption_tracks
        patch = mock.patch.object(chapter_projects, "extract_caption_tracks", new=mock.AsyncMock(return_value=[]))
        patch.start()
        self.addCleanup(patch.stop)
        app = FastAPI()
        app.include_router(chapter_projects.router)
        self.app = app

    @staticmethod
    def write(path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    @staticmethod
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    def create(self, data=None, duration=10):
        snapshot = chapter_projects._snapshot("parent", data or self.data)
        with mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=duration)), \
                mock.patch.object(chapter_projects, "encode_export_clip", side_effect=self.encode), \
                mock.patch.object(chapter_projects, "media_executable", return_value="ffmpeg"):
            return asyncio.run(chapter_projects.create_project(snapshot, "child", lambda _: None))

    def test_chapter_project_keeps_resource_links_with_relative_times(self):
        self.chapters[0]["resources"] = [
            {"title": "Docs", "url": "https://example.com", "start": 3.125, "end": 5.5}]
        self.write(self.SUMMARIES_DIR / "parent.json", self.chapters)
        self.create()
        saved = self.read(self.SUMMARIES_DIR / "child.json")
        self.assertEqual(saved[0]["timestamp"], "00:00:00.000")
        self.assertEqual(saved[0]["resources"][0]["start"], 1.125)
        self.assertEqual(saved[0]["resources"][0]["end"], 3.5)

    def arranged_selection(self):
        from clip_arrangement import reorder_section
        chapters = [
            {"timestamp": "00:00", "title": "First", "sections": [
                {"timestamp": "00:00", "title": "A", "point": "Alpha"},
                {"timestamp": "00:02", "title": "B", "point": "Beta", "flagged": True}]},
            {"timestamp": "00:06", "title": "Second", "sections": [
                {"timestamp": "00:06", "title": "C", "point": "Gamma"},
                {"timestamp": "00:08", "title": "D", "point": "Delta", "skipped": True}]},
        ]
        chapters = reorder_section(chapters, {
            "action": "reorder_section", "chapter": 1, "section": 1,
            "target_chapter": 0, "target_section": 0})
        self.write(self.SUMMARIES_DIR / "parent.json", chapters)
        selections = []
        for ci, chapter in enumerate(chapters):
            for si, part in enumerate(chapter["sections"]):
                following = (chapter["sections"][si + 1]["timestamp"] if si + 1 < len(chapter["sections"])
                             else chapters[ci + 1]["timestamp"] if ci + 1 < len(chapters) else None)
                selections.append({"chapter_index": ci, "section_index": si,
                                   "timestamp": part["timestamp"], "title": part["title"], "next_timestamp": following})
        return {"selections": selections, "arrangement": chapters[0]["clip_arrangement"]}

    def test_arranged_export_uses_saved_order_and_includes_skip_without_merging_backwards(self):
        data = self.arranged_selection()
        snapshots, _, arranged = chapter_projects.selection_snapshots("parent", data)
        ranges = chapter_projects._selected_ranges(snapshots, 10, True, preserve_order=arranged)
        self.assertEqual([(clip["start"], clip["end"]) for clip in ranges], [(8, 10), (0, 2), (2, 6), (6, 8)])
        self.assertTrue(ranges[0]["section_content"]["skipped"])
        data["arrangement"] = []
        with self.assertRaisesRegex(Exception, "Clip order changed"):
            chapter_projects.selection_snapshots("parent", data)

    def test_arranged_project_copies_and_retimes_outline_transcript_words_speakers_and_slides(self):
        data = self.arranged_selection()
        with mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=10)), \
                mock.patch.object(chapter_projects, "encode_export_clip", side_effect=self.encode), \
                mock.patch.object(chapter_projects, "media_executable", return_value="ffmpeg"), \
                mock.patch.object(chapter_projects, "remux_arranged_captions", new=mock.AsyncMock(
                    side_effect=lambda snapshot, clips, media, target, step: media.replace(target))):
            snapshot = asyncio.run(chapter_projects.prepare_arranged_project("parent", data))
            result = asyncio.run(chapter_projects.create_project(snapshot, "arranged", lambda _: None))
        self.assertEqual(result["duration"], 10)
        cues = self.read(self.TRANSCRIPTS_DIR / "arranged_whisper.json")
        self.assertEqual(cues[0]["text"], "After.")
        self.assertEqual(cues[0]["start"], 0)
        self.assertEqual(cues[1]["text"], "Before.")
        self.assertEqual(cues[1]["start"], 2)
        self.assertTrue(any(cue.get("speaker") == "A" and cue.get("words") for cue in cues))
        chapters = self.read(self.SUMMARIES_DIR / "arranged.json")
        self.assertEqual([part["title"] for chapter in chapters for part in chapter["sections"]], ["D", "A", "B", "C"])
        self.assertEqual(chapters[1]["timestamp"], "00:00:08.000")
        self.assertNotIn("clip_arrangement", chapters[0])
        scenes = self.read(self.SCENES_DIR / "arranged.json")
        self.assertEqual([scene["time_seconds"] for scene in scenes], [0, 2, 4, 6, 8])
        self.assertEqual(len({scene["thumbnail"] for scene in scenes}), 5)
        self.assertEqual(scenes[1]["yolo_detections"]["detections"][0]["ocr_text"], "Saved OCR")
        self.assertEqual(len(list((self.THUMBNAILS_DIR / "arranged").glob("*.jpg"))), 5)
        original = self.read(self.SUMMARIES_DIR / "parent.json")
        self.assertEqual(original[1]["sections"][1]["timestamp"], "00:08")

    def test_arranged_selected_project_route_and_chapter_project_use_displayed_clip_membership(self):
        data = self.arranged_selection()
        with TestClient(self.app) as client, \
                mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=10)), \
                mock.patch.object(chapter_projects, "start_project_job", return_value={"job_id": "arranged"}) as start:
            response = client.post("/selected_clip_projects/parent", json=data)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([clip["title"] for clip in start.call_args.args[0]["clips"]], ["D", "A", "B", "C"])
            response = client.post("/chapter_projects/parent", json={
                "chapter_index": 0, "timestamp": "00:00", "next_timestamp": "00:06",
                "title": "First", "arrangement": data["arrangement"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([clip["title"] for clip in start.call_args.args[0]["clips"]], ["D", "A", "B"])
            self.assertEqual(client.post("/selected_clip_projects/parent", json=[]).status_code, 400)

    def test_selecting_chapters_and_their_sections_does_not_duplicate_project_footage(self):
        data = self.arranged_selection()
        data["selections"].extend([
            {"chapter_index": 0, "timestamp": "00:00", "next_timestamp": "00:06", "title": "First"},
            {"chapter_index": 1, "timestamp": "00:06", "next_timestamp": None, "title": "Second"},
        ])
        with mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=10)):
            snapshot = asyncio.run(chapter_projects.prepare_arranged_project("parent", data))
            self.assertEqual([clip["title"] for clip in snapshot["clips"]], ["D", "A", "B", "C"])
            chapters = self.read(self.SUMMARIES_DIR / "parent.json")
            chapters[0].pop("clip_arrangement")
            self.write(self.SUMMARIES_DIR / "parent.json", chapters)
            with self.assertRaisesRegex(Exception, "Clip order changed"):
                asyncio.run(chapter_projects.prepare_arranged_project("parent", data))
            data.pop("arrangement")
            snapshot = asyncio.run(chapter_projects.prepare_arranged_project("parent", data))
            self.assertEqual([clip["title"] for clip in snapshot["clips"]], ["A", "B", "C", "D"])

    def test_real_arranged_project_encodes_video_in_reordered_sequence(self):
        from ffmpeg_tools import media_executable
        data = self.arranged_selection()
        source = self.VIDEO_DIR / "parent.mp4"
        captions = self.root / "arrangement.vtt"
        captions.write_text("WEBVTT\n\n00:00.200 --> 00:01.200\nA\n\n"
                            "00:02.200 --> 00:03.200\nB\n\n"
                            "00:06.200 --> 00:07.200\nC\n\n"
                            "00:08.200 --> 00:09.200\nD\n", encoding="utf-8")
        subprocess.run([
            media_executable("ffmpeg"), "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=red:size=128x72:rate=20:duration=6",
            "-f", "lavfi", "-i", "color=blue:size=128x72:rate=20:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=10", "-i", str(captions),
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a", "-map", "3:s",
            "-c:v", "libx264", "-c:a", "aac", "-c:s", "mov_text", str(source),
        ], check=True, capture_output=True)
        snapshot = asyncio.run(chapter_projects.prepare_arranged_project("parent", data))
        result = asyncio.run(chapter_projects.create_project(snapshot, "real-arranged", lambda _: None))
        output = self.VIDEO_DIR / "real-arranged.mp4"
        self.assertAlmostEqual(asyncio.run(chapter_projects.video_duration(output)), 10, delta=0.2)
        for stamp, expected in [(0.5, "blue"), (2.5, "red"), (8.5, "blue")]:
            pixel = subprocess.run([
                media_executable("ffmpeg"), "-v", "error", "-ss", str(stamp), "-i", str(output),
                "-vf", "scale=1:1", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ], check=True, capture_output=True).stdout
            self.assertEqual(len(pixel), 3)
            self.assertGreater(pixel[2] if expected == "blue" else pixel[0], 200)
        caption = subprocess.run([
            media_executable("ffmpeg"), "-v", "error", "-i", str(output), "-map", "0:s:0", "-f", "webvtt", "-",
        ], check=True, capture_output=True).stdout.decode()
        self.assertLess(caption.index("\nD"), caption.index("\nA"))
        self.assertLess(caption.index("\nA"), caption.index("\nB"))
        self.assertLess(caption.index("\nB"), caption.index("\nC"))
        for start, end, text in [
            ("00:00.200", "00:01.200", "D"), ("00:02.200", "00:03.200", "A"),
            ("00:04.200", "00:05.200", "B"), ("00:08.200", "00:09.200", "C"),
        ]:
            self.assertIn(f"{start} --> {end}\n{text}", caption)
        streams = subprocess.run([
            media_executable("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(output),
        ], check=True, capture_output=True).stdout
        self.assertTrue(any(stream["codec_type"] == "audio" for stream in json.loads(streams)["streams"]))
        self.assertEqual(result["transcript_segments"], len(self.read(self.TRANSCRIPTS_DIR / "real-arranged_whisper.json")))

    def test_speed_project_scales_all_saved_content_without_ai(self):
        self.chapters[0]["sections"] = [
            {"timestamp": "00:02", "title": "A", "point": "Keep A"},
            {"timestamp": "00:04", "title": "B", "point": "Keep B", "flagged": True}]
        self.write(self.SUMMARIES_DIR / "parent.json", self.chapters)
        snapshot = chapter_projects._snapshot("parent", {}, full_video=True)
        snapshot.update(start=3, end=8, speed=2)
        async def encode(snapshot, target, step):
            target.write_bytes(b"speed adjusted")
        with mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=10)), \
                mock.patch.object(chapter_projects, "encode_speed_video", side_effect=encode):
            result = asyncio.run(chapter_projects.create_project(snapshot, "fast", lambda _: None))
        self.assertEqual(result["duration"], 2.5)
        cues = self.read(self.TRANSCRIPTS_DIR / "fast_whisper.json")
        self.assertEqual(cues[0]["start"], 0)
        self.assertEqual(cues[0]["duration"], 0.5)
        self.assertEqual(cues[0]["words"], [], "Words outside a trimmed interval are excluded")
        self.assertEqual(cues[1]["start"], 0.5)
        self.assertEqual(cues[1]["duration"], 2)
        chapters = self.read(self.SUMMARIES_DIR / "fast.json")
        self.assertEqual([chapter_projects._seconds(c["timestamp"]) for c in chapters], [0, 1.5])
        self.assertEqual(chapters[0]["sections"][1]["timestamp"], "00:00:00.500")
        self.assertTrue(chapters[0]["sections"][1]["flagged"])
        scenes = self.read(self.SCENES_DIR / "fast.json")
        self.assertEqual([s["time_seconds"] for s in scenes], [0, 0.5, 1.5])
        self.assertEqual(scenes[-1]["duration"], 1)
        self.assertEqual(scenes[0]["yolo_detections"], self.scenes[0]["yolo_detections"])
        self.assertEqual(self.read(self.SUMMARIES_DIR / "parent.json"), self.chapters)

    def test_speed_timestamp_transform_preserves_words_and_caption_cues(self):
        cues = chapter_projects.scale_cues(chapter_projects.clip_transcript(self.transcript, 1, 8), 0.5)
        self.assertEqual(cues[0]["words"][0]["start"], 0)
        self.assertEqual(cues[0]["words"][0]["end"], 3)
        self.assertEqual(cues[0]["end"], 6)
        vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nFirst\n\n00:00:07.000 --> 00:00:09.000\nLast\n"
        transformed = chapter_projects.speed_vtt(vtt, 2, 8, 2)
        self.assertIn("00:00:00.000 --> 00:00:01.000", transformed)
        self.assertIn("00:00:02.500 --> 00:00:03.000", transformed)
        self.assertIn("First", transformed)
        self.assertIn("Last", transformed)

    def test_cancelled_speed_project_removes_incomplete_output(self):
        snapshot = chapter_projects._snapshot("parent", {}, full_video=True)
        snapshot.update(start=0, end=10, speed=2)
        async def cancelled(snapshot, target, step):
            target.write_bytes(b"incomplete")
            raise asyncio.CancelledError()
        with mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=10)), \
                mock.patch.object(chapter_projects, "encode_speed_video", side_effect=cancelled):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(chapter_projects.create_project(snapshot, "cancelled-speed", lambda _: None))
        self.assertFalse(list(self.VIDEO_DIR.glob("cancelled-speed.*")))
        self.assertFalse(list(self.SUMMARIES_DIR.glob("cancelled-speed*.json")))

    def test_speed_translation_project_keeps_selected_outline_and_scaled_translation(self):
        self.write(self.TRANSCRIPTS_DIR / "parent_translated_de.json", self.transcript)
        self.write(self.SUMMARIES_DIR / "parent_summary_en.json", self.chapters)
        snapshot = chapter_projects._snapshot("parent", {"language": "de", "chapter_language": "en"}, full_video=True)
        snapshot.update(start=2, end=6, speed=2, chapter_language="en")
        async def encode(snapshot, target, step):
            target.write_bytes(b"speed adjusted")
        with mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=10)), \
                mock.patch.object(chapter_projects, "encode_speed_video", side_effect=encode):
            asyncio.run(chapter_projects.create_project(snapshot, "translated-speed", lambda _: None))
        self.assertEqual(self.read(self.TRANSCRIPTS_DIR / "translated-speed_translated_de.json")[1]["start"], 1)
        self.assertEqual(self.read(self.SUMMARIES_DIR / "translated-speed_summary_en.json"),
                         self.read(self.SUMMARIES_DIR / "translated-speed.json"))

    def test_speed_route_validation_and_snapshot_pins_selected_source(self):
        with TestClient(self.app) as client, \
                mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=10)), \
                mock.patch.object(chapter_projects, "start_project_job", return_value={"job_id": "test"}) as start:
            for speed in (0, True, 5, "2"):
                self.assertEqual(client.post("/speed_video/parent", json={"speed": speed}).status_code, 400)
            for data in ({"speed": 2, "start": "00:08", "end": "00:02"},
                         {"speed": 2, "end": "00:20"}, {"speed": 2, "output": "bad"}):
                self.assertEqual(client.post("/speed_video/parent", json=data).status_code, 400)
            result = client.post("/speed_video/parent", json={"speed": 1.75, "output": "project", "start": "00:02"})
            self.assertEqual(result.status_code, 200, result.text)
            snapshot = start.call_args.args[0]
            self.assertEqual(snapshot["speed"], 1.75)
            self.assertEqual(snapshot["start"], 2)
            self.assertEqual(snapshot["end"], 10)
            self.assertEqual(snapshot["video_path"], self.VIDEO_DIR / "parent.mp4")
            self.assertEqual(result.json()["kind"], "project")

    def test_real_speed_export_retimes_audio_video_and_embedded_captions(self):
        from ffmpeg_tools import media_executable
        import array
        source = self.VIDEO_DIR / "speed-source.mp4"
        vtt = self.root / "speed.vtt"
        vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:05.000\nSpeed caption\n", encoding="utf-8")
        subprocess.run([media_executable("ffmpeg"), "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=128x72:rate=30:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-i", str(vtt),
            "-map", "0:v", "-map", "1:a", "-map", "2:0", "-c:v", "libx264",
            "-preset", "ultrafast", "-c:a", "aac", "-c:s", "mov_text", str(source)], check=True)
        for rate in (0.5, 2):
            target = self.root / f"speed-{rate}.mp4"
            asyncio.run(chapter_projects.encode_speed_video(
                {"video_path": source, "start": 2, "end": 6, "speed": rate}, target, lambda _: None))
            duration = asyncio.run(chapter_projects.video_duration(target))
            self.assertAlmostEqual(duration, 4 / rate, delta=0.15)
            caption = subprocess.run([media_executable("ffmpeg"), "-v", "error", "-i", str(target),
                "-map", "0:s:0", "-f", "webvtt", "-"], capture_output=True, check=True).stdout.decode()
            self.assertIn(f"00:00.000 --> {chapter_projects.precise_stamp(3 / rate)[3:]}", caption)
            raw = subprocess.run([media_executable("ffmpeg"), "-v", "error", "-i", str(target),
                "-map", "0:a:0", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                capture_output=True, check=True).stdout
            samples = array.array("h", raw)[1600:6400]
            crossings = sum(left <= 0 < right for left, right in zip(samples, samples[1:]))
            self.assertAlmostEqual(crossings / (len(samples) / 8000), 440, delta=8,
                                   msg="Changing tempo must not change voice pitch")
        silent = self.VIDEO_DIR / "silent.mp4"
        subprocess.run([media_executable("ffmpeg"), "-v", "error", "-y", "-i", str(source),
                        "-map", "0:v", "-c", "copy", str(silent)], check=True)
        asyncio.run(chapter_projects.encode_speed_video(
            {"video_path": silent, "start": 0, "end": 6, "speed": 4}, self.root / "silent-fast.mp4", lambda _: None))
        snapshot = chapter_projects._snapshot("parent", {}, full_video=True)
        snapshot.update(video_path=source, start=2, end=6, speed=2, title="Fast project")
        with mock.patch.object(chapter_projects, "extract_caption_tracks", new=self.real_extract):
            project = asyncio.run(chapter_projects.create_project(snapshot, "speed-project", lambda _: None))
        self.assertAlmostEqual(project["duration"], 2)
        self.assertTrue((self.VIDEO_DIR / "speed-project.mp4").is_file())
        self.assertIn("00:00.000 --> 00:01.500",
                      (self.TRANSCRIPTS_DIR / "speed-project_caption_0.vtt").read_text())
        self.assertEqual(self.read(self.SUMMARIES_DIR / "speed-project.json")[0]["timestamp"], "00:00:00.000")

    def test_project_is_independent_with_relative_transcript_slides_and_ocr(self):
        originals = {path: path.read_bytes() for directory in (
            self.TRANSCRIPTS_DIR, self.SUMMARIES_DIR, self.SCENES_DIR, self.VIDEO_DIR,
        ) for path in directory.iterdir() if path.is_file()}
        result = self.create()
        self.assertEqual(result["duration"], 4)
        self.assertEqual(result["slides"], 2)
        self.assertEqual(result["transcript_segments"], 2)
        cues = self.read(self.TRANSCRIPTS_DIR / "child_whisper.json")
        self.assertEqual([(item["start"], item["duration"]) for item in cues], [(0, 2), (2, 2)])
        self.assertEqual(cues[0]["end"], 2)
        self.assertEqual(cues[0]["speaker"], "A")
        self.assertEqual(cues[0]["words"][0]["start"], 0)
        self.assertEqual(cues[0]["words"][0]["end"], 0.5)
        slides = self.read(self.SCENES_DIR / "child.json")
        self.assertEqual([scene["time_seconds"] for scene in slides], [0, 2])
        self.assertEqual([scene["duration"] for scene in slides], [2, 2])
        self.assertEqual(slides[0]["yolo_detections"], self.scenes[0]["yolo_detections"])
        self.assertEqual(slides[1]["fullsize"], "/fullsize_images/child/1.jpg")
        self.assertTrue((self.FULLSIZE_IMAGES_DIR / "child" / "0.jpg").is_file())
        self.assertFalse((self.SUMMARIES_DIR / "child.json").exists(), "New project can generate fresh chapters")
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original, f"Parent content changed: {path}")
        metadata = self.read(self.VIDEO_DIR / "metadata.json")["child"]
        self.assertEqual(metadata["chapter_project"]["parent_video_id"], "parent")
        self.assertEqual(metadata["filename"], "Chapter one.mp4")
        self.assertTrue((self.VIDEO_DIR / "child.mp4").is_file())
        command = self.commands[0]
        self.assertEqual(command[command.index("-ss") + 1], "2.0")
        self.assertEqual(command[command.index("-t") + 1], "4.0")
        self.assertIn("0:a?", command)
        self.assertFalse((self.VIDEO_DIR / "child.pending").exists())

    def test_last_chapter_ends_at_video_duration(self):
        data = {"chapter_index": 1, "timestamp": "00:06", "title": "Chapter two", "next_timestamp": None}
        result = self.create(data)
        self.assertEqual(result["duration"], 4)
        slides = self.read(self.SCENES_DIR / "child.json")
        self.assertEqual([scene["title"] for scene in slides], ["Outside"])

    def test_sections_use_their_own_boundaries_for_projects_and_downloads(self):
        self.chapters[0]["sections"] = [
            {"timestamp": "00:02", "title": "First section"},
            {"timestamp": "00:04", "title": "Last section"},
        ]
        self.chapters[1]["sections"] = [{"timestamp": "00:06", "title": "Final section"}]
        self.write(self.SUMMARIES_DIR / "parent.json", self.chapters)
        for ci, si, title, start, following, end in (
            (0, 0, "First section", "00:02", "00:04", 4),
            (0, 1, "Last section", "00:04", "00:06", 6),
            (1, 0, "Final section", "00:06", None, None),
        ):
            data = {**self.data, "chapter_index": ci, "section_index": si,
                    "title": title, "timestamp": start, "next_timestamp": following}
            snapshot = chapter_projects._snapshot("parent", data)
            self.assertEqual(snapshot["end"], end)
            self.assertEqual(snapshot["title"], title)
        result = self.create({**self.data, "section_index": 0, "title": "First section", "next_timestamp": "00:04"})
        self.assertEqual(result["duration"], 2)
        self.assertEqual(self.commands[0][self.commands[0].index("-t") + 1], "2.0")
        with TestClient(self.app) as client:
            for si in (True, -1, "0"):
                response = client.post("/chapter_projects/parent", json={**self.data, "section_index": si})
                self.assertEqual(response.status_code, 400)
            for route in ("chapter_projects", "chapter_video"):
                for changes in ({"section_index": 9}, {"section_index": 0, "title": "Old section"},
                                {"section_index": 0, "title": "First section", "next_timestamp": "00:06"}):
                    self.assertEqual(client.post(f"/{route}/parent", json={**self.data, **changes}).status_code, 409)

    def test_previous_section_download_ends_at_edited_next_chapter_start(self):
        import copy
        from content_edits import _chapters
        chapters = [
            {"timestamp": "00:00", "title": "First", "sections": [
                {"timestamp": "00:00", "title": "A"}, {"timestamp": "00:02", "title": "B"}]},
            {"timestamp": "00:06", "title": "Second", "sections": [
                {"timestamp": "00:06", "title": "C"}, {"timestamp": "00:09", "title": "D"}]},
        ]
        for stamp, end in (("00:08", 8), ("00:04", 4)):
            edits = copy.deepcopy(chapters)
            edits[1]["timestamp"] = stamp
            saved = _chapters(chapters, edits)
            self.write(self.SUMMARIES_DIR / "parent.json", saved)
            snapshot = chapter_projects._snapshot("parent", {
                **self.data, "chapter_index": 0, "section_index": 1,
                "timestamp": "00:02", "title": "B", "next_timestamp": stamp,
            })
            self.assertEqual(snapshot["start"], 2)
            self.assertEqual(snapshot["end"], end)
            self.assertEqual(saved[1]["sections"][0]["timestamp"], stamp)

    def test_caption_copy_is_the_project_source_and_subtitle_streams_are_preserved(self):
        store = ExportJobStore(self.root / "exports" / "jobs")
        job = "b" * 32
        store._write(job, {"id": job, "video_id": "parent", "instance": store.instance, "status": "complete",
                          "created_at": 1, "title": "Captioned video - English",
                          "filename": "English.mp4", "download_file": "download.mp4"})
        captioned = store._directory(job) / "download.mp4"
        captioned.write_bytes(b"captioned copy")
        with mock.patch.object(chapter_projects, "EXPORTS_DIR", self.root / "exports"):
            snapshot = chapter_projects._snapshot("parent", {**self.data, "media_export_id": job})
            self.assertEqual(snapshot["video_path"], captioned)
            self.create({**self.data, "media_export_id": job})
        self.assertIn(str(captioned), self.commands[0])
        self.assertIn("0:s?", self.commands[0])
        self.assertIn("mov_text", self.commands[0])
        self.assertEqual(chapter_projects.chapter_captions("child")["tracks"], [])
        with mock.patch.object(chapter_projects, "EXPORTS_DIR", self.root / "exports"):
            state = store.read(job)
            state["video_id"] = "unrelated"
            store._write(job, state)
            with self.assertRaises(chapter_projects.HTTPException):
                chapter_projects._snapshot("parent", {**self.data, "media_export_id": job})

    def test_cancellation_stops_encoder_and_cleans_pending_video(self):
        async def exercise():
            started = asyncio.Event()
            async def encode(command, step, label, duration):
                Path(command[-1]).write_bytes(b"incomplete")
                started.set()
                await asyncio.Event().wait()
            class Request:
                async def json(inner):
                    return self.data
            with mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=10)), \
                    mock.patch.object(chapter_projects, "encode_export_clip", side_effect=encode), \
                    mock.patch.object(chapter_projects, "media_executable", return_value="ffmpeg"):
                response = await chapter_projects.start_chapter_project("parent", Request())
                await started.wait()
                job = response["job_id"]
                task = chapter_projects._active_jobs[job][0]
                self.assertEqual((await chapter_projects.cancel_chapter_project(job))["status"], "cancelling")
                await task
                self.assertEqual(chapter_projects.chapter_project_status(job)["status"], "cancelled")
                self.assertEqual(list(self.VIDEO_DIR.glob("*.pending")), [])
                self.assertTrue((self.VIDEO_DIR / "parent.mp4").exists())
        asyncio.run(exercise())

    def test_active_translation_and_original_language_both_survive(self):
        translated = [{**item, "text": "Translated " + item["text"]} for item in self.transcript]
        self.write(self.TRANSCRIPTS_DIR / "parent_translated_de.json", translated)
        self.write(self.SUMMARIES_DIR / "parent_summary_de.json", self.chapters)
        result = self.create({**self.data, "language": "de"})
        self.assertEqual(result["video_id"], "child")
        self.assertEqual(transcript_versions.get_active_source("child"), {"kind": "translation", "language": "de"})
        original = self.read(self.TRANSCRIPTS_DIR / "child_whisper.json")
        translation = self.read(self.TRANSCRIPTS_DIR / "child_translated_de.json")
        self.assertEqual(original[0]["text"], "Overlapping.")
        self.assertEqual(translation[0]["text"], "Translated Overlapping.")

    def test_video_without_transcript_or_slides_can_be_a_project(self):
        (self.TRANSCRIPTS_DIR / "parent_whisper.json").unlink()
        (self.SCENES_DIR / "parent.json").unlink()
        result = self.create()
        self.assertEqual((result["transcript_segments"], result["slides"]), (0, 0))
        self.assertTrue((self.VIDEO_DIR / "child.mp4").exists())

    def test_failure_cleans_partial_project_and_preserves_parent(self):
        (self.FULLSIZE_IMAGES_DIR / "parent" / "1.jpg").unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.create()
        self.assertFalse((self.VIDEO_DIR / "child.mp4").exists())
        self.assertFalse((self.VIDEO_DIR / "child.pending").exists())
        self.assertFalse((self.THUMBNAILS_DIR / "child").exists())
        self.assertTrue((self.VIDEO_DIR / "parent.mp4").exists())
        self.assertFalse((self.VIDEO_DIR / "metadata.json").exists())

    def test_invalid_or_stale_requests_rejected(self):
        with TestClient(self.app) as client:
            for data in ({**self.data, "chapter_index": True}, {**self.data, "language": []},
                         {**self.data, "source": "../etc"}, {**self.data, "chapter_index": -1},
                         *({**self.data, "caption_track_index": value} for value in (True, -1, 32, "1"))):
                self.assertEqual(client.post("/chapter_projects/parent", json=data).status_code, 400)
            for data in ({**self.data, "timestamp": "00:01"}, {**self.data, "title": "Old title"},
                         {**self.data, "next_timestamp": "00:07"}, {**self.data, "chapter_index": 9}):
                self.assertEqual(client.post("/chapter_projects/parent", json=data).status_code, 409)
            self.assertEqual(client.post("/chapter_projects/bad.id", json=self.data).status_code, 400)
            self.assertEqual(client.post("/chapter_projects/parent", content="{").status_code, 400)
            self.assertEqual(client.get("/chapter_project_jobs/bad").status_code, 404)

    def test_duration_outside_video_does_not_encode(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            self.create(duration=3)
        self.assertEqual(self.commands, [])

    def test_job_persists_result_and_can_be_read_after_restart(self):
        async def project(snapshot, project_id, step):
            return {"video_id": project_id, "filename": "Chapter.mp4", "slides": 0}
        with mock.patch.object(chapter_projects, "create_project", side_effect=project), TestClient(self.app) as client:
            response = client.post("/chapter_projects/parent", json=self.data)
            self.assertEqual(response.status_code, 200)
            job_id = response.json()["job_id"]
            for _ in range(20):
                state = client.get(f"/chapter_project_jobs/{job_id}").json()
                if state["status"] == "complete":
                    break
            self.assertEqual(state["status"], "complete")
            self.assertIn("project", state)
        restarted = ExportJobStore(chapter_projects.jobs.root)
        self.assertEqual(restarted.read(job_id)["project"], state["project"])

    def test_encoding_error_is_reported_by_job(self):
        with mock.patch.object(chapter_projects, "create_project", new=mock.AsyncMock(side_effect=RuntimeError("Encoder failed"))), \
                TestClient(self.app) as client:
            response = client.post("/chapter_projects/parent", json=self.data)
            job_id = response.json()["job_id"]
            for _ in range(20):
                state = client.get(f"/chapter_project_jobs/{job_id}").json()
                if state["status"] == "error":
                    break
            self.assertEqual(state["status"], "error")
            self.assertEqual(state["error"], "Encoder failed")

    def test_real_encoder_produces_precise_playable_chapter_with_audio(self):
        from ffmpeg_tools import media_executable
        subprocess.run([
            media_executable("ffmpeg"), "-y", "-v", "error", "-f", "lavfi",
            "-i", "color=c=blue:s=160x90:r=12:d=10", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=10", "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest", str(self.VIDEO_DIR / "parent.mp4"),
        ], check=True, capture_output=True)
        snapshot = chapter_projects._snapshot("parent", self.data)
        result = asyncio.run(chapter_projects.create_project(snapshot, "child", lambda _: None))
        self.assertEqual(result["duration"], 4)
        output = self.VIDEO_DIR / "child.mp4"
        self.assertAlmostEqual(asyncio.run(chapter_projects.video_duration(output)), 4, delta=0.12)
        probe = subprocess.run([
            media_executable("ffprobe"), "-v", "error", "-show_entries", "stream=codec_name",
            "-of", "json", str(output),
        ], check=True, capture_output=True, text=True)
        self.assertEqual({stream["codec_name"] for stream in json.loads(probe.stdout)["streams"]}, {"h264", "aac"})

    def test_real_nested_projects_keep_english_captions_and_browser_tracks(self):
        from ffmpeg_tools import media_executable
        source = self.VIDEO_DIR / "parent.mp4"
        plain = self.root / "plain.mp4"
        subtitles = self.root / "english.srt"
        subtitles.write_text("1\n00:00:01,000 --> 00:00:08,000\nEnglish captions stay visible.\n", encoding="utf-8")
        german = self.root / "german.srt"
        german.write_text("1\n00:00:01,000 --> 00:00:08,000\nDeutsche Untertitel.\n", encoding="utf-8")
        subprocess.run([media_executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi",
                        "-i", "color=c=blue:s=160x90:r=12:d=10", "-c:v", "libx264", str(plain)],
                       check=True, capture_output=True)
        subprocess.run([media_executable("ffmpeg"), "-v", "error", "-y", "-i", str(plain),
                        "-i", str(german), "-i", str(subtitles),
                        "-map", "0:v", "-map", "1:0", "-map", "2:0", "-c:v", "copy", "-c:s", "mov_text",
                        "-metadata:s:s:0", "language=deu", "-metadata:s:s:1", "language=eng", str(source)],
                       check=True, capture_output=True)
        with mock.patch.object(chapter_projects, "extract_caption_tracks", new=self.real_extract):
            snapshot = chapter_projects._snapshot("parent", {**self.data, "caption_track_index": 1})
            asyncio.run(chapter_projects.create_project(snapshot, "child", lambda _: None))
            tracks = chapter_projects.chapter_captions("child")["tracks"]
            self.assertEqual([track["language"] for track in tracks], ["deu", "eng"])
            self.assertEqual([track["default"] for track in tracks], [False, True])
            self.assertIn("English captions stay visible", (self.TRANSCRIPTS_DIR / "child_caption_1.vtt").read_text())
            self.write(self.SUMMARIES_DIR / "child.json", [{"timestamp": "00:01", "title": "Nested"}])
            nested = chapter_projects._snapshot("child", {
                "chapter_index": 0, "timestamp": "00:01", "title": "Nested", "next_timestamp": None,
            })
            asyncio.run(chapter_projects.create_project(nested, "grandchild", lambda _: None))
            tracks = chapter_projects.chapter_captions("grandchild")["tracks"]
            self.assertEqual([track["default"] for track in tracks], [False, True])
            self.assertEqual(tracks[1]["language"], "eng")
            text = (self.TRANSCRIPTS_DIR / "grandchild_caption_1.vtt").read_text()
            self.assertIn("English captions stay visible", text)
            self.assertIn("00:00.000", text)
            self.assertAlmostEqual(asyncio.run(chapter_projects.video_duration(self.VIDEO_DIR / "grandchild.mp4")), 3, delta=.15)

    def _assert_video_download(self):
        captured = {}
        def start(video_id, options, generate):
            captured["generate"] = generate
            return "d" * 32
        class Request:
            async def json(inner):
                return self.data
        with mock.patch.object(chapter_projects.video_jobs, "start", side_effect=start):
            result = asyncio.run(chapter_projects.start_chapter_video("parent", Request()))
        self.assertEqual(result["job_id"], "d" * 32)
        workspace = self.root / "download"
        workspace.mkdir()
        with mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=10)), \
                mock.patch.object(chapter_projects, "encode_export_clip", side_effect=self.encode), \
                mock.patch.object(chapter_projects, "media_executable", return_value="ffmpeg"):
            response = asyncio.run(captured["generate"]("parent", {}, lambda _: None, None, workspace))
        self.assertEqual(Path(response.path).suffix, ".mp4")
        self.assertEqual(Path(response.path).read_bytes(), b"encoded chapter")
        self.assertFalse((self.VIDEO_DIR / "metadata.json").exists(), "A download must not create another upload")

    def test_single_chapter_video_download_is_an_mp4_not_a_project(self):
        self._assert_video_download()

    def test_section_download_encodes_only_the_selected_section(self):
        self.chapters[0]["sections"] = [
            {"timestamp": "00:02", "title": "Section A"},
            {"timestamp": "00:04", "title": "Section B"},
        ]
        self.write(self.SUMMARIES_DIR / "parent.json", self.chapters)
        self.data = {**self.data, "section_index": 1, "timestamp": "00:04", "title": "Section B"}
        self._assert_video_download()
        command = self.commands[0]
        self.assertEqual(command[command.index("-ss") + 1], "4.0")
        self.assertEqual(command[command.index("-t") + 1], "2.0")

    def test_manual_ends_limit_chapter_and_section_exports_without_including_grey_gap(self):
        from content_edits import mutate_outline
        for section in (None, 0):
            with self.subTest(section=section):
                chapters = [{**self.chapters[0], "sections": [
                    {"timestamp": "00:02", "title": "Section A"}]}, self.chapters[1]]
                revised = mutate_outline(chapters, {"action": "update", "chapter": 0, "section": section,
                                                    "item": {"end_timestamp": "00:03.250"}}, duration=10)
                self.write(self.SUMMARIES_DIR / "parent.json", revised)
                selection = {**self.data, "next_timestamp": "00:03.250"}
                if section is not None:
                    selection.update(section_index=0, title="Section A")
                snapshot = chapter_projects._snapshot("parent", selection)
                ranges = chapter_projects._selected_ranges([snapshot], 10, True)
                self.assertEqual([(clip["start"], clip["end"]) for clip in ranges], [(2, 3.25)])
                gap = revised[1] if section is None else revised[0]["sections"][1]
                self.assertTrue(gap["flagged"])
                self.assertFalse(gap["skipped"], "A grey gap is not an automatic playback skip")

    def test_custom_clip_validates_times_without_requiring_chapters(self):
        (self.SUMMARIES_DIR / "parent.json").unlink()
        captured = {}
        def start(video_id, options, generate):
            captured["generate"] = generate
            return "d" * 32
        with mock.patch.object(chapter_projects.video_jobs, "start", side_effect=start), \
                mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=10)), \
                TestClient(self.app) as client:
            response = client.post("/custom_video/parent", json={
                "start": "00:01.250", "end": "00:03.750", "caption_track_index": 1,
            })
            self.assertEqual(response.status_code, 200, response.text)
            for data in (
                {"start": "00:03", "end": "00:01"}, {"start": "00:00", "end": "00:11"},
                {"start": "NaN", "end": "00:02"}, {"start": "00:60", "end": "00:02"},
                {"start": -1, "end": "00:02"}, {"start": "00:02", "end": "00:02"},
                {"start": "00:00", "end": "00:02", "caption_track_index": True},
            ):
                self.assertEqual(client.post("/custom_video/parent", json=data).status_code, 400)
            self.assertEqual(client.post("/custom_video/missing", json={"start": "00:00", "end": "00:02"}).status_code, 404)
        workspace = self.root / "custom-download"
        workspace.mkdir()
        with mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=10)), \
                mock.patch.object(chapter_projects, "encode_export_clip", side_effect=self.encode):
            result = asyncio.run(captured["generate"]("parent", {}, lambda _: None, None, workspace))
        command = self.commands[0]
        self.assertEqual(command[command.index("-ss") + 1], "1.25")
        self.assertEqual(command[command.index("-t") + 1], "2.5")
        self.assertIn("-disposition:s:1", command)
        self.assertEqual(Path(result.path).suffix, ".mp4")

    def test_selected_clips_zip_merges_touching_ranges_but_not_gaps(self):
        self.chapters[0]["sections"] = [
            {"timestamp": "00:02", "title": "Part A"}, {"timestamp": "00:04", "title": "Part B"}]
        self.write(self.SUMMARIES_DIR / "parent.json", self.chapters)
        selection = [
            {**self.data, "section_index": 0, "title": "Part A", "next_timestamp": "00:04"},
            {**self.data, "section_index": 1, "timestamp": "00:04", "title": "Part B"},
            {"chapter_index": 1, "timestamp": "00:06", "title": "Chapter two", "next_timestamp": None},
        ]
        captured = {}
        def start(video_id, options, generate):
            captured["generate"] = generate
            return "d" * 32
        with mock.patch.object(chapter_projects.video_jobs, "start", side_effect=start), \
                mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=10)), \
                TestClient(self.app) as client:
            for merge, selected, expected in (
                (False, selection, [(2, 2), (4, 2), (6, 4)]),
                (True, list(reversed(selection)), [(2, 8)]),
                (True, [selection[0], selection[2]], [(2, 2), (6, 4)]),
                (True, [self.data, selection[0]], [(2, 4)]),
            ):
                self.commands.clear()
                response = client.post("/selected_videos/parent", json={
                    "selections": selected, "merge_consecutive": merge,
                })
                self.assertEqual(response.status_code, 200, response.text)
                workspace = self.root / "batch-download"
                workspace.mkdir(exist_ok=True)
                with mock.patch.object(chapter_projects, "encode_export_clip", side_effect=self.encode):
                    result = asyncio.run(captured["generate"]("parent", {}, lambda _: None, None, workspace))
                encoded = [(float(cmd[cmd.index("-ss") + 1]), float(cmd[cmd.index("-t") + 1])) for cmd in self.commands]
                self.assertEqual(encoded, expected)
                with zipfile.ZipFile(result.path) as archive:
                    self.assertEqual(len(archive.namelist()), len(expected))
                    self.assertEqual(archive.namelist(), sorted(archive.namelist()))
                    self.assertTrue(all(archive.read(name) == b"encoded chapter" for name in archive.namelist()))
            for payload in ({"selections": []}, {"selections": [None]},
                            {"selections": selection, "merge_consecutive": "yes"}):
                self.assertEqual(client.post("/selected_videos/parent", json=payload).status_code, 400)
            stale = [{**selection[0], "title": "Stale"}]
            self.assertEqual(client.post("/selected_videos/parent", json={"selections": stale}).status_code, 409)

    def test_real_nonconsecutive_selection_is_one_mp4_without_the_gap(self):
        from ffmpeg_tools import media_executable
        subprocess.run([media_executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi",
                        "-i", "testsrc2=size=128x72:rate=15:duration=10", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=10", "-c:v", "libx264", "-c:a", "aac",
                        str(self.VIDEO_DIR / "parent.mp4")], check=True, capture_output=True)
        self.chapters[0]["sections"] = [{"timestamp": "00:02", "title": "Part A"},
                                        {"timestamp": "00:04", "title": "Part B"}]
        self.write(self.SUMMARIES_DIR / "parent.json", self.chapters)
        captured = {}
        def start(video_id, options, generate):
            captured["generate"] = generate
            return "a" * 32
        selections = [{**self.data, "section_index": 0, "title": "Part A", "next_timestamp": "00:04"},
                      {"chapter_index": 1, "timestamp": "00:06", "title": "Chapter two", "next_timestamp": None}]
        with mock.patch.object(chapter_projects.video_jobs, "start", side_effect=start), TestClient(self.app) as client:
            response = client.post("/selected_videos/parent", json={"selections": selections[::-1], "combine": True})
            self.assertEqual(response.status_code, 200, response.text)
        output = self.root / "combined"
        output.mkdir()
        result = asyncio.run(captured["generate"]("parent", {}, lambda _: None, None, output))
        self.assertEqual(Path(result.path).suffix, ".mp4")
        self.assertAlmostEqual(asyncio.run(chapter_projects.video_duration(Path(result.path))), 6, delta=.2)
        probe = subprocess.run([media_executable("ffprobe"), "-v", "error", "-show_streams", "-of", "json",
                                str(result.path)], capture_output=True, text=True, check=True)
        self.assertEqual({s["codec_type"] for s in json.loads(probe.stdout)["streams"]}, {"audio", "video"})

    def test_real_uploaded_merge_keeps_upload_order_handles_silent_portrait_and_saves_project(self):
        from ffmpeg_tools import media_executable
        paths = [self.root / "red.mp4", self.root / "blue.mp4"]
        for index, (color, size) in enumerate((("red", "160x90"), ("blue", "90x160"))):
            command = [media_executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi",
                       "-i", f"color=c={color}:size={size}:rate={24 if index else 15}:duration=0.6"]
            if not index:
                command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.6", "-c:a", "aac"]
            subprocess.run([*command, "-c:v", "libx264", str(paths[index])], check=True, capture_output=True)
        captured = {}
        def start(video_id, options, generate, **kwargs):
            captured.update(generate=generate, cleanup=kwargs["on_finish"])
            return "b" * 32
        with mock.patch.object(chapter_projects.video_jobs, "start", side_effect=start), TestClient(self.app) as client:
            response = client.post("/merge_uploaded_clips", data={"title": "My compilation", "save_project": "true"},
                                   files=[("files", (path.name, path.read_bytes(), "video/mp4")) for path in paths[::-1]])
            self.assertEqual(response.status_code, 200, response.text)
        output = self.root / "uploaded-merged"
        output.mkdir()
        try:
            result = asyncio.run(captured["generate"]("unused", {}, lambda _: None, None, output))
            published = self.VIDEO_DIR / f"{response.json()['project_id']}.mp4"
            self.assertEqual(published.read_bytes(), Path(result.path).read_bytes())
            self.assertAlmostEqual(asyncio.run(chapter_projects.video_duration(published)), 1.2, delta=.2)
            metadata = self.read(self.VIDEO_DIR / "metadata.json")[response.json()["project_id"]]
            self.assertEqual(metadata["merged_clips"], ["blue.mp4", "red.mp4"])
            self.assertEqual(metadata["filename"], "My compilation.mp4")
            for at, dominant in (("0.2", 2), ("0.9", 0)):
                frame = subprocess.run([media_executable("ffmpeg"), "-v", "error", "-ss", at,
                                        "-i", str(published), "-frames:v", "1", "-vf", "crop=2:2:640:360",
                                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
                self.assertGreater(frame[dominant], 200, "Actual frames must follow the requested upload order")
        finally:
            captured["cleanup"]()

    def test_real_uploaded_trim_merge_keeps_ranges_in_upload_order_with_silent_audio(self):
        from ffmpeg_tools import media_executable
        source = self.root / "colors.mp4"
        subprocess.run([
            media_executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi",
            "-i", "color=red:size=128x72:rate=30:duration=1", "-f", "lavfi",
            "-i", "color=blue:size=128x72:rate=30:duration=1", "-f", "lavfi",
            "-i", "color=lime:size=128x72:rate=30:duration=1",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", str(source),
        ], capture_output=True, check=True)
        captured = {}

        def start(video_id, options, generate, **kwargs):
            captured.update(generate=generate, cleanup=kwargs["on_finish"])
            return "e" * 32

        with mock.patch.object(chapter_projects.video_jobs, "start", side_effect=start), TestClient(self.app) as client:
            response = client.post("/merge_uploaded_clips", data={"trim_ranges": json.dumps([
                {"start": "00:02.100", "end": "00:02.500"},
                {"start": "00:00.100", "end": "00:00.500"},
            ])}, files=[("files", ("colors.mp4", source.read_bytes(), "video/mp4")) for _ in range(2)])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNone(response.json()["project_id"])
        output = self.root / "trim-merged"
        output.mkdir()
        try:
            result = asyncio.run(captured["generate"]("unused", {}, lambda _: None, None, output))
            self.assertAlmostEqual(asyncio.run(chapter_projects.video_duration(Path(result.path))), .8, delta=.15)
            for at, dominant in (("0.1", 1), ("0.6", 0)):
                frame = subprocess.run([
                    media_executable("ffmpeg"), "-v", "error", "-ss", at, "-i", str(result.path),
                    "-frames:v", "1", "-vf", "crop=2:2:640:360", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
                ], capture_output=True, check=True).stdout
                self.assertGreater(frame[dominant], 200)
            probe = subprocess.run([
                media_executable("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(result.path),
            ], capture_output=True, text=True, check=True)
            self.assertEqual({stream["codec_type"] for stream in json.loads(probe.stdout)["streams"]},
                             {"video", "audio"})
        finally:
            captured["cleanup"]()

    def test_compatible_audio_encodes_real_wav_and_reuses_completed_copy(self):
        import wave
        from ffmpeg_tools import media_executable
        source = self.VIDEO_DIR / "parent.mp4"
        subprocess.run([
            media_executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi",
            "-i", "color=blue:size=128x72:rate=15:duration=1", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-c:a", "aac", str(source),
        ], capture_output=True, check=True)
        original = source.read_bytes()
        captured = {}

        def start(video_id, options, generate):
            captured.update(generate=generate, title=options["document_title"])
            return "a" * 32

        with mock.patch.object(chapter_projects.video_jobs, "start", side_effect=start), \
                mock.patch.object(chapter_projects.video_jobs, "list", return_value=[]), TestClient(self.app) as client:
            info = client.post("/audio_info/parent", json={})
            self.assertEqual(info.status_code, 200, info.text)
            self.assertTrue(info.json()["has_audio"])
            self.assertEqual(info.json()["stream"]["codec_name"], "aac")
            response = client.post("/compatible_audio/parent", json={})
            self.assertEqual(response.status_code, 200, response.text)
        output = self.root / "wav-output"
        output.mkdir()
        result = asyncio.run(captured["generate"]("parent", {}, lambda _: None, None, output))
        with wave.open(str(result.path)) as audio:
            self.assertEqual(audio.getframerate(), 16000)
            self.assertEqual(audio.getnchannels(), 1)
            self.assertEqual(audio.getsampwidth(), 2)
            self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), 1, delta=.1)
            self.assertTrue(any(audio.readframes(audio.getnframes())))
        self.assertEqual(source.read_bytes(), original)
        with mock.patch.object(chapter_projects.video_jobs, "list", return_value=[{
            "id": "a" * 32, "title": captured["title"], "status": "complete", "download_file": "audio.wav",
        }]), mock.patch.object(chapter_projects.video_jobs, "file", return_value=Path(result.path)), \
                mock.patch.object(chapter_projects.video_jobs, "start") as start, TestClient(self.app) as client:
            self.assertEqual(client.post("/compatible_audio/parent", json={}).json()["job_id"], "a" * 32)
            start.assert_not_called()
        with mock.patch.object(chapter_projects.video_jobs, "read", return_value={
            "status": "complete", "filename": "Compatible_audio.wav", "download_file": "audio.wav",
        }), mock.patch.object(chapter_projects.video_jobs, "file", return_value=Path(result.path)), \
                TestClient(self.app) as client:
            download = client.get("/chapter_video_jobs/" + "a" * 32 + "/download",
                                  headers={"Range": "bytes=0-43"})
            self.assertEqual(download.status_code, 206)
            self.assertEqual(download.headers["content-type"], "audio/wav")
            self.assertEqual(download.content[:4], b"RIFF")

    def test_project_timestamp_split_needs_no_outline_and_zips_every_range(self):
        captured = {}

        def start(video_id, options, generate, **kwargs):
            captured["generate"] = generate
            return "f" * 32

        with mock.patch.object(chapter_projects.video_jobs, "start", side_effect=start), \
                mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=10)), \
                mock.patch.object(chapter_projects, "_read", side_effect=AssertionError("No transcript or outline needed")), \
                TestClient(self.app) as client:
            response = client.post("/split_video/parent", json={"cut_points": ["00:02.125", "00:06.500"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["parts"], 3)
            for cuts in ([], ["00:00"], ["00:06", "00:02"], ["00:10"], ["00:60"]):
                self.assertEqual(client.post("/split_video/parent", json={"cut_points": cuts}).status_code, 400)
        output = self.root / "split-download"
        output.mkdir()
        with mock.patch.object(chapter_projects, "encode_export_clip", side_effect=self.encode):
            result = asyncio.run(captured["generate"]("parent", {}, lambda _: None, None, output))
        self.assertEqual([(float(command[command.index("-ss") + 1]), float(command[command.index("-t") + 1]))
                          for command in self.commands], [(0, 2.125), (2.125, 4.375), (6.5, 3.5)])
        with zipfile.ZipFile(result.path) as archive:
            self.assertEqual(archive.namelist(), ["Part_001.mp4", "Part_002.mp4", "Part_003.mp4"])
            self.assertTrue(all(archive.read(name) == b"encoded chapter" for name in archive.namelist()))
        self.assertEqual((self.VIDEO_DIR / "parent.mp4").read_bytes(), b"source video")

    def test_loading_repeated_chapter_titles_preserves_section_indices_and_download_boundaries(self):
        from processors.summary_processor import SummaryProcessor
        from processors import summary_processor
        chapters = [
            {"timestamp": "00:00", "title": "Same topic", "sections": [
                {"timestamp": "00:00", "title": "Opening", "point": "First part."},
                {"timestamp": "00:02", "title": "Example", "point": "Example one."},
            ]},
            {"timestamp": "00:04", "title": "Same topic", "sections": [
                {"timestamp": "00:04", "title": "Opening", "point": "Second part."},
                {"timestamp": "00:06", "title": "Example", "point": "Example two."},
            ]},
        ]
        path = self.SUMMARIES_DIR / "parent_summary_en.json"
        self.write(path, chapters)
        original = path.read_bytes()
        with mock.patch.object(summary_processor, "SUMMARIES_DIR", self.SUMMARIES_DIR):
            response = asyncio.run(SummaryProcessor.__new__(SummaryProcessor).get_summary("parent", "en"))
        displayed = json.loads(response.body)["chapters"]
        self.assertEqual(displayed, chapters, "Loading must not merge chapters or change their indices")
        self.assertEqual(path.read_bytes(), original, "Reading must not rewrite the saved outline")
        for chapter_index, chapter in enumerate(displayed):
            for section_index, section in enumerate(chapter["sections"]):
                following = (chapter["sections"][section_index + 1]["timestamp"]
                             if section_index + 1 < len(chapter["sections"])
                             else displayed[chapter_index + 1]["timestamp"]
                             if chapter_index + 1 < len(displayed) else None)
                snapshot = chapter_projects._snapshot("parent", {
                    "chapter_index": chapter_index, "section_index": section_index,
                    "timestamp": section["timestamp"], "title": section["title"],
                    "next_timestamp": following, "chapter_language": "en",
                })
                self.assertEqual(snapshot["start"], chapter_projects._seconds(section["timestamp"]))
                self.assertEqual(snapshot["end"], chapter_projects._seconds(following) if following else None)

    def test_real_uploaded_split_zip_has_requested_colors_durations_audio_and_resolution(self):
        from ffmpeg_tools import media_executable
        source = self.root / "split-source.mp4"
        subprocess.run([
            media_executable("ffmpeg"), "-v", "error", "-y", "-f", "lavfi",
            "-i", "color=red:size=128x72:rate=30:duration=1", "-f", "lavfi",
            "-i", "color=blue:size=128x72:rate=30:duration=1", "-f", "lavfi",
            "-i", "color=lime:size=128x72:rate=30:duration=1", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=3", "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a",
            "-c:v", "libx264", "-c:a", "aac", str(source),
        ], capture_output=True, check=True)
        original = source.read_bytes()
        captured = {}

        def start(video_id, options, generate, **kwargs):
            captured.update(generate=generate, cleanup=kwargs["on_finish"])
            return "e" * 32

        with mock.patch.object(chapter_projects.video_jobs, "start", side_effect=start), TestClient(self.app) as client:
            response = client.post("/split_uploaded_video", data={"cut_points": '["00:01","00:02"]'},
                                   files={"file": ("source.mp4", original, "video/mp4")})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["parts"], 3)
        output = self.root / "real-split"
        output.mkdir()
        try:
            result = asyncio.run(captured["generate"]("unused", {}, lambda _: None, None, output))
            with zipfile.ZipFile(result.path) as archive:
                self.assertEqual(archive.namelist(), ["Part_001.mp4", "Part_002.mp4", "Part_003.mp4"])
                for index, name in enumerate(archive.namelist()):
                    clip = output / f"checked-{index}.mp4"
                    clip.write_bytes(archive.read(name))
                    self.assertAlmostEqual(asyncio.run(chapter_projects.video_duration(clip)), 1, delta=.1)
                    probe = subprocess.run([
                        media_executable("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(clip),
                    ], capture_output=True, text=True, check=True)
                    streams = json.loads(probe.stdout)["streams"]
                    self.assertEqual({stream["codec_type"] for stream in streams}, {"video", "audio"})
                    video = next(stream for stream in streams if stream["codec_type"] == "video")
                    self.assertEqual((video["width"], video["height"]), (128, 72))
                    frame = subprocess.run([
                        media_executable("ffmpeg"), "-v", "error", "-ss", "0.5", "-i", str(clip),
                        "-frames:v", "1", "-vf", "crop=2:2:64:36", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
                    ], capture_output=True, check=True).stdout
                    self.assertGreater(frame[(0, 2, 1)[index]], 200)
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse((self.VIDEO_DIR / "metadata.json").exists())
        finally:
            captured["cleanup"]()

    def test_invalid_uploaded_splits_are_rejected_and_temporary_files_cleaned(self):
        workspace = self.root / "invalid-split"
        workspace.mkdir()
        with mock.patch.object(chapter_projects.tempfile, "mkdtemp", return_value=str(workspace)), \
                mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=3)), \
                mock.patch.object(chapter_projects.video_jobs, "start") as start, TestClient(self.app) as client:
            response = client.post("/split_uploaded_video", data={"cut_points": '["00:03"]'},
                                   files={"file": ("source.mp4", b"video")})
            self.assertEqual(response.status_code, 400)
            self.assertFalse(workspace.exists())
            start.assert_not_called()

    def test_uploaded_video_only_is_default_and_never_copies_or_saves_a_project(self):
        captured = {}

        def start(video_id, options, generate, **kwargs):
            captured.update(generate=generate, cleanup=kwargs["on_finish"])
            return "c" * 32

        with mock.patch.object(chapter_projects.video_jobs, "start", side_effect=start), \
                mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=1)), \
                TestClient(self.app) as client:
            response = client.post("/merge_uploaded_clips", files=[
                ("files", ("a.mp4", b"clip-a")), ("files", ("b.mp4", b"clip-b"))])
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNone(response.json()["project_id"])
        output = self.root / "video-only"
        output.mkdir()
        probe = subprocess.CompletedProcess([], 0, json.dumps({"streams": [
            {"codec_type": "video"}, {"codec_type": "audio"}]}), "")
        steps = []
        try:
            with mock.patch.object(chapter_projects.subprocess, "run", return_value=probe), \
                    mock.patch.object(chapter_projects, "encode_export_clip", side_effect=self.encode), \
                    mock.patch.object(chapter_projects.shutil, "copy2") as copy_video, \
                    mock.patch.object(chapter_projects, "save_video_metadata") as save_metadata:
                result = asyncio.run(captured["generate"]("unused", {}, steps.append, None, output))
            self.assertTrue(Path(result.path).is_file())
            copy_video.assert_not_called()
            save_metadata.assert_not_called()
            self.assertFalse(any("project" in step for step in steps))
            self.assertFalse((self.VIDEO_DIR / "metadata.json").exists())
        finally:
            captured["cleanup"]()

    def test_real_single_uploaded_trim_keeps_only_requested_frames_audio_and_duration(self):
        from ffmpeg_tools import media_executable
        source = self.root / "three-colors.mp4"
        subprocess.run([media_executable("ffmpeg"), "-v", "error", "-y",
                        "-f", "lavfi", "-i", "color=c=red:size=160x90:rate=30:duration=1",
                        "-f", "lavfi", "-i", "color=c=blue:size=160x90:rate=30:duration=1",
                        "-f", "lavfi", "-i", "color=c=green:size=160x90:rate=30:duration=1",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
                        "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-c:a", "aac", str(source)],
                       check=True, capture_output=True)
        captured = {}
        def start(video_id, options, generate, **kwargs):
            captured.update(generate=generate, cleanup=kwargs["on_finish"])
            return "e" * 32
        with mock.patch.object(chapter_projects.video_jobs, "start", side_effect=start), TestClient(self.app) as client:
            response = client.post("/trim_uploaded_clips", data={
                "title": "Blue part", "save_project": "true",
                "trim_ranges": json.dumps([{"start": "00:01.200", "end": "00:01.700"}]),
            }, files=[("files", (source.name, source.read_bytes(), "video/mp4"))])
            self.assertEqual(response.status_code, 200, response.text)
        output = self.root / "trimmed"
        output.mkdir()
        try:
            result = asyncio.run(captured["generate"]("unused", {}, lambda _: None, None, output))
            target = Path(result.path)
            self.assertAlmostEqual(asyncio.run(chapter_projects.video_duration(target)), .5, delta=.1)
            for at in ("0.1", "0.4"):
                frame = subprocess.run([media_executable("ffmpeg"), "-v", "error", "-ss", at,
                                        "-i", str(target), "-frames:v", "1", "-vf", "crop=2:2:640:360",
                                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                       capture_output=True, check=True).stdout
                self.assertGreater(frame[2], 200, "Trim must exclude the red and green ends")
                self.assertLess(frame[0], 30)
            metadata = self.read(self.VIDEO_DIR / "metadata.json")[response.json()["project_id"]]
            self.assertEqual(metadata["duration"], .5)
            self.assertEqual(metadata["clip_ranges"], [{"start": 1.2, "end": 1.7}])
            probe = subprocess.run([media_executable("ffprobe"), "-v", "error", "-show_streams", "-of", "json",
                                    str(target)], capture_output=True, text=True, check=True)
            self.assertEqual({stream["codec_type"] for stream in json.loads(probe.stdout)["streams"]}, {"video", "audio"})
        finally:
            captured["cleanup"]()

    def test_uploaded_trim_validation_rejects_bad_times_counts_and_out_of_bounds(self):
        with mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=10)), \
                mock.patch.object(chapter_projects.video_jobs, "start") as start, TestClient(self.app) as client:
            for trims in ("bad", "{}", "[]", '[null]', '[{"start":true}]',
                          '[{"start":"00:60"}]', '[{"start":"00:02","end":"00:01"}]',
                          '[{"start":"00:00","end":"00:11"}]', '[{"start":"00:10"}]'):
                response = client.post("/trim_uploaded_clips", data={"trim_ranges": trims},
                                       files=[("files", ("video.mp4", b"mock media"))])
                self.assertEqual(response.status_code, 400, response.text)
            start.assert_not_called()

    def test_uploaded_merge_rejects_bad_media_and_cleans_temporary_inputs(self):
        with TestClient(self.app) as client:
            for files in (
                [("files", ("a.mp4", b"x", "video/mp4"))],
                [("files", ("a.exe", b"x")), ("files", ("b.mp4", b"x"))],
                [("files", ("a.mp4", b"")), ("files", ("b.mp4", b"x"))],
            ):
                self.assertEqual(client.post("/merge_uploaded_clips", files=files).status_code, 400)
            with mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(side_effect=ValueError("Unreadable"))), \
                    mock.patch.object(chapter_projects, "cleanup_export_workspace",
                                      wraps=chapter_projects.cleanup_export_workspace) as cleanup:
                response = client.post("/merge_uploaded_clips", files=[
                    ("files", ("a.mp4", b"invalid")), ("files", ("b.mp4", b"invalid"))])
                self.assertEqual(response.status_code, 400, response.text)
                self.assertFalse(cleanup.call_args.args[0].exists())

    def test_real_custom_clip_uses_caption_copy_and_precise_duration(self):
        from ffmpeg_tools import media_executable
        subtitles = self.root / "custom.srt"
        subtitles.write_text("1\n00:00:00,000 --> 00:00:03,000\nSelected English copy.\n", encoding="utf-8")
        store = ExportJobStore(self.root / "exports" / "jobs")
        job_id = "e" * 32
        store._write(job_id, {"id": job_id, "video_id": "parent", "status": "complete",
                             "instance": store.instance, "title": "Captioned video - English",
                             "filename": "English.mp4", "download_file": "download.mp4", "created_at": 1})
        copy = store._directory(job_id) / "download.mp4"
        subprocess.run([
            media_executable("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i",
            "color=c=blue:s=160x90:r=20:d=3", "-i", str(subtitles),
            "-map", "0:v", "-map", "1:0", "-c:v", "libx264", "-c:s", "mov_text",
            "-metadata:s:s:0", "language=eng", str(copy),
        ], check=True, capture_output=True)
        captured = {}
        def start(video_id, options, generate):
            captured["generate"] = generate
            return "d" * 32
        with mock.patch.object(chapter_projects, "EXPORTS_DIR", self.root / "exports"), \
                mock.patch.object(chapter_projects.video_jobs, "start", side_effect=start), \
                TestClient(self.app) as client:
            response = client.post("/custom_video/parent", json={
                "start": "00:00.500", "end": "00:02.000",
                "media_export_id": job_id, "caption_track_index": 0,
            })
            self.assertEqual(response.status_code, 200, response.text)
        workspace = self.root / "real-custom"
        workspace.mkdir()
        result = asyncio.run(captured["generate"]("parent", {}, lambda _: None, None, workspace))
        self.assertAlmostEqual(asyncio.run(chapter_projects.video_duration(Path(result.path))), 1.5, delta=.06)
        tracks = asyncio.run(self.real_extract(Path(result.path), "custom"))
        self.assertEqual(tracks[0]["language"], "eng")
        text = (self.TRANSCRIPTS_DIR / "custom_caption_0.vtt").read_text()
        self.assertIn("Selected English copy.", text)
        self.assertIn("00:00.000", text)
        self.assertFalse((self.VIDEO_DIR / "metadata.json").exists(), "Custom downloads are not new projects")


if __name__ == "__main__":
    unittest.main()
