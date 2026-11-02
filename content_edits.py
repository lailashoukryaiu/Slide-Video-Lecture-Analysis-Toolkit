"""Validated, conflict-checked edits to saved lecture content."""
import hashlib
import copy
import json
import math
import logging
import asyncio
import re
import threading

from fastapi import APIRouter, HTTPException, Request
from processors import transcript_versions
from caption_exports import video_duration
from project_paths import SCENES_DIR, SUMMARIES_DIR, TRANSCRIPTS_DIR, VIDEO_DIR
from clip_arrangement import reorder_section, section_arrangement
from resource_links import validate_resources


router = APIRouter()
_lock = threading.Lock()
logger = logging.getLogger(__name__)

def _review_fields(edit, old):
    fields = {}
    for key in ("flagged", "skipped"):
        value = edit.get(key, old.get(key, False))
        if type(value) is not bool:
            raise HTTPException(400, f"{key.capitalize()} must be true or false.")
        fields[key] = value
    fields["flag_reason"] = _text(edit.get("flag_reason", old.get("flag_reason", "")),
                                  "Review reason", blank=True, limit=1000)
    return fields


def _resource_fields(edit, old):
    if "resources" not in edit and "resources" not in old:
        return {}
    try:
        return {"resources": validate_resources(edit.get("resources", old.get("resources", [])))}
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


