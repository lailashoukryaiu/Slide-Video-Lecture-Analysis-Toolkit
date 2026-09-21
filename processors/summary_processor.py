import os
import json
import re
import asyncio
from fastapi.responses import JSONResponse
from google import genai
from google.genai import types
from dotenv import load_dotenv

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

        try:
            if not GOOGLE_API_KEY:
                raise ValueError("GOOGLE_API_KEY is not configured")

            self.client = genai.Client(api_key=GOOGLE_API_KEY)
            self.model_name = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
            self.model = True
        except Exception as e:
            print(f"Warning: Gemini API initialization failed: {str(e)}")
            self.model = None

    async def generate_summary(self, transcript: list, video_id: str = None):
        """Generate chapter summary using Gemini."""
        try:
            if not transcript or not self.model:
                return JSONResponse({
                    "success": False,
                    "error": "No transcript available or Gemini API not initialized"
                })

            # Combine transcript text with timestamps
            full_text = ""
            for item in transcript:
                hours = int(item["start"] // 3600)
                minutes = int((item["start"] % 3600) // 60)
                seconds = int(item["start"] % 60)
                timestamp = f"{hours:02d}:{minutes:02d}:{seconds:02d}: "
                full_text += timestamp + item["text"] + "\n"
            

            # Generate chapter summary using Gemini
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
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.client.models.generate_content,
                        model=self.model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.5,
                            response_mime_type="application/json",
                        ),
                    ),
                    timeout=90,
                )
            except asyncio.TimeoutError:
                return JSONResponse({
                    "success": False,
                    "error": "Gemini summary generation timed out after 90 seconds. Please retry."
                })
            except Exception as error:
                return JSONResponse({
                    "success": False,
                    "error": f"Gemini could not generate a summary: {error}"
                })
            try:
                # Try to parse the response as JSON
                chapters = json.loads(response.text)
            except json.JSONDecodeError:
                # If parsing fails, try to extract JSON from the response text
                match = re.search(r'\[.*\]', response.text.replace('\n', ' '), re.DOTALL)
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
                "chapters": chapters
            })

        except Exception as e:
            return JSONResponse({
                "success": False,
                "error": str(e)
            })

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