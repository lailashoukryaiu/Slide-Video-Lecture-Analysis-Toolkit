import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import ModuleType


fastapi_module = sys.modules.setdefault("fastapi", ModuleType("fastapi"))
fastapi_responses_module = sys.modules.setdefault(
    "fastapi.responses", ModuleType("fastapi.responses")
)

class FakeJSONResponse:
    def __init__(self, content):
        self.content = content


fastapi_responses_module.JSONResponse = FakeJSONResponse
fastapi_module.responses = fastapi_responses_module

youtube_module = ModuleType("youtube_transcript_api")
youtube_module.YouTubeTranscriptApi = object
sys.modules.setdefault("youtube_transcript_api", youtube_module)

transcribe_module = ModuleType("transcribe")
transcribe_module.transcribe_audio = lambda *args, **kwargs: ()
sys.modules.setdefault("transcribe", transcribe_module)

from processors import transcript_processor as transcript_module
from processors.transcript_processor import TranscriptProcessor


class TranscriptProcessorReuseTests(unittest.TestCase):
    def test_heartbeat_refreshes_progress_marker(self):
        class StopAfterOneHeartbeat:
            def __init__(self):
                self.calls = 0

            def wait(self, timeout):
                self.calls += 1
                return self.calls > 1

        with tempfile.TemporaryDirectory() as directory:
            original_dir = transcript_module.TRANSCRIPTS_DIR
            working_dir = Path(directory)
            transcript_module.TRANSCRIPTS_DIR = working_dir
            try:
                progress_path = working_dir / "video_whisper_progress.txt"
                progress_path.write_text("0", encoding="utf-8")
                old_time = time.time() - 120
                os.utime(progress_path, (old_time, old_time))
                processor = TranscriptProcessor()

                processor._heartbeat_whisper_job(
                    "video", StopAfterOneHeartbeat()
                )

                self.assertLess(time.time() - progress_path.stat().st_mtime, 5)
            finally:
                transcript_module.TRANSCRIPTS_DIR = original_dir

    def test_forced_retry_reclaims_an_old_progress_marker(self):
        class BackgroundTasks:
            def __init__(self):
                self.tasks = []

            def add_task(self, function, *args):
                self.tasks.append((function, args))

        with tempfile.TemporaryDirectory() as directory:
            original_video_dir = transcript_module.VIDEO_DIR
            original_transcript_dir = transcript_module.TRANSCRIPTS_DIR
            working_dir = Path(directory)
            transcript_module.VIDEO_DIR = working_dir
            transcript_module.TRANSCRIPTS_DIR = working_dir
            try:
                (working_dir / "video.mp4").write_bytes(b"video")
                progress_path = working_dir / "video_whisper_progress.txt"
                progress_path.write_text("0", encoding="utf-8")
                old_time = time.time() - 120
                os.utime(progress_path, (old_time, old_time))
                background_tasks = BackgroundTasks()
                processor = TranscriptProcessor()
                processor.manual_retry_stale_seconds = 90

                response = asyncio.run(
                    processor.generate_whisper_transcript(
                        "video", background_tasks, force=True
                    )
                )

                self.assertEqual(response.content["phase"], "starting")
                self.assertEqual(len(background_tasks.tasks), 1)
                self.assertLess(time.time() - progress_path.stat().st_mtime, 5)
            finally:
                transcript_module.VIDEO_DIR = original_video_dir
                transcript_module.TRANSCRIPTS_DIR = original_transcript_dir

    def test_matching_model_and_prompt_allow_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            original_dir = transcript_module.TRANSCRIPTS_DIR
            transcript_module.TRANSCRIPTS_DIR = Path(directory)
            try:
                metadata_path = Path(directory) / "video_whisper_meta.json"
                metadata_path.write_text(
                    json.dumps({
                        "model": "turbo",
                        "prompt": "lecture",
                        "precise_timestamps": False,
                    }),
                    encoding="utf-8",
                )
                processor = TranscriptProcessor()

                self.assertTrue(
                    processor._can_reuse_whisper_transcript(
                        "video", "turbo", " lecture "
                    )
                )
                self.assertFalse(
                    processor._can_reuse_whisper_transcript(
                        "video", "medium", "lecture"
                    )
                )
                self.assertFalse(
                    processor._can_reuse_whisper_transcript(
                        "video", "turbo", "lecture", precise_timestamps=True
                    )
                )
            finally:
                transcript_module.TRANSCRIPTS_DIR = original_dir

    def test_existing_transcript_skips_asr_and_runs_diarization(self):
        with tempfile.TemporaryDirectory() as directory:
            original_dir = transcript_module.TRANSCRIPTS_DIR
            original_transcribe = transcript_module.transcribe_audio
            transcript_module.TRANSCRIPTS_DIR = Path(directory)
            transcript_module.transcribe_audio = self._unexpected_transcription
            try:
                processor = TranscriptProcessor()
                processor.apply_diarization = lambda path, transcript: (
                    [{**item, "speaker": "SPEAKER_00"} for item in transcript],
                    {"SPEAKER_00": "Speaker 00"},
                )
                output_path = Path(directory) / "video_whisper.json"
                existing = [{"text": "Hello", "start": 0, "duration": 1}]
                (Path(directory) / "video_whisper_progress.txt").write_text(
                    "100", encoding="utf-8"
                )

                processor._process_whisper_transcript_sync(
                    "video",
                    "video.mp4",
                    str(output_path),
                    diarization=True,
                    existing_transcript=existing,
                )

                result = json.loads(output_path.read_text(encoding="utf-8"))
                self.assertEqual(result[0]["speaker"], "SPEAKER_00")
                self.assertFalse(
                    (Path(directory) / "video_whisper_progress.txt").exists()
                )
                self.assertFalse(
                    (Path(directory) / "video_whisper_phase.txt").exists()
                )
            finally:
                transcript_module.TRANSCRIPTS_DIR = original_dir
                transcript_module.transcribe_audio = original_transcribe

    @staticmethod
    def _unexpected_transcription(*args, **kwargs):
        raise AssertionError("ASR should not run when reusing a transcript")


if __name__ == "__main__":
    unittest.main()
