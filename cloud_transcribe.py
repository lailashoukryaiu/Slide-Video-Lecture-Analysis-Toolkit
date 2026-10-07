"""Online transcription through free-tier APIs (Groq Whisper and Gemini)."""
import json
import math
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from ffmpeg_tools import media_executable

CLOUD_TRANSCRIPTION_MODELS = {
    "gemini:gemini-3.8-flash": {
        "provider": "gemini",
        "api_model": "gemini-3.8-flash",
        "label": "Gemini 3.8 Flash",
    },
    "groq:whisper-large-v3-turbo": {
        "provider": "groq",
        "api_model": "whisper-large-v3-turbo",
        "label": "Groq Whisper Large v3 Turbo",
    },
    "groq:whisper-large-v3": {
        "provider": "groq",
        "api_model": "whisper-large-v3",
        "label": "Groq Whisper Large v3",
    },
    "gemini:gemini-3.6-flash": {
        "provider": "gemini",
        "api_model": "gemini-3.6-flash",
        "label": "Gemini 3.6 Flash",
    },
    "gemini:gemini-2.5-flash": {
        "provider": "gemini",
        "api_model": "gemini-2.5-flash",
        "label": "Gemini 2.5 Flash",
    },
}

PROVIDER_KEY_NAMES = {
    "groq": ("GROQ_API_KEY",),
    "gemini": ("GOOGLE_API_KEY",),
}

# Groq accepts files up to 25 MB on the free tier; 20 minutes of 32 kbit/s mono
# MP3 is about 5 MB. Gemini uses shorter parts to keep timestamps accurate and
# the JSON response well below the output token limit.
CHUNK_SECONDS = {"groq": 1200, "gemini": 600}
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
# Whisper only reads the last 224 tokens of a prompt; about 800 characters.
GROQ_PROMPT_CHARACTERS = 800
RETRY_DELAYS_SECONDS = (15, 30, 60, 90)


def is_cloud_model(model):
    return model in CLOUD_TRANSCRIPTION_MODELS


def provider_api_key(provider):
    for name in PROVIDER_KEY_NAMES.get(provider, ()):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


def cloud_provider_status():
    return {provider: bool(provider_api_key(provider)) for provider in PROVIDER_KEY_NAMES}


def recommended_transcription_model(cuda_available):
    """Pick the best default for this session's hardware and configured keys."""
    if cuda_available:
        return "turbo"
    if provider_api_key("groq"):
        return "groq:whisper-large-v3-turbo"
    if provider_api_key("gemini"):
        return "gemini:gemini-3.8-flash"
    return "turbo"


def describe_cloud_method(model):
    provider = CLOUD_TRANSCRIPTION_MODELS[model]["provider"]
    minutes = CHUNK_SECONDS[provider] // 60
    if provider == "gemini":
        return f"Gemini API in {minutes}-minute parts (approximate timestamps)"
    return f"Groq API in {minutes}-minute parts"


def _require_ffmpeg():
    media_executable("ffmpeg")
    media_executable("ffprobe")


def media_duration(path):
    _require_ffmpeg()
    result = subprocess.run(
        [
            media_executable("ffprobe"), "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip() or 0)


def extract_audio_chunk(video_path, output_path, start, duration):
    _require_ffmpeg()
    subprocess.run(
        [
            media_executable("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(video_path),
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", str(output_path),
        ],
        check=True,
    )


def _is_retryable(error):
    text = str(error).lower()
    return any(marker in text for marker in (
        "429", "rate limit", "rate_limit", "too many requests", "quota",
        "resource_exhausted", "503", "unavailable", "overloaded", "high demand",
        "timeout", "timed out", "connection",
    ))


def _with_retries(call, description, on_step=None):
    last_error = None
    for attempt, wait in enumerate((0,) + RETRY_DELAYS_SECONDS):
        if wait:
            message = (
                f"{description} is rate limited or busy ({str(last_error)[:150]}); "
                f"retry {attempt} of {len(RETRY_DELAYS_SECONDS)} in {wait}s"
            )
            print(message)
            if on_step:
                on_step(message)
            time.sleep(wait)
        try:
            return call()
        except Exception as error:
            last_error = error
            if attempt == len(RETRY_DELAYS_SECONDS) or not _is_retryable(error):
                raise RuntimeError(f"{description} failed: {error}") from error


def _field(item, name, default=None):
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _transcribe_groq_chunk(audio_path, api_model, api_key, prompt=None, on_step=None):
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL, max_retries=0)
    prompt_options = {"prompt": prompt[:GROQ_PROMPT_CHARACTERS]} if prompt else {}

    def call():
        with open(audio_path, "rb") as audio_file:
            return client.audio.transcriptions.create(
                model=api_model,
                file=audio_file,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
                temperature=0,
                **prompt_options,
            )

    result = _with_retries(call, "Groq transcription", on_step)
    segments = []
    for segment in _field(result, "segments", None) or []:
        text = str(_field(segment, "text", "") or "").strip()
        if text:
            segments.append({
                "start": float(_field(segment, "start", 0) or 0),
                "end": float(_field(segment, "end", 0) or 0),
                "text": text,
            })
    return segments


GEMINI_PROMPT = (
    "Transcribe the speech in this audio clip verbatim, in the language that is "
    "spoken. Do not translate, summarize, correct or omit anything. Return JSON "
    'only, in the form {"segments": [{"start": 0.0, "end": 4.2, "text": "..."}]}. '
    "start and end are seconds from the beginning of this clip. Each segment is "
    "one sentence or phrase of at most 15 seconds, in chronological order. "
    'If there is no speech, return {"segments": []}.'
)

GEMINI_TRANSCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "text": {"type": "string"},
                },
                "required": ["start", "end", "text"],
            },
        },
    },
    "required": ["segments"],
}


