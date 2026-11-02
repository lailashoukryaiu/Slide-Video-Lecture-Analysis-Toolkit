"""Quiz generation, step 1: pick a scope and extract its content.

No AI calls happen here. This module only reads the saved lecture data:
- static/summaries/<video_id>.json              chapters and sections, timestamps as "MM:SS"
- static/transcripts/<video_id>_whisper.json    segments with float "start" in seconds
"""

import json
from dataclasses import dataclass
from pathlib import Path

SCOPE_TYPES = ("full", "chapter", "section")


@dataclass
class ContentScope:
    scope_type: str
    chapter_index: int | None
    section_index: int | None
    title: str
    start: float
    end: float


def parse_timestamp(value) -> float:
    """Convert 'MM:SS', 'HH:MM:SS', or a number of seconds into seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    seconds = 0.0
    for part in str(value).strip().split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def find_transcript(base_dir, video_id):
    """Whisper first, then YouTube captions, then any other transcript for this video."""
    folder = Path(base_dir) / "static" / "transcripts"
    for name in (f"{video_id}_whisper.json", f"{video_id}_youtube.json"):
        if (folder / name).exists():
            return folder / name
    matches = sorted(folder.glob(f"{video_id}_*.json"))
    if matches:
        return matches[0]
    raise FileNotFoundError(f"No transcript for {video_id}")


def load_lecture_data(base_dir, video_id):
    """Read the saved chapters and transcript for one video."""
    base = Path(base_dir)
    chapters = json.loads(
        (base / "static" / "summaries" / f"{video_id}.json").read_text(encoding="utf-8")
    )
    segments = json.loads(find_transcript(base, video_id).read_text(encoding="utf-8"))
    return chapters, segments


def _video_end(segments):
    # Small margin so the last segment still falls inside the range.
    return max((s["start"] + s.get("duration", 0) for s in segments), default=0.0) + 0.01


def build_scope(chapters, segments, scope_type="full", chapter_index=None, section_index=None):
    """Return the title and time range for the requested scope."""
    video_end = _video_end(segments)

    if scope_type == "full":
        return ContentScope("full", None, None, "Full lecture", 0.0, video_end)

    if scope_type not in SCOPE_TYPES:
        raise ValueError(f"Unknown scope_type: {scope_type!r}")

    if chapter_index is None or not 0 <= chapter_index < len(chapters):
        raise ValueError(f"chapter_index out of range: {chapter_index}")

    chapter = chapters[chapter_index]
    next_chapter_start = (
        parse_timestamp(chapters[chapter_index + 1]["timestamp"])
        if chapter_index + 1 < len(chapters)
        else video_end
    )

    if scope_type == "chapter":
        return ContentScope(
            "chapter",
            chapter_index,
            None,
            chapter["title"],
            parse_timestamp(chapter["timestamp"]),
            next_chapter_start,
        )

    sections = chapter.get("sections", [])
    if section_index is None or not 0 <= section_index < len(sections):
        raise ValueError(f"section_index out of range: {section_index}")

    next_section_start = (
        parse_timestamp(sections[section_index + 1]["timestamp"])
        if section_index + 1 < len(sections)
        else next_chapter_start
    )
    return ContentScope(
        "section",
        chapter_index,
        section_index,
        sections[section_index]["title"],
        parse_timestamp(sections[section_index]["timestamp"]),
        next_section_start,
    )


def extract_content(chapters, segments, scope):
    """Gather the transcript text and summary that belong to a scope."""
    in_range = [s for s in segments if scope.start <= s["start"] < scope.end]

    if scope.scope_type == "full":
        summary = " ".join(c["summary"] for c in chapters)
    elif scope.scope_type == "chapter":
        summary = chapters[scope.chapter_index]["summary"]
    else:
        summary = chapters[scope.chapter_index]["sections"][scope.section_index]["point"]

    return {
        "title": scope.title,
        "time_range": {"start": scope.start, "end": scope.end},
        "summary": summary,
        "transcript_text": " ".join(s["text"].strip() for s in in_range),
        "segment_count": len(in_range),
    }


if __name__ == "__main__":
    # Run from the project root: python processors\quiz_processor.py
    chapters, segments = load_lecture_data(".", "wjI1WNcIntg")
    cases = [("full", None, None), ("chapter", 1, None), ("section", 1, 0)]
    for scope_type, ci, si in cases:
        scope = build_scope(chapters, segments, scope_type, ci, si)
        content = extract_content(chapters, segments, scope)
        print(f"\n== {scope_type}: {content['title']}")
        print(f"   range: {scope.start:.0f}s to {scope.end:.0f}s, segments: {content['segment_count']}")
        print(f"   summary: {content['summary'][:120]}")
        print(f"   transcript: {content['transcript_text'][:120]}...")
