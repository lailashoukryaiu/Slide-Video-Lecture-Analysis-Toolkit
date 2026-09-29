import os
import unittest
from unittest import mock

import cloud_transcribe


class RecommendedModelTests(unittest.TestCase):
    def test_gpu_prefers_local_turbo(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "key"}, clear=True):
            self.assertEqual(cloud_transcribe.recommended_transcription_model(True), "turbo")

    def test_cpu_prefers_groq_then_gemini_then_local(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "g", "GOOGLE_API_KEY": "k"}, clear=True):
            self.assertEqual(
                cloud_transcribe.recommended_transcription_model(False),
                "groq:whisper-large-v3-turbo",
            )
        with mock.patch.dict(os.environ, {"GOOGLE_API_KEY": "k"}, clear=True):
            self.assertEqual(
                cloud_transcribe.recommended_transcription_model(False),
                "gemini:gemini-3.8-flash",
            )
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cloud_transcribe.recommended_transcription_model(False), "turbo")


class GeminiParsingTests(unittest.TestCase):
    def test_parses_fenced_json_and_clock_times(self):
        text = '```json\n{"segments": [{"start": "0:05", "end": "0:09.5", "text": " Hi. "},' \
               '{"start": 3, "end": 700, "text": "Later"}, {"start": 1, "text": ""}]}\n```'
        segments = cloud_transcribe.parse_gemini_segments(text, 600)
        self.assertEqual(segments[0], {"start": 5.0, "end": 9.5, "text": "Hi."})
        # Out-of-order starts are kept monotonic and ends are clamped to the clip.
        self.assertEqual(segments[1], {"start": 5.0, "end": 600, "text": "Later"})
        self.assertEqual(len(segments), 2)


class CloudTranscriptionTests(unittest.TestCase):
    def test_parts_are_offset_and_report_progress(self):
        calls = []

        def fake_chunk(audio_path, api_model, api_key, prompt=None, on_step=None):
            calls.append((api_model, api_key, prompt))
            return [{"start": 1.0, "end": 3.5, "text": "Hello."}]

        steps = []
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "secret"}, clear=True), \
                mock.patch.object(cloud_transcribe, "media_duration", return_value=1500), \
                mock.patch.object(cloud_transcribe, "extract_audio_chunk"), \
                mock.patch.object(cloud_transcribe, "_transcribe_groq_chunk", fake_chunk):
            results = list(cloud_transcribe.transcribe_audio_cloud(
                "video.mp4", "groq:whisper-large-v3-turbo", "SAP Fiori", on_step=steps.append
            ))

        self.assertIn("Part 2/2: extracting audio with ffmpeg", steps)
        self.assertTrue(any("Part 1/2: Groq returned 1 segments" in step for step in steps))

        self.assertEqual(calls, [("whisper-large-v3-turbo", "secret", "SAP Fiori")] * 2)
        self.assertEqual(results[0][0], [{"text": "Hello.", "start": 1.0, "duration": 2.5}])
        self.assertEqual(results[1][0][0]["start"], 1201.0)
        self.assertEqual([round(progress) for _, progress in results], [80, 100])

    def test_missing_key_is_reported(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GROQ_API_KEY"):
                list(cloud_transcribe.transcribe_audio_cloud(
                    "video.mp4", "groq:whisper-large-v3"
                ))

    def test_rate_limits_are_retried(self):
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 2:
                raise RuntimeError("Error code: 429 rate limit reached")
            return "ok"

        steps = []
        with mock.patch.object(cloud_transcribe.time, "sleep"):
            self.assertEqual(cloud_transcribe._with_retries(flaky, "Groq", steps.append), "ok")
        self.assertEqual(len(attempts), 2)
        self.assertIn("429 rate limit", steps[0])


if __name__ == "__main__":
    unittest.main()
