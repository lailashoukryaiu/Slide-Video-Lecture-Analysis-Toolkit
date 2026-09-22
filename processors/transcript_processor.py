import os
import json
import time
from fastapi.responses import JSONResponse
from youtube_transcript_api import YouTubeTranscriptApi
from transcribe import transcribe_audio
from project_paths import VIDEO_DIR, TRANSCRIPTS_DIR

class TranscriptProcessor:
    def __init__(self):
        self.transcript_preference = "youtube"  # Can be "youtube" or "whisper"
        self.progress_stale_seconds = int(os.getenv("WHISPER_PROGRESS_STALE_SECONDS", "900"))

    async def get_transcript(self, video_id: str, source: str):
        """Get a specific transcript by source."""
        if source not in ["youtube", "whisper"]:
            return JSONResponse({
                "success": False,
                "error": "Invalid source. Must be 'youtube' or 'whisper'."
            })
        
        transcript_path = str(TRANSCRIPTS_DIR / f"{video_id}_{source}.json")
        if not os.path.exists(transcript_path):
            # If transcript doesn't exist and source is youtube, try to fetch it
            if source == "youtube":
                transcript, error = await self.get_youtube_transcript(video_id)
                if transcript:
                    return JSONResponse({
                        "success": True,
                        "transcript": transcript
                    })
                else:
                    return JSONResponse({
                        "success": False,
                        "error": error or "Failed to fetch YouTube transcript"
                    })
            return JSONResponse({
                "success": False,
                "error": f"No {source} transcript available for this video"
            })
        
        try:
            with open(transcript_path, 'r') as f:
                transcript = json.load(f)
            
            # Ensure consistent format
            if source == "youtube":
                transcript = self.format_youtube_transcript(transcript)
            else:
                names = self._read_speaker_names(video_id)
                transcript = [
                    {
                        **item,
                        "speaker_name": names.get(item.get("speaker"), item.get("speaker"))
                        if item.get("speaker") else None,
                    }
                    for item in transcript
                ]
            
            return JSONResponse({
                "success": True,
                "transcript": transcript
            })
        except Exception as e:
            return JSONResponse({
                "success": False,
                "error": str(e)
            })

    def format_youtube_transcript(self, transcript):
        """Ensure YouTube transcript has consistent format."""
        formatted = []
        for item in transcript:
            formatted.append({
                "text": item.get("text", ""),
                "start": item.get("start", 0),
                "duration": item.get("duration", 0)
            })
        return formatted

    async def get_youtube_transcript(self, video_id: str):
        """Get transcript from YouTube."""
        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['en', 'fr'])
            
            # Format transcript
            formatted_transcript = self.format_youtube_transcript(transcript)
            
            # Save transcript for future use
            with open(str(TRANSCRIPTS_DIR / f"{video_id}_youtube.json"), 'w') as f:
                json.dump(formatted_transcript, f)
            
            return formatted_transcript, None
        except Exception as e:
            return None, str(e)

    async def generate_whisper_transcript(self, video_id: str, background_tasks, model="turbo", prompt=None, diarization=False):
        """Generate a transcript using Whisper."""
        try:
            # Check if video exists
            video_files = os.listdir(VIDEO_DIR)
            video_path = None
            for file in video_files:
                if file.startswith(video_id):
                    video_path = str(VIDEO_DIR / file)
                    break
            
            if not video_path:
                return JSONResponse({
                    "success": False,
                    "error": "Video not found"
                })
            
            output_path = str(TRANSCRIPTS_DIR / f"{video_id}_whisper.json")
            progress_path = TRANSCRIPTS_DIR / f"{video_id}_whisper_progress.txt"

            # Check if transcript already exists
            if model not in {"turbo", "small", "medium", "large-v3"}:
                return JSONResponse({"success": False, "error": "Unsupported transcription model"})
            if os.path.exists(output_path) and model == "turbo" and not prompt:
                with open(output_path, 'r') as f:
                    transcript = json.load(f)
                return JSONResponse({
                    "success": True,
                    "transcript": transcript,
                    "message": "Using existing Whisper transcript"
                })

            if progress_path.exists():
                return JSONResponse({
                    "success": True,
                    "message": "Transcript generation is already in progress",
                    "status": "in_progress"
                })
            
            # Start background task to generate transcript
            if os.path.exists(output_path):
                os.remove(output_path)
            background_tasks.add_task(
                self.process_whisper_transcript, video_id, video_path, output_path, model, prompt, diarization
            )
            
            return JSONResponse({
                "success": True,
                "message": "Transcript generation started",
                "status_url": f"/whisper_transcript_status/{video_id}"
            })
            
        except Exception as e:
            return JSONResponse({
                "success": False,
                "error": str(e)
            })

    async def start_whisper_generation(self, video_id: str, video_path: str, background_tasks):
        """Start Whisper transcript generation."""
        output_path = str(TRANSCRIPTS_DIR / f"{video_id}_whisper.json")
        error_path = TRANSCRIPTS_DIR / f"{video_id}_whisper_error.txt"
        progress_path = TRANSCRIPTS_DIR / f"{video_id}_whisper_progress.txt"
        if error_path.exists():
            error_path.unlink()
        if progress_path.exists():
            progress_age = time.time() - progress_path.stat().st_mtime
            if progress_age <= self.progress_stale_seconds:
                return
            progress_path.unlink()

        # Create progress file
        with open(progress_path, 'w') as f:
            f.write("0")
        
        # Start background task
        background_tasks.add_task(self.process_whisper_transcript, video_id, video_path, output_path)

    async def process_whisper_transcript(self, video_id: str, video_path: str, output_path: str, model="turbo", prompt=None, diarization=False):
        """Process video with Whisper and save transcript."""
        try:
            transcript = []
            
            for sentence_data, progress in transcribe_audio(video_path, model_name=model, prompt=prompt):
                lines = sentence_data.strip().split('\n')
                i = 0
                while i < len(lines):
                    if i + 2 < len(lines) and '-->' in lines[i+1]:
                        timestamp_line = lines[i+1]
                        start_time = timestamp_line.split(' --> ')[0].strip()
                        end_time = timestamp_line.split(' --> ')[1].strip()

                        h, m, s = start_time.split(':')
                        s, ms = s.split(',')
                        start_seconds = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

                        h, m, s = end_time.split(':')
                        s, ms = s.split(',')
                        end_seconds = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

                        text = lines[i+2].strip()
                        transcript.append({
                            "text": text,
                            "start": start_seconds,
                            "duration": end_seconds - start_seconds
                        })

                        i += 4
                    else:
                        i += 1

                progress_file = str(TRANSCRIPTS_DIR / f"{video_id}_whisper_progress.txt")
                with open(progress_file, 'w') as f:
                    f.write(str(progress))

            if not transcript:
                raise RuntimeError(
                    "Whisper completed without any transcript segments. "
                    "The video may not contain recognizable speech."
                )
            speaker_names = {}
            if diarization:
                transcript, speaker_names = self.apply_diarization(video_path, transcript)
            with open(output_path, 'w') as f:
                json.dump(transcript, f)
            with open(str(TRANSCRIPTS_DIR / f"{video_id}_whisper_meta.json"), "w") as f:
                json.dump({
                    "model": model,
                    "prompt": prompt or "",
                    "diarization": bool(diarization),
                    "speaker_names": speaker_names,
                }, f)

            progress_file = str(TRANSCRIPTS_DIR / f"{video_id}_whisper_progress.txt")
            if os.path.exists(progress_file):
                os.remove(progress_file)

        except Exception as e:
            print(f"Error generating Whisper transcript: {str(e)}")
            progress_file = TRANSCRIPTS_DIR / f"{video_id}_whisper_progress.txt"
            if progress_file.exists():
                progress_file.unlink()
            with open(str(TRANSCRIPTS_DIR / f"{video_id}_whisper_error.txt"), 'w') as f:
                f.write(str(e))

    async def get_whisper_status(self, video_id: str):
        """Check the status of Whisper transcript generation."""
        try:
            transcript_path = str(TRANSCRIPTS_DIR / f"{video_id}_whisper.json")
            if os.path.exists(transcript_path):
                try:
                    with open(transcript_path, 'r') as f:
                        transcript = json.load(f)
                except (OSError, json.JSONDecodeError) as error:
                    return JSONResponse({
                        "success": False,
                        "status": "error",
                        "error": f"Whisper transcript file is invalid: {error}"
                    })
                if not isinstance(transcript, list) or not transcript:
                    return JSONResponse({
                        "success": False,
                        "status": "error",
                        "error": "Whisper completed without any transcript segments."
                    })
                return JSONResponse({
                    "success": True,
                    "status": "complete",
                    "transcript": transcript,
                    "model": self._read_whisper_model(video_id)
                    ,"speaker_names": self._read_speaker_names(video_id)
                })

            error_path = str(TRANSCRIPTS_DIR / f"{video_id}_whisper_error.txt")
            if os.path.exists(error_path):
                with open(error_path, 'r') as f:
                    error = f.read()
                return JSONResponse({
                    "success": False,
                    "status": "error",
                    "error": error
                })

            progress_path = str(TRANSCRIPTS_DIR / f"{video_id}_whisper_progress.txt")
            if os.path.exists(progress_path):
                progress_age = time.time() - os.path.getmtime(progress_path)
                if progress_age > self.progress_stale_seconds:
                    stale_message = (
                        "Whisper transcription stopped responding. "
                        "The previous job was marked stale and can be retried."
                    )
                    os.remove(progress_path)
                    with open(str(TRANSCRIPTS_DIR / f"{video_id}_whisper_error.txt"), 'w') as f:
                        f.write(stale_message)
                    return JSONResponse({
                        "success": False,
                        "status": "error",
                        "error": stale_message
                    })
                with open(progress_path, 'r') as f:
                    progress = float(f.read())
                return JSONResponse({
                    "success": True,
                    "status": "in_progress",
                    "progress": progress,
                    "last_updated_seconds_ago": round(progress_age, 1)
                })

            return JSONResponse({
                "success": True,
                "status": "queued"
            })

        except Exception as e:
            return JSONResponse({
                "success": False,
                "status": "error",
                "error": str(e)
            })

    def _read_whisper_model(self, video_id: str):
        metadata_path = TRANSCRIPTS_DIR / f"{video_id}_whisper_meta.json"
        if metadata_path.exists():
            try:
                return json.loads(metadata_path.read_text(encoding="utf-8")).get("model", "turbo")
            except (OSError, json.JSONDecodeError):
                pass
        return "turbo"

    def _read_speaker_names(self, video_id: str):
        metadata_path = TRANSCRIPTS_DIR / f"{video_id}_whisper_meta.json"
        if metadata_path.exists():
            try:
                return json.loads(metadata_path.read_text(encoding="utf-8")).get("speaker_names", {})
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def apply_diarization(self, video_path, transcript):
        """Assign pyannote speaker labels to Whisper segments by timestamp overlap."""
        token = os.getenv("HUGGINGFACE_TOKEN") or os.getenv("HF_TOKEN")
        if not token:
            raise RuntimeError(
                "Speaker identification requires HUGGINGFACE_TOKEN (or HF_TOKEN) "
                "with access to pyannote/speaker-diarization-3.1."
            )
        try:
            from pyannote.audio import Pipeline
        except ImportError as error:
            raise RuntimeError(
                "Speaker identification requires pyannote.audio. Install the project "
                "requirements and retry."
            ) from error
        try:
            pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=token)
            diarization = pipeline(video_path)
        except Exception as error:
            raise RuntimeError(
                f"Speaker identification could not start. Confirm the Hugging Face token "
                f"has accepted the pyannote model terms: {error}"
            ) from error

        speaker_segments = [
            (turn.start, turn.end, speaker)
            for turn, _, speaker in diarization.itertracks(yield_label=True)
        ]
        speakers = sorted({speaker for _, _, speaker in speaker_segments})
        speaker_names = {speaker: speaker.replace("_", " ").title() for speaker in speakers}
        for item in transcript:
            start = float(item.get("start", 0))
            end = start + float(item.get("duration", 0))
            overlaps = {}
            for segment_start, segment_end, speaker in speaker_segments:
                overlap = max(0.0, min(end, segment_end) - max(start, segment_start))
                overlaps[speaker] = overlaps.get(speaker, 0.0) + overlap
            if overlaps:
                item["speaker"] = max(overlaps, key=overlaps.get)
        return transcript, speaker_names

    async def save_speaker_names(self, video_id, names):
        metadata_path = TRANSCRIPTS_DIR / f"{video_id}_whisper_meta.json"
        metadata = {}
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                metadata = {}
        known_speakers = {
            item.get("speaker")
            for item in self._read_transcript(video_id)
            if item.get("speaker")
        }
        cleaned = {
            str(key): str(value).strip()
            for key, value in names.items()
            if str(key) in known_speakers and str(value).strip()
        }
        metadata["speaker_names"] = cleaned
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        return {"success": True, "speaker_names": cleaned}

    def _read_transcript(self, video_id):
        path = TRANSCRIPTS_DIR / f"{video_id}_whisper.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    async def set_preference(self, preference: str):
        """Set the transcript preference."""
        if preference not in ["youtube", "whisper"]:
            return JSONResponse({
                "success": False,
                "error": "Invalid preference"
            })
        
        self.transcript_preference = preference
        return JSONResponse({
            "success": True,
            "message": f"Transcript preference set to {preference}"
        })

    def get_preference(self):
        """Get the current transcript preference."""
        return self.transcript_preference 