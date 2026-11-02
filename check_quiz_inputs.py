"""Check what the quiz generator will see for one video. No AI calls are made.

Run from the project root:
    python check_quiz_inputs.py <video_id> [scope_type] [chapter_index] [section_index]
Example:
    python check_quiz_inputs.py wjI1WNcIntg
    python check_quiz_inputs.py wjI1WNcIntg chapter 1
"""

import sys

from processors.quiz_generator import (
    MAX_PROMPT_CHARS,
    WINDOW_SECONDS,
    _collect,
    _windows,
    format_clock,
    load_slides,
)
from processors.quiz_processor import build_scope, find_transcript, load_lecture_data


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    video_id = argv[1]
    scope_type = argv[2] if len(argv) > 2 else "full"
    chapter_index = int(argv[3]) if len(argv) > 3 else None
    section_index = int(argv[4]) if len(argv) > 4 else None

    chapters, segments = load_lecture_data(".", video_id)
    slides = load_slides(".", video_id)
    duration = max((s["start"] + s.get("duration", 0) for s in segments), default=0)

    print(f"Transcript file: {find_transcript('.', video_id).name}")
    print(f"Segments: {len(segments)}, length: {format_clock(duration)}")
    print(f"Slides with OCR text: {len(slides)}")
    for t, text in slides[:5]:
        print(f"  {format_clock(t)}  {text[:80]}")
    if not slides:
        print("  No slide text found. Run slide detection and text extraction on this video first.")

    scope = build_scope(chapters, segments, scope_type, chapter_index, section_index)
    transcript_text, slide_text = _collect(segments, slides, scope.start, scope.end)
    total = len(transcript_text) + len(slide_text)
    print(f"Scope: {scope.title}, {format_clock(scope.start)} to {format_clock(scope.end)}")
    print(f"Evidence characters: transcript {len(transcript_text)}, slides {len(slide_text)}")
    if total <= MAX_PROMPT_CHARS:
        print("Plan: one prompt for the whole scope")
    else:
        spans = _windows(scope.start, scope.end)
        print(f"Plan: {len(spans)} windows of {WINDOW_SECONDS // 60} minutes each")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
