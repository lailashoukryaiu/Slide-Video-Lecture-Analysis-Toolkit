import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace


torch_module = ModuleType("torch")
torch_module.cuda = SimpleNamespace(is_available=lambda: False)
sys.modules.setdefault("torch", torch_module)

faster_whisper_module = ModuleType("faster_whisper")
faster_whisper_module.WhisperModel = object
faster_whisper_module.BatchedInferencePipeline = object
sys.modules.setdefault("faster_whisper", faster_whisper_module)

module_spec = importlib.util.spec_from_file_location(
    "transcribe_under_test",
    Path(__file__).resolve().parents[1] / "transcribe.py",
)
transcribe = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(transcribe)


class FakeWhisperModel:
    def __init__(self, *args, **kwargs):
        pass


class FakePipeline:
    options = None

    def __init__(self, model):
        pass

    def transcribe(self, file_path, **options):
        self.__class__.options = options
        segment = SimpleNamespace(
            start=1.25,
            end=3.5,
            text=" Segment text ",
            words=None,
        )
        return iter([segment]), SimpleNamespace(duration=5)


class TranscribeAudioTests(unittest.TestCase):
    def setUp(self):
        self.original_model = transcribe.WhisperModel
        self.original_pipeline = transcribe.BatchedInferencePipeline
        transcribe.WhisperModel = FakeWhisperModel
        transcribe.BatchedInferencePipeline = FakePipeline

    def tearDown(self):
        transcribe.WhisperModel = self.original_model
        transcribe.BatchedInferencePipeline = self.original_pipeline

    def test_fast_mode_uses_segment_timestamps_without_word_alignment(self):
        result = list(transcribe.transcribe_audio("video.mp4"))

        self.assertFalse(FakePipeline.options["word_timestamps"])
        self.assertIn("00:00:01,250 --> 00:00:03,500", result[0][0])
        self.assertIn("Segment text", result[0][0])
        self.assertEqual(result[0][1], 70)


if __name__ == "__main__":
    unittest.main()