def _parse_seconds(value):
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if ":" in text:
        seconds = 0.0
        for part in text.split(":"):
            seconds = seconds * 60 + float(part or 0)
        return seconds
    return float(text or 0)


def parse_gemini_segments(response_text, clip_duration):
    text = (response_text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    data = json.loads(text)
    items = data.get("segments", []) if isinstance(data, dict) else data
    segments = []
    previous_start = 0.0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("text", "")).strip()
        if not content:
            continue
        try:
            start = _parse_seconds(item.get("start"))
            end = _parse_seconds(item.get("end", start))
        except ValueError:
            continue
        start = min(max(start, previous_start), clip_duration)
        end = min(max(end, start), clip_duration)
        segments.append({"start": start, "end": end, "text": content})
        previous_start = start
    return segments


def _transcribe_gemini_chunk(
    audio_path, api_model, api_key, clip_duration, prompt=None, on_step=None
):
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    audio_bytes = Path(audio_path).read_bytes()
    instructions = GEMINI_PROMPT
    if prompt:
        instructions += (
            "\nContext and spelling guidance from the user (use it to recognize names "
            f"and terms; still transcribe only what is spoken): {prompt}"
        )

    def call():
        return client.models.generate_content(
            model=api_model,
            contents=[
                types.Part.from_bytes(data=audio_bytes, mime_type="audio/mp3"),
                instructions,
            ],
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                response_schema=GEMINI_TRANSCRIPT_SCHEMA,
            ),
        )

    for attempt in range(2):
        response = _with_retries(call, "Gemini transcription", on_step)
        try:
            return parse_gemini_segments(response.text, clip_duration)
        except (json.JSONDecodeError, TypeError, AttributeError) as error:
            if attempt == 1:
                raise RuntimeError(
                    f"Gemini returned an unreadable transcript after retry: {error}"
                ) from error
            message = "Gemini returned invalid transcript JSON; retrying this audio part once"
            print(message)
            if on_step:
                on_step(message)


def transcribe_audio_cloud(video_path, model, prompt=None, on_step=None):
    """Yield (transcript items, progress percent) for each transcribed part."""
    if model not in CLOUD_TRANSCRIPTION_MODELS:
        raise ValueError(f"Unsupported online transcription model: {model}")
    step = on_step or (lambda message: None)
    config = CLOUD_TRANSCRIPTION_MODELS[model]
    provider = config["provider"]
    provider_name = "Groq" if provider == "groq" else "Google Gemini"
    api_key = provider_api_key(provider)
    if not api_key:
        key_name = PROVIDER_KEY_NAMES[provider][0]
        raise RuntimeError(
            f"{config['label']} requires {key_name} in the server environment. "
            "Add it and restart the server."
        )
    step("Reading the video's duration")
    total = media_duration(video_path)
    if total <= 0:
        raise RuntimeError("Could not read the video's duration for online transcription.")
    chunk_seconds = CHUNK_SECONDS[provider]
    part_count = max(1, math.ceil(total / chunk_seconds))
    print(f"Online transcription: {config['label']}, {total:.0f}s in {chunk_seconds}s parts")
    step(
        f"Video has {total / 60:.1f} min of audio; sending {part_count} "
        f"part{'s' if part_count != 1 else ''} to {config['label']}"
    )

    with tempfile.TemporaryDirectory(prefix="cloud_transcribe_") as directory:
        start = 0.0
        index = 0
        while start < total:
            duration = min(chunk_seconds, total - start)
            audio_path = Path(directory) / f"part_{index}.mp3"
            label = f"Part {index + 1}/{part_count}"
            step(f"{label}: extracting audio with ffmpeg")
            started = time.monotonic()
            extract_audio_chunk(video_path, audio_path, start, duration)
            size_mb = audio_path.stat().st_size / 1_000_000 if audio_path.exists() else 0
            step(
                f"{label}: audio extracted in {time.monotonic() - started:.0f}s "
                f"({size_mb:.1f} MB); waiting for {provider_name}"
            )
            started = time.monotonic()
            if provider == "groq":
                segments = _transcribe_groq_chunk(
                    audio_path, config["api_model"], api_key, prompt, on_step=step
                )
            else:
                segments = _transcribe_gemini_chunk(
                    audio_path, config["api_model"], api_key, duration, prompt, on_step=step
                )
            step(
                f"{label}: {provider_name} returned {len(segments)} segments "
                f"in {time.monotonic() - started:.0f}s"
            )
            items = [
                {
                    "text": segment["text"],
                    "start": round(start + segment["start"], 3),
                    "duration": round(max(0.0, segment["end"] - segment["start"]), 3),
                }
                for segment in segments
            ]
            audio_path.unlink(missing_ok=True)
            start += duration
            index += 1
            yield items, min(100.0, start / total * 100)
