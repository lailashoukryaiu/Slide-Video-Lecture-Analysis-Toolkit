import subprocess
import unittest
from unittest import mock

import numpy as np

from audio_decoder import decode_audio_samples


class AudioDecoderTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch("audio_decoder.media_executable", return_value="ffmpeg")
        patch.start()
        self.addCleanup(patch.stop)

    def test_decodes_writable_mono_samples(self):
        samples = np.array([-0.5, 0, 0.5], dtype="<f4")
        with mock.patch("audio_decoder.subprocess.run") as run:
            run.return_value.stdout = samples.tobytes()
            result = decode_audio_samples("video.mp4")
        np.testing.assert_array_equal(result, samples)
        self.assertEqual(result.dtype, np.float32)
        self.assertTrue(result.flags.writeable)
        args, kwargs = run.call_args
        self.assertEqual(args[0], [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-i", "video.mp4", "-vn", "-ac", "1", "-ar", "16000",
            "-f", "f32le", "pipe:1",
        ])
        self.assertTrue(kwargs["check"])

    def test_empty_audio_is_reported(self):
        with mock.patch("audio_decoder.subprocess.run") as run:
            run.return_value.stdout = b""
            with self.assertRaisesRegex(RuntimeError, "no decodable audio"):
                decode_audio_samples("silent.mp4")

    def test_missing_ffmpeg_is_reported(self):
        with mock.patch("audio_decoder.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(RuntimeError, "Install FFmpeg"):
                decode_audio_samples("video.mp4")

    def test_decoder_error_includes_ffmpeg_stderr(self):
        error = subprocess.CalledProcessError(1, "ffmpeg", stderr=b"Invalid input")
        with mock.patch("audio_decoder.subprocess.run", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "Invalid input"):
                decode_audio_samples("broken.mp4")
