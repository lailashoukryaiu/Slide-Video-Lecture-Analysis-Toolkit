import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


fastapi_module = sys.modules.setdefault("fastapi", ModuleType("fastapi"))
fastapi_responses_module = sys.modules.setdefault(
    "fastapi.responses", ModuleType("fastapi.responses")
)
fastapi_responses_module.JSONResponse = object
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
    def test_matching_model_and_prompt_allow_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            original_dir = transcript_module.TRANSCRIPTS_DIR
            transcript_module.TRANSCRIPTS_DIR = Path(directory)
            try:
                metadata_path = Path(directory) / "video_whisper_meta.json"
                metadata_path.write_text(
                    json.dumps({"model": "turbo", "prompt": "lecture"}),
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
