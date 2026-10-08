import asyncio
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from processors import video_processor


class SavedVideoTests(unittest.TestCase):
    def test_unicode_saved_data_does_not_depend_on_console_encoding(self):
        from processors.transcript_processor import TranscriptProcessor

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript = [{"start": 0, "duration": 1, "text": "\u0645\u0631\u062d\u0628\u0627"}]
            scenes = [{"time_seconds": 0, "title": "\u0645\u0631\u062d\u0628\u0627"}]
            (root / "video_whisper.json").write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
            (root / "video.json").write_text(json.dumps(scenes, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(video_processor, "TRANSCRIPTS_DIR", root), \
                    mock.patch.object(video_processor, "SCENES_DIR", root), \
                    mock.patch.object(TranscriptProcessor, "start_whisper_generation") as start, \
                    contextlib.redirect_stdout(io.TextIOWrapper(io.BytesIO(), encoding="cp1252")):
                response = asyncio.run(video_processor.VideoProcessor().handle_existing_video(
                    "video", "video.mp4", mock.Mock()
                ))
            payload = getattr(response, "content", None)
            if payload is None:
                payload = json.loads(response.body)
            self.assertTrue(payload["success"])
            self.assertEqual(payload["transcript"], transcript)
            self.assertEqual(payload["scenes"], scenes)
            start.assert_not_called()
