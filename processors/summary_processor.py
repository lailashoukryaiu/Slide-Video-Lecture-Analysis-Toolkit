import os
import json
import re
import asyncio
from fastapi.responses import JSONResponse
from google import genai
from google.genai import types
from dotenv import load_dotenv
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

from project_paths import SUMMARIES_DIR

class SummaryProcessor:
    def __init__(self):
        # Initialize Gemini API
        load_dotenv()

        # Prefer the environment variable for normal/local execution.
        GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

        # Colab Secrets are available only from the interactive notebook
        # process. The server process receives the key through the environment.
        # Do not call google.colab.userdata from the FastAPI/Uvicorn process.

        self.client = None
        self.model = None
        self.openai_client = None
        self.openai_model_name = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        if OpenAI and os.getenv("OPENAI_API_KEY"):
            self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        try:
            if not GOOGLE_API_KEY:
                raise ValueError("GOOGLE_API_KEY is not configured")

            self.client = genai.Client(api_key=GOOGLE_API_KEY)
            self.model_name = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
            self.model = True
        except Exception as e:
            print(f"Warning: Gemini API initialization failed: {str(e)}")
            self.model = None

    async def generate_summary(self, transcript: list, video_id: str = None, requested_model: str = None):
        """Generate chapter summary using Gemini."""
        try:
            if not transcript or (not self.model and not self.openai_client):
                return JSONResponse({
                    "success": False,
                    "error": "No transcript available or no summary model is configured"
                })

            # Combine transcript text with timestamps
            full_text = ""
            for item in transcript:
                hours = int(item["start"] // 3600)
                minutes = int((item["start"] % 3600) // 60)
                seconds = int(item["start"] % 60)
                timestamp = f"{hours:02d}:{minutes:02d}:{seconds:02d}: "
                full_text += timestamp + item["text"] + "\n"
            

            prompt = f"""Based on the following transcript with timestamps, create chapters that outline the main topics.
For each chapter, provide:
The timestamp where the chapter starts (in MM:SS format)
A title
Format each chapter exactly like this example:
[
{{"timestamp": "00:00", "title": "Introduction to the topic"}},
{{"timestamp": "02:30", "title": "Key concept explained"}},
{{"timestamp": "05:45", "title": "Practical examples"}}
]
{full_text}"""

            # dump prompt into a debug file
            with open('debug.txt', 'w') as f:
                f.write(prompt)
            selected_model = requested_model or self.model_name
            use_openai = selected_model.startswith("gpt-")
            provider = "OpenAI" if use_openai else "Gemini"
            try:
                if use_openai:
                    if not self.openai_client:
                        raise RuntimeError("OpenAI is not configured")
                    response = await asyncio.wait_for(
                        asyncio.to_thread(
                            self.openai_client.chat.completions.create,
                            model=selected_model,
                            messages=[{"role": "user", "content": prompt}],
                            temperature=0.5,
                        ),
                        timeout=90,
                    )
                    response_text = response.choices[0].message.content
                elif not self.model:
                    raise RuntimeError("Gemini is not configured")
                else:
                    response_text = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.client.models.generate_content,
                        model=selected_model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.5,
                            response_mime_type="application/json",
                        ),
                    ),
                    timeout=90,
                    )
                    response_text = response_text.text
            except Exception as error:
                if use_openai:
                    return JSONResponse({"success": False, "error": f"OpenAI could not generate chapters: {error}"})
                if not self._is_quota_error(error):
                    return JSONResponse({
                        "success": False,
                        "error": f"Gemini could not generate a summary: {error}"
                    })
                if not self.openai_client:
                    return JSONResponse({
                        "success": False,
                        "error": "Gemini quota is exhausted. Configure OPENAI_API_KEY to use the OpenAI fallback."
                    })
                provider = "OpenAI fallback"
                try:
                    response = await asyncio.wait_for(
                        asyncio.to_thread(
                            self.openai_client.chat.completions.create,
                            model=self.openai_model_name,
                            messages=[{"role": "user", "content": prompt}],
                            temperature=0.5,
                        ),
                        timeout=90,
                    )
                    response_text = response.choices[0].message.content
                except Exception as fallback_error:
                    return JSONResponse({
                        "success": False,
                        "error": f"Gemini quota is exhausted and OpenAI fallback failed: {fallback_error}"
                    })
            try:
                # Try to parse the response as JSON
                chapters = json.loads(response_text)
                if isinstance(chapters, dict) and isinstance(chapters.get("chapters"), list):
                    chapters = chapters["chapters"]
            except json.JSONDecodeError:
                # If parsing fails, try to extract JSON from the response text
                match = re.search(r'\[.*\]', response_text.replace('\n', ' '), re.DOTALL)
                if match:
                    chapters = json.loads(match.group())
                else:
                    raise ValueError("Could not parse Gemini response as JSON")
            
            if isinstance(chapters, list) and chapters and isinstance(chapters[0], list):
                chapters = [item for sublist in chapters for item in sublist]

            chapters = self._normalize_chapters(chapters, transcript)
            self._save_chapters(video_id, chapters)

            return JSONResponse({
                "success": True,
                "chapters": chapters,
                "provider": provider,
                "notice": "Gemini quota was exceeded; the OpenAI fallback generated these chapters."
                    if provider == "OpenAI fallback" else None
            })

        except Exception as e:
            return JSONResponse({
                "success": False,
                "error": str(e)
            })

    async def translate_transcript(self, transcript, target_language):
        if not self.model and not self.openai_client:
            raise RuntimeError("No translation model is configured")
        prompt = (
            f"Translate each transcript segment into language code {target_language}. "
            "Return only a JSON array with the same start, duration, and translated text fields.\n"
            + json.dumps(transcript, ensure_ascii=False)
        )
        if self.model:
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            result = response.text
        else:
            response = await asyncio.to_thread(
                self.openai_client.chat.completions.create,
                model=self.openai_model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            result = response.choices[0].message.content
        translated = json.loads(result)
        if not isinstance(translated, list):
            raise ValueError("Translation model returned an invalid transcript")
        return translated

    @staticmethod
    def _is_quota_error(error: Exception) -> bool:
        text = str(error).lower()
        return any(marker in text for marker in (
            "429", "503", "resource_exhausted", "quota", "rate limit",
            "too many requests", "unavailable", "high demand", "overloaded"
        ))

    @staticmethod
    def _fallback_chapters(transcript: list) -> list:
        """Create usable chapters without an external model."""
        chapters = []
        last_start = None
        for item in transcript:
            start = float(item.get("start", 0))
            text = " ".join(str(item.get("text", "")).split())
            if not text:
                continue
            if last_start is None or start - last_start >= 180:
                title = " ".join(text.split()[:8]).strip(".,!?")
                chapters.append({
                    "timestamp": f"{int(start // 60):02d}:{int(start % 60):02d}",
                    "title": title or f"Chapter {len(chapters) + 1}"
                })
                last_start = start
        return chapters or [{"timestamp": "00:00", "title": "Lecture"}]

    @staticmethod
    def _normalize_chapters(chapters: list, transcript: list) -> list:
        """Ensure every chapter has a usable timestamp and title."""
        if not isinstance(chapters, list):
            raise ValueError("Gemini returned chapters in an invalid format")

        normalized = []
        for index, chapter in enumerate(chapters):
            if not isinstance(chapter, dict):
                continue
            raw_timestamp = chapter.get("timestamp", "00:00")
            raw_title = " ".join(str(chapter.get("title", "")).split()).strip()
            try:
                timestamp_parts = [int(float(part)) for part in str(raw_timestamp).split(":")]
                if len(timestamp_parts) == 2:
                    total_seconds = timestamp_parts[0] * 60 + timestamp_parts[1]
                elif len(timestamp_parts) == 3:
                    total_seconds = timestamp_parts[0] * 3600 + timestamp_parts[1] * 60 + timestamp_parts[2]
                else:
                    raise ValueError
            except (TypeError, ValueError):
                total_seconds = int(float(transcript[index].get("start", 0))) if index < len(transcript) else 0

            if not raw_title:
                matching_text = next(
                    (
                        " ".join(str(item.get("text", "")).split())
                        for item in transcript
                        if abs(float(item.get("start", 0)) - total_seconds) < 30
                    ),
                    f"Chapter {index + 1}",
                )
                raw_title = " ".join(matching_text.split()[:8]).strip(".,!?") or f"Chapter {index + 1}"

            normalized.append({
                "timestamp": f"{total_seconds // 60:02d}:{total_seconds % 60:02d}",
                "title": raw_title,
            })
        if not normalized:
            raise ValueError("Gemini returned no valid chapters")
        return normalized

    @staticmethod
    def _save_chapters(video_id: str, chapters: list):
        if video_id:
            summary_path = SUMMARIES_DIR / f"{video_id}.json"
            with summary_path.open("w", encoding="utf-8") as file:
                json.dump(chapters, file)

    @staticmethod
    def _looks_like_legacy_fallback(chapters: list) -> bool:
        if not isinstance(chapters, list) or not chapters:
                return True
        titles = [str(item.get("title", "")).strip().lower() for item in chapters if isinstance(item, dict)]
        if len(titles) != len(chapters) or any(not title for title in titles):
                return True
        if all(title == f"chapter {index + 1}" for index, title in enumerate(titles)):
                return True
        if all(title == f"interval {index + 1}" for index, title in enumerate(titles)):
                return True
        timestamps = []
        for item in chapters:
                try:
                    timestamps.append(SummaryProcessor._timestamp_seconds(str(item["timestamp"])))
                except (KeyError, TypeError, ValueError):
                    return True
        gaps = [right - left for left, right in zip(timestamps, timestamps[1:])]
        return len(gaps) >= 2 and all(gap == 180 for gap in gaps)

    @staticmethod
    def _timestamp_seconds(timestamp: str) -> int:
        parts = [int(float(part)) for part in timestamp.split(":")]
        if len(parts) == 2:
                return parts[0] * 60 + parts[1]
        if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
        raise ValueError("Invalid timestamp")

    async def get_summary(self, video_id: str):
        """Get saved chapter summary for a video."""
        summary_path = str(SUMMARIES_DIR / f"{video_id}.json")
        if os.path.exists(summary_path):
            try:
                with open(summary_path, 'r') as f:
                    chapters = json.load(f)
                if isinstance(chapters, list) and chapters and isinstance(chapters[0], list):
                    chapters = [item for sublist in chapters for item in sublist]
                if self._looks_like_legacy_fallback(chapters):
                    return JSONResponse({
                        "success": True,
                        "chapters": [],
                        "exists": False,
                        "stale": True
                    })
                chapters = self._normalize_chapters(chapters, [])
                return JSONResponse({
                    "success": True,
                    "chapters": chapters,
                    "exists": True
                })
            except Exception as e:
                return JSONResponse({
                    "success": False,
                    "error": str(e)
                })
        return JSONResponse({
            "success": True,
            "chapters": [],
            "exists": False
        })