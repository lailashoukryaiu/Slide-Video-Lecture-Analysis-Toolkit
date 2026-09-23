import asyncio
import json
import sys
import unittest
from types import ModuleType
from types import SimpleNamespace


fastapi_module = ModuleType("fastapi")
fastapi_responses_module = ModuleType("fastapi.responses")
fastapi_responses_module.JSONResponse = object
fastapi_module.responses = fastapi_responses_module
sys.modules.setdefault("fastapi", fastapi_module)
sys.modules.setdefault("fastapi.responses", fastapi_responses_module)

dotenv_module = ModuleType("dotenv")
dotenv_module.load_dotenv = lambda: None
sys.modules.setdefault("dotenv", dotenv_module)

google_module = ModuleType("google")
google_genai_module = ModuleType("google.genai")
google_genai_types_module = ModuleType("google.genai.types")
google_genai_types_module.GenerateContentConfig = lambda **kwargs: kwargs
google_genai_module.types = google_genai_types_module
google_genai_module.Client = object
google_module.genai = google_genai_module
sys.modules.setdefault("google", google_module)
sys.modules.setdefault("google.genai", google_genai_module)
sys.modules.setdefault("google.genai.types", google_genai_types_module)

from processors.summary_processor import SummaryProcessor


class FakeGeminiModels:
    def __init__(self):
        self.payloads = []

    def generate_content(self, **kwargs):
        payload = json.loads(kwargs["contents"].split("\n", 1)[1])
        self.payloads.append(payload)
        lines = payload["lines"]
        if len(self.payloads) == 1:
            lines = lines[::2]
        translations = [
            {"id": item["id"], "text": f"translated: {item['text']}"}
            for item in lines
        ]
        return SimpleNamespace(text=json.dumps({"translations": translations}))


class CompleteFakeGeminiModels:
    def __init__(self):
        self.payloads = []

    def generate_content(self, **kwargs):
        payload = json.loads(kwargs["contents"].split("\n", 1)[1])
        self.payloads.append(payload)
        return SimpleNamespace(text=json.dumps({
            "translations": [
                {"id": item["id"], "text": f"translated: {item['text']}"}
                for item in payload["lines"]
            ]
        }))


class SummaryProcessorTranslationTests(unittest.TestCase):
    def test_translation_retries_only_missing_ids_and_preserves_fields(self):
        processor = SummaryProcessor.__new__(SummaryProcessor)
        models = FakeGeminiModels()
        processor.client = SimpleNamespace(models=models)
        processor.model = True
        processor.model_name = "gemini-test"
        processor.openai_client = None
        processor.openai_model_name = "gpt-test"
        processor._refresh_gemini_client = lambda: None
        processor._refresh_openai_client = lambda: None
        transcript = [
            {"start": 0, "duration": 2.2, "text": "First", "speaker": "SPEAKER_00"},
            {"start": 2.2, "duration": 3.1, "text": "Second", "speaker": "SPEAKER_01"},
            {"start": 5.3, "duration": 1.5, "text": "Third", "speaker": "SPEAKER_00"},
        ]

        result = asyncio.run(
            processor.translate_transcript(transcript, "de", "gemini-test")
        )

        self.assertEqual(
            [item["text"] for item in result["transcript"]],
            ["translated: First", "translated: Second", "translated: Third"],
        )
        self.assertEqual(
            [item["speaker"] for item in result["transcript"]],
            ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"],
        )
        self.assertEqual(
            [item["id"] for item in models.payloads[0]["lines"]],
            ["segment-0", "segment-1", "segment-2"],
        )
        self.assertEqual(
            [item["id"] for item in models.payloads[1]["lines"]],
            ["segment-1"],
        )
        self.assertEqual(models.payloads[0]["lines"][0]["seconds"], 2.2)

    def test_translation_batches_include_neighboring_context_only(self):
        processor = SummaryProcessor.__new__(SummaryProcessor)
        models = CompleteFakeGeminiModels()
        processor.client = SimpleNamespace(models=models)
        processor.model = True
        processor.model_name = "gemini-test"
        processor.openai_client = None
        processor.openai_model_name = "gpt-test"
        processor._refresh_gemini_client = lambda: None
        processor._refresh_openai_client = lambda: None
        processor._translation_batches = lambda items: [items[:2], items[2:]]
        transcript = [
            {"start": 0, "duration": 1, "text": "First"},
            {"start": 1, "duration": 1, "text": "Second"},
            {"start": 2, "duration": 1, "text": "Third"},
        ]

        asyncio.run(
            processor.translate_transcript(transcript, "de", "gemini-test")
        )

        payloads = sorted(
            models.payloads,
            key=lambda payload: payload["lines"][0]["id"],
        )
        self.assertEqual(payloads[0]["context_before"], [])
        self.assertEqual(payloads[0]["context_after"], ["Third"])
        self.assertEqual(payloads[1]["context_before"], ["First", "Second"])
        self.assertEqual(payloads[1]["context_after"], [])

    def test_translation_batches_respect_item_and_character_limits(self):
        items = [
            {"id": "segment-0", "position": 0, "text": "abcd"},
            {"id": "segment-1", "position": 1, "text": "efgh"},
            {"id": "segment-2", "position": 2, "text": "ij"},
        ]

        batches = SummaryProcessor._translation_batches(
            items, maximum_items=2, maximum_characters=6
        )

        self.assertEqual(
            [[item["id"] for item in batch] for batch in batches],
            [["segment-0"], ["segment-1", "segment-2"]],
        )


if __name__ == "__main__":
    unittest.main()
