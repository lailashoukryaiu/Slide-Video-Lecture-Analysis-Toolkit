"""Notes and summary API.

Add these two lines to main.py, after the quiz router lines:

    from notes_routes import router as notes_router
    app.include_router(notes_router)
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from processors.quiz_generator import call_gemini

BASE_DIR = Path(__file__).resolve().parent
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
MAX_TRANSCRIPT_CHARS = 60000

router = APIRouter(prefix="/api/notes", tags=["notes"])


class NotesBody(BaseModel):
    notes: str


def _check_video_id(video_id):
    if not VIDEO_ID_PATTERN.match(video_id):
        raise HTTPException(status_code=400, detail="Invalid video_id")


def _notes_path(video_id):
    return BASE_DIR / "static" / "notes" / f"{video_id}.json"


def _read(video_id):
    path = _notes_path(video_id)
    if not path.exists():
        return {"notes": "", "summary": "", "updated_at": None, "summary_updated_at": None}
    return json.loads(path.read_text(encoding="utf-8"))


def _write(video_id, data):
    path = _notes_path(video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _strip_fences(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return text


@router.get("/{video_id}")
def get_notes(video_id: str):
    _check_video_id(video_id)
    return _read(video_id)


@router.put("/{video_id}")
def save_notes(video_id: str, body: NotesBody):
    _check_video_id(video_id)
    data = _read(video_id)
    data["notes"] = body.notes
    data["updated_at"] = _now()
    _write(video_id, data)
    return data


@router.post("/{video_id}/summary")
def create_summary(video_id: str):
    _check_video_id(video_id)

    transcript_path = BASE_DIR / "static" / "transcripts" / f"{video_id}_whisper.json"
    if not transcript_path.exists():
        raise HTTPException(status_code=404, detail="This video has no transcript yet")
    segments = json.loads(transcript_path.read_text(encoding="utf-8"))
    transcript = " ".join(s["text"].strip() for s in segments)[:MAX_TRANSCRIPT_CHARS]

    prompt = (
        "You are helping a student review a lecture. Summarize the transcript below.\n"
        "Write one short overview paragraph, then 5 to 8 bullet points of the key ideas, "
        "in the order they appear in the lecture. Start each bullet with '- '. "
        "Keep it clear and specific.\n"
        'Return only JSON like {"summary": "overview paragraph\\n- first point\\n- second point"}\n\n'
        f"Transcript:\n{transcript}"
    )
    try:
        data = json.loads(_strip_fences(call_gemini(prompt)))
        summary = str(data.get("summary", "")).strip()
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"AI provider error: {error}")
    if not summary:
        raise HTTPException(status_code=502, detail="The model returned an empty summary")

    stored = _read(video_id)
    stored["summary"] = summary
    stored["summary_updated_at"] = _now()
    _write(video_id, stored)
    return stored
