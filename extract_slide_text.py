"""Read the text on each slide with Tesseract and save it in the scenes file.

Adds a "slide_text" field to each scene. The detections and everything else in the file stay as they are.
A backup of the scenes file is made first, next to it, if one does not exist yet.

Run from the project root:
    python extract_slide_text.py <video_id>
Example:
    python extract_slide_text.py wjI1WNcIntg
"""

import json
import shutil
import sys
from pathlib import Path

import pytesseract
from PIL import Image

MIN_LINE_CHARS = 4
MIN_ALNUM_RATIO = 0.6


def resolve_image(base, rel):
    """Map the stored path (like /fullsize_images/<id>/0.jpg) to a file on disk."""
    rel = str(rel).lstrip("/\\")
    candidates = [
        base / "static" / rel,
        base / rel,
        base / "static" / rel.replace("fullsize_images/", "", 1),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def clean_text(raw):
    """Keep lines that look like real text. Drops the noisy fragments OCR produces on diagrams."""
    kept = []
    for line in raw.splitlines():
        line = line.strip()
        if len(line) < MIN_LINE_CHARS:
            continue
        non_space = [c for c in line if not c.isspace()]
        alnum = [c for c in non_space if c.isalnum()]
        if non_space and len(alnum) / len(non_space) >= MIN_ALNUM_RATIO:
            kept.append(line)
    return " ".join(kept)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    video_id = argv[1]
    base = Path(".").resolve()
    scenes_path = base / "static" / "scenes" / f"{video_id}.json"
    if not scenes_path.exists():
        print(f"No scenes file at {scenes_path}")
        return 1

    backup = scenes_path.with_name(f"{video_id}.backup.json")
    if not backup.exists():
        shutil.copy(scenes_path, backup)
        print(f"Backup saved: {backup.name}")

    scenes = json.loads(scenes_path.read_text(encoding="utf-8"))
    done = 0
    for index, scene in enumerate(scenes):
        rel = scene.get("fullsize")
        if not rel:
            print(f"Scene {index}: no fullsize image path")
            continue
        image_path = resolve_image(base, rel)
        if image_path is None:
            print(f"Scene {index}: image not found. Tried static/{str(rel).lstrip('/')}")
            continue
        raw = pytesseract.image_to_string(Image.open(image_path), config="--psm 6")
        text = clean_text(raw)
        scene["slide_text"] = text
        done += 1
        preview = text[:80] if text else "(no readable text)"
        print(f"Scene {index} at {scene.get('timestamp', '?')}: {len(text)} chars  {preview}")

    scenes_path.write_text(json.dumps(scenes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Done. Read {done} of {len(scenes)} slides. Saved to {scenes_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
