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

    def test_gemini_quota_error_falls_back_to_openai(self):
        class ExhaustedGemini:
            def generate_content(self, **kwargs):
                raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")

        class FakeOpenAICompletions:
            def create(self, **kwargs):
                payload = json.loads(kwargs["messages"][0]["content"].split("\n", 1)[1])
                content = json.dumps({"translations": [
                    {"id": item["id"], "text": f"übersetzt: {item['text']}"}
                    for item in payload["lines"]
                ]})
                return SimpleNamespace(choices=[
                    SimpleNamespace(message=SimpleNamespace(content=content))
                ])

        processor = SummaryProcessor.__new__(SummaryProcessor)
        processor.client = SimpleNamespace(models=ExhaustedGemini())
        processor.model = True
        processor.model_name = "gemini-test"
        processor.openai_client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeOpenAICompletions())
        )
        processor.openai_model_name = "gpt-test"
        processor._refresh_gemini_client = lambda: None
        processor._refresh_openai_client = lambda: None
        original_sleep = asyncio.sleep

        async def no_sleep(seconds):
            return None

        asyncio.sleep = no_sleep
        try:
            result = asyncio.run(processor.translate_transcript(
                [{"start": 0, "duration": 1, "text": "Hello"}], "de"
            ))
        finally:
            asyncio.sleep = original_sleep

        self.assertEqual(result["provider"], "OpenAI fallback")
        self.assertEqual(result["model"], "gpt-test")
        self.assertEqual(result["transcript"][0]["text"], "übersetzt: Hello")

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


class SummaryProviderFallbackTests(unittest.TestCase):
    def setUp(self):
        class FakeJSONResponse:
            def __init__(self, content):
                self.content = content

        from processors import summary_processor as summary_module
        self._module = summary_module
        self._original_response = summary_module.JSONResponse
        summary_module.JSONResponse = FakeJSONResponse

    def tearDown(self):
        self._module.JSONResponse = self._original_response

    def _processor(self, gemini_models, groq_completions, openai_completions):
        processor = SummaryProcessor.__new__(SummaryProcessor)
        processor.client = SimpleNamespace(models=gemini_models)
        processor.model = True
        processor.model_name = "gemini-3.6-flash"
        processor.groq_client = SimpleNamespace(chat=SimpleNamespace(completions=groq_completions))
        processor.groq_model_name = "groq-test"
        processor.openai_client = SimpleNamespace(chat=SimpleNamespace(completions=openai_completions))
        processor.openai_model_name = "gpt-test"
        processor._refresh_gemini_client = lambda: None
        processor._refresh_openai_client = lambda: None
        processor._refresh_groq_client = lambda: None
        processor._save_chapters = lambda video_id, chapters: None
        return processor

    def test_chain_tries_other_gemini_models_then_groq_before_paid_openai(self):
        processor = self._processor(None, None, None)
        self.assertEqual(processor._provider_chain("gemini-2.5-flash"), [
            ("gemini", "gemini-2.5-flash"),
            ("gemini", "gemini-3.6-flash"),
            ("gemini", "gemini-2.5-flash-lite"),
            ("groq", "groq-test"),
            ("openai", "gpt-test"),
        ])

    def test_summary_falls_back_to_second_gemini_model(self):
        class QuotaOnFirstModel:
            def __init__(self):
                self.models = []

            def generate_content(self, model, **kwargs):
                self.models.append(model)
                if model == "gemini-3.6-flash":
                    raise RuntimeError("429 RESOURCE_EXHAUSTED")
                return SimpleNamespace(text='[{"timestamp": "00:00", "title": "Intro"}]')

        gemini = QuotaOnFirstModel()
        processor = self._processor(gemini, None, None)
        response = asyncio.run(processor.generate_summary(
            [{"start": 0, "duration": 5, "text": "Welcome"}], "video", "gemini-3.6-flash"
        ))

        self.assertTrue(response.content["success"], response.content)
        self.assertEqual(gemini.models, ["gemini-3.6-flash", "gemini-2.5-flash"])
        self.assertEqual(response.content["provider"], "Gemini fallback")
        self.assertEqual(response.content["model"], "gemini-2.5-flash")

    def test_summary_reports_every_exhausted_provider(self):
        class Exhausted:
            def generate_content(self, **kwargs):
                raise RuntimeError("429 quota exceeded")

            def create(self, **kwargs):
                raise RuntimeError("Error code: 429 insufficient_quota")

        exhausted = Exhausted()
        processor = self._processor(exhausted, exhausted, exhausted)
        response = asyncio.run(processor.generate_summary(
            [{"start": 0, "duration": 5, "text": "Welcome"}], "video"
        ))

        self.assertFalse(response.content["success"])
        self.assertIn("Every configured AI model", response.content["error"])
        self.assertIn("Groq groq-test", response.content["error"])


if __name__ == "__main__":
    unittest.main()
