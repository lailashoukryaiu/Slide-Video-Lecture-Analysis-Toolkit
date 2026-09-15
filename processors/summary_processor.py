import os
import json
import re
from fastapi.responses import JSONResponse
from google import genai
from google.genai import types
from dotenv import load_dotenv

class SummaryProcessor:
    def __init__(self):
        # Initialize Gemini API
        load_dotenv()

        # Prefer the environment variable for normal/local execution.
        GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

        # In Google Colab, use the Colab Secret directly.
        if not GOOGLE_API_KEY:
            from google.colab import userdata
            GOOGLE_API_KEY = userdata.get("GOOGLE_API_KEY")

        try:
            if not GOOGLE_API_KEY:
                raise ValueError("GOOGLE_API_KEY is not configured")

            self.client = genai.Client(api_key=GOOGLE_API_KEY)
            self.model_name = "gemini-3.6-flash"
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
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.5,
                    response_mime_type="application/json",
                ),
            )
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

            # Save chapters to file if video_id is provided
            if video_id:
                summary_path = f"static/summaries/{video_id}.json"
                with open(summary_path, 'w') as f:
                    json.dump(chapters, f)

            return JSONResponse({
                "success": True,
                "chapters": chapters
            })

        except Exception as e:
            return JSONResponse({
                "success": False,
                "error": str(e)
            })

    async def get_summary(self, video_id: str):
        """Get saved chapter summary for a video."""
        summary_path = f"static/summaries/{video_id}.json"
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