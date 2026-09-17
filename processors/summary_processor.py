import os
import json
import re
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
            self.model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
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
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.5,
                        response_mime_type="application/json",
                    ),
                )
            except Exception as error:
                chapters = self._fallback_chapters(transcript)
                self._save_chapters(video_id, chapters)
                return JSONResponse({
                    "success": True,
                    "chapters": chapters,
                    "fallback": True,
                    "message": f"Gemini was unavailable ({error}); generated timestamp-based chapters instead."
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
            
            if isinstance(chapters, list) and isinstance(chapters[0], list):
                chapters = [item for sublist in chapters for item in sublist]

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
    def _save_chapters(video_id: str, chapters: list):
        if video_id:
            summary_path = SUMMARIES_DIR / f"{video_id}.json"
            with summary_path.open("w", encoding="utf-8") as file:
                json.dump(chapters, file)

    async def get_summary(self, video_id: str):
        """Get saved chapter summary for a video."""
        summary_path = str(SUMMARIES_DIR / f"{video_id}.json")
        if os.path.exists(summary_path):
            try:
                with open(summary_path, 'r') as f:
                    chapters = json.load(f)
                # if chapters is not flat, flatten
                if isinstance(chapters, list) and isinstance(chapters[0], list):
                    chapters = [item for sublist in chapters for item in sublist]
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
        else:
            return JSONResponse({
                "success": True,
                "chapters": [],
                "exists": False
            }) 