import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

import numpy as np


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
    model_name = None
    options = None

    def __init__(self, *args, **kwargs):
        self.__class__.model_name = args[0]

    def transcribe(self, file_path, **options):
        if not isinstance(file_path, np.ndarray):
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
        self.__class__.options = options
        return transcript_result()


class FakePipeline:
    options = None

    def __init__(self, model):
        pass

    def transcribe(self, file_path, **options):
        if not isinstance(file_path, np.ndarray):
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
        self.__class__.options = options
        return transcript_result()


def transcript_result():
    segment = SimpleNamespace(
        start=1.25,
        end=3.5,
        text=" Segment text ",
        words=[
            SimpleNamespace(
                start=1.25,
                end=3.5,
                word=" Segment text",
                probability=0.95,
            )
        ],
    )
    return iter([segment]), SimpleNamespace(duration=5)


class TranscribeAudioTests(unittest.TestCase):
    def setUp(self):
        self.audio = np.zeros(16000, dtype=np.float32)
        self.decoder = mock.patch.object(
            transcribe, "decode_audio_samples", return_value=self.audio
        )
        self.mock_decoder = self.decoder.start()
        self.addCleanup(self.decoder.stop)
        self.original_model = transcribe.WhisperModel
        self.original_pipeline = transcribe.BatchedInferencePipeline
        FakeWhisperModel.options = None
        FakePipeline.options = None
        transcribe.WhisperModel = FakeWhisperModel
        transcribe.BatchedInferencePipeline = FakePipeline

    def tearDown(self):
        transcribe.WhisperModel = self.original_model
        transcribe.BatchedInferencePipeline = self.original_pipeline

    def test_cpu_uses_streaming_model_with_vad(self):
        result = list(transcribe.transcribe_audio("video.mp4"))

        self.assertIsNone(FakePipeline.options)
        self.assertTrue(FakeWhisperModel.options["word_timestamps"])
        self.assertTrue(FakeWhisperModel.options["vad_filter"])
        self.assertEqual(FakeWhisperModel.model_name, "turbo")
        self.assertIn("00:00:01,250 --> 00:00:03,500", result[0][0])
        self.assertIn("Segment text", result[0][0])
        self.assertEqual(result[0][1], 70)
        self.mock_decoder.assert_called_once_with("video.mp4")

    def test_gpu_keeps_batch_size_sixteen(self):
        original_device_config = transcribe.get_whisper_device_config
        transcribe.get_whisper_device_config = lambda: ("cuda", "float16")
        try:
            list(transcribe.transcribe_audio("video.mp4"))
        finally:
            transcribe.get_whisper_device_config = original_device_config

        self.assertTrue(FakePipeline.options["word_timestamps"])
        self.assertEqual(FakePipeline.options["batch_size"], 16)
        self.assertNotIn("initial_prompt", FakePipeline.options)
        self.mock_decoder.assert_called_once_with("video.mp4")

    def test_prompt_is_passed_as_initial_prompt(self):
        list(transcribe.transcribe_audio("video.mp4", prompt="SAP Fiori lecture"))
        options = FakePipeline.options or FakeWhisperModel.options
        self.assertEqual(options["initial_prompt"], "SAP Fiori lecture")

    def test_selected_model_is_loaded(self):
        list(transcribe.transcribe_audio("video.mp4", model_name="large-v3"))
        self.assertEqual(FakeWhisperModel.model_name, "large-v3")

    def test_unknown_model_is_rejected(self):
        with self.assertRaises(ValueError):
            list(transcribe.transcribe_audio("video.mp4", model_name="huge"))


if __name__ == "__main__":
    unittest.main()