def _read(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default
    except (OSError, ValueError) as error:
        raise HTTPException(500, "Could not read saved lecture content.") from error


def _text(value, label, *, blank=False, limit=20000):
    if not isinstance(value, str) or len(value) > limit or (not blank and not value.strip()):
        raise HTTPException(400, f"{label} must be text{' (not empty)' if not blank else ''}, at most {limit} characters.")
    return value.strip()


def _seconds(timestamp):
    if not isinstance(timestamp, str) or not re.fullmatch(r"(?:\d+:)?\d+:[0-5]\d(?:\.\d{1,3})?", timestamp):
        raise HTTPException(400, "Use MM:SS or HH:MM:SS timestamps (optional milliseconds).")
    parts = timestamp.split(":")
    if len(parts) == 3 and int(parts[1]) >= 60:
        raise HTTPException(400, "Minutes in HH:MM:SS must be below 60.")
    return sum(float(part) * 60 ** index for index, part in enumerate(reversed(parts)))


def _revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _snapshot(video_id, kind, language, source, version_id, transcript_language=None):
    selected_language = language if transcript_language is None else transcript_language
    if not all(isinstance(value, str) for value in (video_id, kind, language, source, version_id)):
        raise HTTPException(400, "Content type, source, language and version must be strings.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", video_id):
        raise HTTPException(400, "Invalid video ID.")
    if kind not in {"chapters", "slides", "transcript"}:
        raise HTTPException(400, "Invalid content type.")
    if (language not in {"", "en", "de", "ar", "pl"} or not isinstance(selected_language, str)
            or selected_language not in {"", "en", "de", "ar", "pl"} or source not in {"whisper", "uploaded", "youtube"}):
        raise HTTPException(400, "Invalid transcript source or language.")
    if kind == "slides":
        scenes = _read(SCENES_DIR / f"{video_id}.json", [])
        _rows(scenes)
        return scenes, None
    entry = None
    if version_id:
        try:
            entry, transcript = transcript_versions.read_version(video_id, version_id)
        except (KeyError, FileNotFoundError) as error:
            raise HTTPException(409, "The selected transcript version no longer exists. Reload it.") from error
        except (OSError, ValueError) as error:
            raise HTTPException(500, "Could not read the selected transcript version.") from error
        if (entry.get("language") or "") != selected_language:
            raise HTTPException(409, "The selected transcript language changed. Reopen the editor.")
        if not selected_language and entry.get("source") != source:
            raise HTTPException(409, "The selected transcript source changed. Reopen the editor.")
        active_path = (TRANSCRIPTS_DIR / f"{video_id}_translated_{selected_language}.json" if selected_language
                       else TRANSCRIPTS_DIR / f"{video_id}_{source}.json")
        if _read(active_path) != transcript:
            raise HTTPException(409, "Another transcript version is now active. Reload it before editing.")
    else:
        path = (TRANSCRIPTS_DIR / f"{video_id}_translated_{selected_language}.json" if selected_language
                else TRANSCRIPTS_DIR / f"{video_id}_{source}.json")
        transcript = _read(path)
        if transcript is None and source == "youtube" and not selected_language:
            transcript = _read(TRANSCRIPTS_DIR / f"{video_id}.json", [])
    if not isinstance(transcript, list) or not transcript:
        raise HTTPException(409, "Load a saved transcript before editing this content.")
    _rows(transcript)
    if kind == "transcript":
        return transcript, entry
    path = SUMMARIES_DIR / (f"{video_id}_summary_{language}.json" if language else f"{video_id}.json")
    chapters = _read(path)
    if chapters is None and language:
        chapters = _read(SUMMARIES_DIR / f"{video_id}.json", [])
    if chapters is None:
        chapters = []
    if isinstance(chapters, list) and chapters and isinstance(chapters[0], list):
        if not all(isinstance(group, list) for group in chapters):
            raise HTTPException(500, "Saved chapters have an invalid format.")
        chapters = [chapter for group in chapters for chapter in group]
    _rows(chapters)
    for chapter in chapters:
        _rows(chapter.get("sections") or [])
    return chapters, (entry, transcript, path)


def _rows(value):
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise HTTPException(500, "Saved content has an invalid format.")


@router.get("/editable_content/{video_id}")
async def editable_content(video_id: str, kind: str, language: str = "", source: str = "whisper", version_id: str = "",
                     transcript_language: str | None = None):
    with _lock:
        content, context = _snapshot(video_id, kind, language, source, version_id, transcript_language)
    response = {"content": content, "revision": _revision(content)}
    if kind == "chapters":
        duration = await _outline_media_duration(video_id)
        response["duration"] = duration if duration is not None else _transcript_duration(context[1])
    return response


def _transcript_duration(transcript):
    return max(float(cue["start"]) + float(cue.get("duration", 0) or 0) for cue in transcript)


async def _outline_media_duration(video_id):
    if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", video_id):
        raise HTTPException(400, "Invalid video ID.")
    videos = [path for path in VIDEO_DIR.glob(f"{video_id}.*")
              if path.is_file() and path.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v")]
    if not videos:
        return None
    try:
        duration = await video_duration(videos[0])
    except (OSError, ValueError) as error:
        raise HTTPException(500, "Could not verify the video duration. No outline changes were saved.") from error
    if not math.isfinite(duration) or duration <= 0:
        raise HTTPException(500, "The saved video has an invalid duration. No outline changes were saved.")
    return round(duration, 3)


def _split_gap(edit, old, new_stamp):
    """Return the vacated start time when the user chose a new row for it, else None."""
    gap = edit.get("split_gap")
    if gap is None:
        return None
    if not isinstance(gap, str) or gap != old.get("timestamp") or _seconds(gap) >= _seconds(new_stamp):
        raise HTTPException(400, "A new row can only be created at the time a later start vacated.")
    return gap


def _chapters(original, edits):
    if not isinstance(edits, list) or len(edits) != len(original):
        raise HTTPException(400, "The chapter count must not change.")
    result = []
    for old, edit in zip(original, edits):
        if not isinstance(edit, dict):
            raise HTTPException(400, "Invalid chapter edit.")
        timestamp = _text(edit.get("timestamp"), "Chapter timestamp", limit=30)
        _seconds(timestamp)
        gap = _split_gap(edit, old, timestamp)
        if gap:
            result.append({"timestamp": gap, "title": "New chapter", "summary": "", "sections": [],
                           "user_edited": True, "timestamp_edited": True})
        chapter = {**old, "title": _text(edit.get("title"), "Chapter title", limit=500),
                   "timestamp": timestamp, "summary": _text(edit.get("summary", ""), "Chapter summary", blank=True),
                   "user_edited": True}
        chapter.update(_review_fields(edit, old))
        chapter.update(_resource_fields(edit, old))
        if timestamp != old.get("timestamp"):
            chapter["timestamp_edited"] = True
        sections = old.get("sections") or []
        changed = edit.get("sections", [])
        if not isinstance(changed, list) or len(changed) != len(sections):
            raise HTTPException(400, "The section count must not change.")
        chapter["sections"] = []
        for position, (section, change) in enumerate(zip(sections, changed)):
            if not isinstance(change, dict):
                raise HTTPException(400, "Invalid section edit.")
            stamp = _text(change.get("timestamp"), "Section timestamp", limit=30)
            _seconds(stamp)
            gap = _split_gap(change, section, stamp)
            if gap and position == 0:
                raise HTTPException(400, "Create a new chapter, not a section, before a chapter's first part.")
            if gap:
                chapter["sections"].append({"timestamp": gap, "title": "New section", "point": "",
                                            "user_edited": True, "timestamp_edited": True})
            chapter["sections"].append({
                **section, "timestamp": stamp, "title": _text(change.get("title"), "Section title", limit=500),
                **_review_fields(change, section),
                **_resource_fields(change, section),
                "point": _text(change.get("point", ""), "Section text", blank=True),
                **({"timestamp_edited": True} if stamp != section.get("timestamp") else {}),
            })
        if chapter["sections"] and timestamp != old.get("timestamp"):
            chapter["sections"][0]["timestamp"] = timestamp
            chapter["sections"][0]["timestamp_edited"] = True
        result.append(chapter)
    starts = [_seconds(chapter["timestamp"]) for chapter in result]
    if any(right <= left for left, right in zip(starts, starts[1:])):
        raise HTTPException(400, "Chapter timestamps must be in strictly increasing order.")
    for index, chapter in enumerate(result):
        times = [_seconds(section["timestamp"]) for section in chapter["sections"]]
        end = starts[index + 1] if index + 1 < len(starts) else float("inf")
        if any(time < starts[index] or time >= end for time in times) or any(
            right <= left for left, right in zip(times, times[1:])
        ):
            raise HTTPException(400, "Sections must be ordered and inside their chapter's time range.")
        if times and times[0] > starts[index]:
            gap = _unassigned_part(chapter["timestamp"])
            gap["flag_reason"] = "Unassigned time before the first section."
            chapter["sections"].insert(0, gap)
        if len(chapter["sections"]) > 500:
            raise HTTPException(400, "An outline can contain at most 500 sections per chapter.")
    return result


def _unassigned_part(timestamp, chapter=False):
    return {"timestamp": timestamp, "title": "Unassigned", "flagged": True,
            "flag_reason": "Unassigned time after a manually set end.", "skipped": False,
            "unassigned": True, "user_edited": True, "timestamp_edited": True,
            **({"summary": "", "sections": []} if chapter else {"point": ""})}


def _set_outline_end(chapters, index, part, timestamp, duration):
    chapter = chapters[index]
    rows = chapters if part is None else chapter["sections"]
    position = index if part is None else part
    target = rows[position]
    end = _seconds(timestamp)
    start = _seconds(target["timestamp"])
    following = rows[position + 1] if position + 1 < len(rows) else None
    gap = following if following and following.get("unassigned") is True else None
    limit_index = position + (2 if gap else 1)
    limit = (_seconds(rows[limit_index]["timestamp"]) if limit_index < len(rows)
             else _seconds(chapters[index + 1]["timestamp"]) if part is not None and index + 1 < len(chapters)
             else duration)
    if limit is None or not start < end <= limit:
        raise HTTPException(400, "End must be after Start and cannot pass the next assigned part or video end.")
    if following is not None and end == _seconds(following["timestamp"]):
        return
    if part is None:
        # Repartition children without losing their labels when a chapter end moves.
        sections = list(chapter.get("sections", []))
        if gap:
            sections.extend(section for section in gap.get("sections", []) if not section.get("unassigned"))
        chapter["sections"] = [section for section in sections if _seconds(section["timestamp"]) < end]
        remaining = [section for section in sections if _seconds(section["timestamp"]) >= end]
    if gap:
        rows.pop(position + 1)
    if end < limit:
        unassigned = _unassigned_part(timestamp, chapter=part is None)
        if part is None:
            unassigned["sections"] = remaining
            if remaining and _seconds(remaining[0]["timestamp"]) > end:
                unassigned["sections"].insert(0, _unassigned_part(timestamp))
        rows.insert(position + 1, unassigned)


def mutate_outline(original, data, duration=None):
    if original and original[0].get("clip_arrangement") and data.get("action") != "update":
        raise HTTPException(400, "Reset clip order before adding or deleting outline rows.")
    """Apply one conflict-checked row edit, retaining all unrelated saved metadata."""
    result = copy.deepcopy(original)
    action = data.get("action")
    index, part = data.get("chapter"), data.get("section")
    if action not in {"add_chapter", "add_section", "update", "delete"}:
        raise HTTPException(400, "Unknown outline action.")
    if type(index) is not int or not 0 <= index <= len(result):
        raise HTTPException(400, "Invalid chapter.")
    if action == "add_chapter":
        edit = data.get("item")
        if not isinstance(edit, dict):
            raise HTTPException(400, "Provide a chapter title and timestamp.")
        start = _seconds(edit.get("timestamp"))
        if index and start <= _seconds(result[index - 1]["timestamp"]):
            raise HTTPException(400, "The new chapter must start after the previous chapter.")
        if index < len(result) and start >= _seconds(result[index]["timestamp"]):
            raise HTTPException(400, "The new chapter must start before the next chapter.")
        sections = []
        if index:
            previous = result[index - 1]
            sections = [s for s in previous.get("sections", []) if _seconds(s["timestamp"]) >= start]
            previous["sections"] = [s for s in previous.get("sections", []) if _seconds(s["timestamp"]) < start]
        if not sections or _seconds(sections[0]["timestamp"]) != start:
            sections.insert(0, {"timestamp": edit["timestamp"], "title": edit.get("title"), "point": ""})
        item = {key: edit[key] for key in ("title", "timestamp", "summary", "flagged", "flag_reason", "skipped")
                if key in edit}
        result.insert(index, {**item, "sections": sections, "user_edited": True, "timestamp_edited": True})
    else:
        if index == len(result):
            raise HTTPException(400, "The chapter no longer exists.")
        chapter = result[index]
        sections = chapter.setdefault("sections", [])
        if action == "add_section":
            edit = data.get("item")
            if not isinstance(edit, dict):
                raise HTTPException(400, "Provide a section title and timestamp.")
            if type(part) is not int or not 0 <= part <= len(sections):
                raise HTTPException(400, "Invalid section insertion point.")
            sections.insert(part, {key: edit[key] for key in
                                  ("title", "timestamp", "point", "flagged", "flag_reason", "skipped") if key in edit})
            sections[part]["timestamp_edited"] = True
        else:
            if part is not None and (type(part) is not int or not 0 <= part < len(sections)):
                raise HTTPException(400, "The section no longer exists.")
            if action == "delete":
                if part is None:
                    result.pop(index)
                else:
                    sections.pop(part)
            else:
                edit = data.get("item")
                if not isinstance(edit, dict):
                    raise HTTPException(400, "Invalid row edit.")
                target = chapter if part is None else sections[part]
                previous_title = target.get("title")
                if "timestamp" in edit and edit["timestamp"] != target["timestamp"]:
                    target["timestamp_edited"] = True
                    if part is None and sections:
                        sections[0]["timestamp"] = edit["timestamp"]
                        sections[0]["timestamp_edited"] = True
                for key in ("title", "timestamp", "summary" if part is None else "point",
                            "flagged", "flag_reason", "skipped"):
                    if key in edit:
                        target[key] = edit[key]
                if target.get("unassigned") and (target.get("title") != previous_title or not target.get("flagged")):
                    target.pop("unassigned", None)
    if action != "delete" and "end_timestamp" in data.get("item", {}):
        _set_outline_end(result, index, None if action == "add_chapter" else part,
                         data["item"]["end_timestamp"], duration)
    if len(result) > 500 or any(len(chapter.get("sections", [])) > 500 for chapter in result):
        raise HTTPException(400, "An outline can contain at most 500 chapters and 500 sections per chapter.")
    # The structure has changed; validate the resulting shape, not the previous row counts.
    return _chapters(result, result)


def _unassigned_ranges(chapters, duration):
    for index, chapter in enumerate(chapters):
        end = _seconds(chapters[index + 1]["timestamp"]) if index + 1 < len(chapters) else duration
        if chapter.get("unassigned"):
            yield chapter, _seconds(chapter["timestamp"]), end, "summary"
        sections = chapter.get("sections", [])
        for part, section in enumerate(sections):
            if section.get("unassigned"):
                finish = _seconds(sections[part + 1]["timestamp"]) if part + 1 < len(sections) else end
                yield section, _seconds(section["timestamp"]), finish, "point"


async def describe_unassigned_parts(original, content, transcript, duration, language, processor):
    existing = {(start, end, field) for _, start, end, field in _unassigned_ranges(original, duration)}
    warnings = []
    for item, start, end, field in _unassigned_ranges(content, duration):
        if (start, end, field) in existing:
            continue
        # Use cue starts for half-open ranges; a cue straddling a cut cannot reliably be split into words.
        excerpt = [cue for cue in transcript if start <= float(cue["start"]) < end
                   and isinstance(cue.get("text"), str) and cue["text"].strip()]
        if not excerpt:
            item.update(title="No transcript text", **{field: "No transcript text is available in this separated time range."})
            continue
        try:
            if processor is None:
                raise RuntimeError("The summary processor is not configured.")
            description = await asyncio.wait_for(processor.summarize_outline_part(excerpt, language), timeout=45)
        except (RuntimeError, ValueError, asyncio.TimeoutError) as error:
            logger.warning("Could not describe separated outline range %s-%s: %s", start, end, error)
            warnings.append(f"Saved the separated part at {item['timestamp']}, but its title and summary could not "
                            "be generated. It remains grey and editable.")
            continue
        item.update(title=description["title"], **{field: description["summary"]})
    return warnings


@router.post("/outline_item/{video_id}")
async def save_outline_item(video_id: str, request: Request):
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(400, "Outline changes must be an object.")
    language = data.get("language", "")
    selected = data.get("transcript_language", language)
    source = data.get("source", "whisper")
    media_duration = await _outline_media_duration(video_id)
    with _lock:
        original, context = _snapshot(video_id, "chapters", language, source, data.get("version_id", ""), selected)
        if data.get("revision") != _revision(original):
            raise HTTPException(409, "Chapters changed while editing. Reopen this row to keep the newer changes.")
        transcript = context[1]
        end = media_duration if media_duration is not None else _transcript_duration(transcript)
        content = mutate_outline(original, data, end)
        if any(_seconds(item["timestamp"]) >= end for chapter in content
               for item in [chapter, *chapter.get("sections", [])]):
            raise HTTPException(400, "Chapter and section starts must be inside the saved video or transcript duration.")
        transcript_revision = _revision(transcript)
    warnings = await describe_unassigned_parts(
        original, content, transcript, end, language or selected, getattr(request.app.state, "summary_processor", None),
    )
    with _lock:
        latest, context = _snapshot(video_id, "chapters", language, source, data.get("version_id", ""), selected)
        if _revision(latest) != data.get("revision") or _revision(context[1]) != transcript_revision:
            raise HTTPException(409, "Chapters or transcript changed while describing the separated part. "
                                "Reopen this row to keep the newer changes.")
        try:
            version = persist_chapters(video_id, content, context, language, selected, source)
        except OSError as error:
            raise HTTPException(500, "Could not save the outline. Your previous content was kept.") from error
        return {"content": content, "revision": _revision(content), "version": version, "warnings": warnings}


def outline_operations(chapters, operations):
    """Apply chronological grouping changes without dropping saved content or metadata."""
    result = copy.deepcopy(chapters)
    if not isinstance(operations, list) or len(operations) > 500:
        raise HTTPException(400, "Invalid outline operations.")
    for operation in operations:
        if not isinstance(operation, dict):
            raise HTTPException(400, "Invalid outline operation.")
        action, index = operation.get("action"), operation.get("chapter")
        if action in {"reorder_section", "reset_clip_order"}:
            result = reorder_section(result, operation)
            continue
        if result and result[0].get("clip_arrangement"):
            raise HTTPException(400, "Reset clip order before merging or changing chapter grouping.")
        if type(index) is not int or not 0 <= index < len(result):
            raise HTTPException(400, "The chapter no longer exists.")
        chapter = result[index]
        sections = chapter.setdefault("sections", [])
        if action == "merge_chapter":
            if index + 1 >= len(result):
                raise HTTPException(400, "Choose two adjacent chapters.")
            following = result.pop(index + 1)
            for item in (chapter, following):
                if not item.get("sections"):
                    item["sections"] = [{"timestamp": item["timestamp"], "title": item["title"],
                                         "point": item.get("summary", "")}]
            chapter["sections"] += following["sections"]
            chapter["summary"] = "\n\n".join(filter(None, [chapter.get("summary"), following.get("summary")]))
            if "resources" in chapter or "resources" in following:
                chapter["resources"] = chapter.get("resources", []) + following.get("resources", [])
        else:
            part = operation.get("section")
            if type(part) is not int or not 0 <= part < len(sections):
                raise HTTPException(400, "The part no longer exists.")
            if action == "merge_section":
                if part + 1 >= len(sections):
                    if index + 1 >= len(result) or not result[index + 1].get("sections"):
                        raise HTTPException(400, "Choose two adjacent sections.")
                    next_chapter = result[index + 1]
                    following = next_chapter["sections"].pop(0)
                    if next_chapter["sections"]:
                        next_chapter["timestamp"] = next_chapter["sections"][0]["timestamp"]
                        next_chapter["timestamp_edited"] = True
                    else:
                        if next_chapter.get("resources"):
                            following["resources"] = following.get("resources", []) + next_chapter["resources"]
                        result.pop(index + 1)
                else:
                    following = sections.pop(part + 1)
                sections[part]["point"] = "\n\n".join(filter(None, [
                    sections[part].get("point"), following["title"], following.get("point"),
                ]))
                if "resources" in sections[part] or "resources" in following:
                    sections[part]["resources"] = sections[part].get("resources", []) + following.get("resources", [])
            elif action == "promote":
                selected = sections[part]
                if _seconds(selected["timestamp"]) == _seconds(chapter["timestamp"]):
                    raise HTTPException(400, "This part already starts its chapter. Edit the chapter title instead.")
                result.insert(index + 1, {
                    "timestamp": selected["timestamp"], "title": selected["title"], "summary": "",
                    "sections": sections[part:], "user_edited": True, "timestamp_edited": True,
                })
                chapter["sections"] = sections[:part]
            elif action == "move_section":
                target = operation.get("target")
                if type(target) is not int or abs(target - index) != 1 or not 0 <= target < len(result):
                    raise HTTPException(400, "Move a boundary part only to an adjacent chapter.")
                if len(sections) < 2:
                    raise HTTPException(400, "Use Merge chapters to move the last remaining part.")
                destination = result[target]
                if not destination.get("sections"):
                    destination["sections"] = [{"timestamp": destination["timestamp"],
                                                 "title": destination["title"], "point": destination.get("summary", "")}]
                if target < index and part == 0:
                    destination["sections"].append(sections.pop(0))
                    chapter["timestamp"] = sections[0]["timestamp"]
                    chapter["timestamp_edited"] = True
                elif target > index and part == len(sections) - 1:
                    moved = sections.pop()
                    destination["sections"].insert(0, moved)
                    destination["timestamp"] = moved["timestamp"]
                    destination["timestamp_edited"] = True
                else:
                    raise HTTPException(400, "Only the first part can move back, or the last part forward, to preserve video order.")
                destination["user_edited"] = True
            else:
                raise HTTPException(400, "Unknown outline operation.")
        chapter["user_edited"] = True
    return result


def _merge_selected_chapters(original, selections, merged_groups):
    indexes = set()
    for selection in selections:
        chapter = selection.get("chapter_index") if isinstance(selection, dict) else None
        if type(chapter) is not int or not 0 <= chapter < len(original):
            raise HTTPException(400, "A selected chapter no longer exists. Reload chapters and try again.")
        if chapter in indexes:
            raise HTTPException(400, "A chapter can only be selected once.")
        indexes.add(chapter)
    groups = []
    for chapter in sorted(indexes):
        if groups and chapter == groups[-1][-1] + 1:
            groups[-1].append(chapter)
        else:
            groups.append([chapter])
    groups = [group for group in groups if len(group) > 1]
    if not groups:
        raise HTTPException(400, "Merge requires at least two adjacent chapters.")
    result = copy.deepcopy(original)
    for group in reversed(groups):
        parts = [copy.deepcopy(original[index]) for index in group]
        merged = result[group[0]]
        sections = []
        for part in parts:
            sections += part.get("sections") or [{"timestamp": part["timestamp"], "title": part["title"],
                                                  "point": part.get("summary", "")}]
        merged["sections"] = sections
        merged["summary"] = "\n\n".join(filter(None, (part.get("summary") for part in parts)))
        if any("resources" in part for part in parts):
            merged["resources"] = [link for part in parts for link in part.get("resources", [])]
        merged["user_edited"] = True
        del result[group[0] + 1:group[-1] + 1]
        if merged_groups is not None:
            merged_groups.append((merged, parts, "chapter"))
    validated = _chapters(result, result)
    if merged_groups is not None:
        for index, (merged, parts, kind) in enumerate(merged_groups):
            target = next(chapter for chapter in validated
                          if _seconds(chapter["timestamp"]) == _seconds(merged["timestamp"]))
            merged_groups[index] = (target, parts, kind)
    return validated


def mutate_selected_sections(original, action, selections, merged_groups=None):
    if original and original[0].get("clip_arrangement"):
        raise HTTPException(400, "Reset clip order before merging or deleting source sections.")
    if action not in {"merge", "delete"} or not isinstance(selections, list) or not 1 <= len(selections) <= 500:
        raise HTTPException(400, "Choose between 1 and 500 sections and a valid action.")
    chapter_rows = [isinstance(item, dict) and item.get("section_index") is None for item in selections]
    if any(chapter_rows):
        if not all(chapter_rows):
            raise HTTPException(400, "Select either chapters or sections to merge, not both.")
        if action != "merge":
            raise HTTPException(400, "Select sections only, not chapter rows.")
        return _merge_selected_chapters(original, selections, merged_groups)
    selected = set()
    for selection in selections:
        if not isinstance(selection, dict):
            raise HTTPException(400, "Invalid section selection.")
        chapter, section = selection.get("chapter_index"), selection.get("section_index")
        if (type(chapter) is not int or type(section) is not int or not 0 <= chapter < len(original)
                or not 0 <= section < len(original[chapter].get("sections", []))):
            raise HTTPException(400, "Select sections only, not chapter rows.")
        if (chapter, section) in selected:
            raise HTTPException(400, "A section can only be selected once.")
        selected.add((chapter, section))
    ordered = sorted(selected)
    result = copy.deepcopy(original)
    if action == "delete":
        for chapter, section in reversed(ordered):
            result[chapter]["sections"].pop(section)
            result[chapter]["user_edited"] = True
    else:
        positions = {}
        position = 0
        for chapter, item in enumerate(original):
            for section in range(len(item.get("sections", []))):
                positions[(chapter, section)] = position
                position += 1
            if not item.get("sections"):
                position += 1
        groups = []
        for chapter, section in ordered:
            if groups and positions[(chapter, section)] == positions[groups[-1][-1]] + 1:
                groups[-1].append((chapter, section))
            else:
                groups.append([(chapter, section)])
        groups = [group for group in groups if len(group) > 1]
        if not groups:
            raise HTTPException(400, "Merge requires at least two adjacent sections.")
        consumed_chapters = set()
        for group in reversed(groups):
            chapter, first = group[0]
            sections = result[chapter]["sections"]
            parts = [result[ci]["sections"][si] for ci, si in group]
            merged = copy.deepcopy(parts[0])
            merged["point"] = "\n\n".join(filter(None, [
                parts[0].get("point", ""),
                *("\n".join(filter(None, [part["title"], part.get("point", "")])) for part in parts[1:]),
            ]))
            merged["flagged"] = any(part.get("flagged") for part in parts)
            if any("resources" in part for part in parts):
                merged["resources"] = [link for part in parts for link in part.get("resources", [])]
            merged["skipped"] = all(part.get("skipped") for part in parts)
            merged["flag_reason"] = "\n".join(dict.fromkeys(
                part["flag_reason"] for part in parts if part.get("flag_reason")))[:1000]
            if not all(part.get("unassigned") for part in parts):
                merged.pop("unassigned", None)
            for ci, si in reversed(group[1:]):
                result[ci]["sections"].pop(si)
                result[ci]["user_edited"] = True
                if ci != chapter:
                    consumed_chapters.add(ci)
                    if not result[ci]["sections"] and result[ci].get("resources"):
                        merged.setdefault("resources", []).extend(result[ci]["resources"])
            sections[first] = merged
            if merged_groups is not None:
                merged_groups.append((merged, parts, "section"))
            result[chapter]["user_edited"] = True
        for ci in sorted(consumed_chapters, reverse=True):
            if result[ci]["sections"]:
                result[ci]["timestamp"] = result[ci]["sections"][0]["timestamp"]
                result[ci]["timestamp_edited"] = True
            else:
                result.pop(ci)
    validated = _chapters(result, result)
    if merged_groups is not None:
        for index, (merged, parts, kind) in enumerate(merged_groups):
            target = next(section for chapter in validated for section in chapter.get("sections", [])
                          if section["timestamp"] == merged["timestamp"])
            merged_groups[index] = (target, parts, kind)
    return validated


@router.post("/outline_selection/{video_id}")
async def save_outline_selection(video_id: str, request: Request):
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(400, "Section changes must be an object.")
    language = data.get("language", "")
    selected = data.get("transcript_language", language)
    source = data.get("source", "whisper")
    with _lock:
        original, context = _snapshot(video_id, "chapters", language, source, data.get("version_id", ""), selected)
        if data.get("revision") != _revision(original):
            raise HTTPException(409, "Chapters changed. Reload them before changing selected sections.")
        merged_groups = []
        content = mutate_selected_sections(original, data.get("action"), data.get("selections"), merged_groups)
        transcript_revision = _revision(context[1])
    processor = getattr(request.app.state, "summary_processor", None)
    for merged, parts, kind in merged_groups:
        if processor is None:
            raise HTTPException(503, f"Merged {kind} descriptions are unavailable. Nothing was changed.")
        detail = "summary" if kind == "chapter" else "point"
        material = [{"text": f"{kind.title()} {index + 1}: {part['title']}\n{part.get(detail, '')}"}
                    for index, part in enumerate(parts)]
        try:
            description = await asyncio.wait_for(
                processor.summarize_outline_part(material, language or selected, combined=True), timeout=45)
        except (RuntimeError, ValueError, asyncio.TimeoutError) as error:
            logger.warning("Could not describe merged %ss: %s", kind, error)
            raise HTTPException(502, f"Could not generate the merged title and summary. "
                                f"No {kind}s were changed. Please retry.") from error
        merged.update({"title": description["title"], detail: description["summary"]})
    with _lock:
        latest, context = _snapshot(video_id, "chapters", language, source, data.get("version_id", ""), selected)
        if _revision(latest) != data.get("revision") or _revision(context[1]) != transcript_revision:
            raise HTTPException(409, "Chapters or transcript changed while describing merged sections. "
                                "Reload chapters before merging again.")
        try:
            version = persist_chapters(video_id, content, context, language, selected, source)
        except OSError as error:
            raise HTTPException(500, "Could not save the selected section changes. Previous content was kept.") from error
        return {"content": content, "revision": _revision(content), "version": version}


def persist_chapters(video_id, content, context, language, selected_language, source):
    section_arrangement(content)
    entry, transcript, path = context
    version = transcript_versions.save_version(
        video_id, "translation" if selected_language else "transcript", transcript,
        source=(entry or {}).get("source") or ("uploaded" if source == "uploaded" else "whisper"),
        language=selected_language or None, chapters=content,
    ) if language == selected_language else entry
    transcript_versions._write_json(path, content)
    return version


@router.post("/editable_content/{video_id}")
async def save_content_edits(video_id: str, request: Request):
    try:
        data = await request.json()
    except ValueError as error:
        raise HTTPException(400, "Edits must be valid JSON.") from error
    if not isinstance(data, dict):
        raise HTTPException(400, "Edits must be an object.")
    kind, language = data.get("kind"), data.get("language", "")
    source, version_id = data.get("source", "whisper"), data.get("version_id", "")
    media_duration = await _outline_media_duration(video_id) if kind == "chapters" else None
    with _lock:
        selected_language = data.get("transcript_language", language)
        original, context = _snapshot(video_id, kind, language, source, version_id, selected_language)
        if data.get("revision") != _revision(original):
            raise HTTPException(409, "This content changed since editing started. Reopen the editor to avoid overwriting changes.")
        edits = data.get("edits")
        if not isinstance(original, list) or not original:
            raise HTTPException(400, "There is no saved content to edit.")
        try:
            if kind == "slides":
                if not isinstance(edits, list) or len(edits) != len(original):
                    raise HTTPException(400, "The slide count must not change.")
                content = []
                for scene, value in zip(original, edits):
                    title = _text(value.get("title") if isinstance(value, dict) else value,
                                  "Slide title", blank=True, limit=500)
                    if isinstance(value, dict):
                        timestamp = _text(value.get("timestamp"), "Slide timestamp", limit=30)
                        scene = {**scene, "timestamp": timestamp, "time_seconds": _seconds(timestamp)}
                    if language:
                        content.append({**scene, "title_translations": {
                            **(scene.get("title_translations") or {}), language: title,
                        }})
                    else:
                        content.append({**scene, "title": title, "title_edited": bool(title)})
                times = [float(scene.get("time_seconds", 0)) for scene in content]
                if any(right <= left for left, right in zip(times, times[1:])):
                    raise HTTPException(400, "Slide timestamps must stay in increasing order.")
                for index, scene in enumerate(content[:-1]):
                    if isinstance(edits[index], dict):
                        scene["duration"] = times[index + 1] - times[index]
                transcript_versions._write_json(SCENES_DIR / f"{video_id}.json", content)
                return {"content": content, "revision": _revision(content)}
            transcript_versions.sync_existing(video_id)
            if kind == "chapters":
                structured = outline_operations(original, data.get("operations", []))
                content = _chapters(structured, edits)
                duration = media_duration if media_duration is not None else _transcript_duration(context[1])
                for chapter in content:
                    for row in [chapter, *chapter.get("sections", [])]:
                        if any(link.get("end", 0) > duration for link in row.get("resources", [])):
                            raise HTTPException(400, "Custom link times must be within the video duration.")
                version = persist_chapters(video_id, content, context, language, selected_language, source)
            else:
                if not isinstance(edits, list) or len(edits) != len(original):
                    raise HTTPException(400, "The transcript segment count must not change.")
                content = [{**item, "text": _text(text, "Transcript text")}
                           for item, text in zip(original, edits)]
                if transcript_versions.content_hash(content) == transcript_versions.content_hash(original):
                    raise HTTPException(400, "No transcript text has changed. Use Cancel to keep the original.")
                path = SUMMARIES_DIR / (f"{video_id}_summary_{language}.json" if language else f"{video_id}.json")
                chapters = _read(path, [])
                entry = context or {}
                stored = (_read(transcript_versions._versions_dir(video_id) / f"{entry['id']}.json", {})
                          if entry else {})
                metadata = stored.get("metadata")
                if metadata is None and not language and source == "whisper":
                    metadata = _read(TRANSCRIPTS_DIR / f"{video_id}_whisper_meta.json")
                # Save a backup of the displayed version before creating corrections.
                transcript_versions.save_version(
                    video_id, "translation" if language else "transcript", original,
                    source=entry.get("source") or ("uploaded" if source == "uploaded" else "whisper"),
                    language=language or None, chapters=chapters, metadata=metadata,
                )
                version = transcript_versions.save_version(
                    video_id, "translation" if language else "transcript", content,
                    source=entry.get("source") or ("uploaded" if source == "uploaded" else "whisper"),
                    language=language or None, model=entry.get("model"), chapters=chapters, edited=True,
                    metadata=metadata, based_on=entry.get("based_on"),
                )
                transcript_versions.activate_version(video_id, version["id"])
            return {"content": content, "revision": _revision(content), "version": version}
        except OSError as error:
            raise HTTPException(500, "Could not save edits. Check file permissions and try again.") from error
