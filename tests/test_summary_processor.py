import asyncio
import json
import sys
import unittest
import tempfile
from pathlib import Path
from unittest import mock
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

    def test_fallback_keeps_finished_batches_and_reports_progress(self):
        calls = []

        def complete(provider, model, prompt, temperature, json_output=True, max_tokens=None, fast=False):
            payload = json.loads(prompt.split("\n", 1)[1])
            ids = [item["id"] for item in payload["lines"]]
            calls.append((provider, ids, fast))
            if provider == "gemini" and "segment-2" in ids:
                raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
            return json.dumps({"translations": [
                {"id": item["id"], "text": f"{provider}: {item['text']}"}
                for item in payload["lines"]
            ]})

        processor = SummaryProcessor.__new__(SummaryProcessor)
        processor.model = True
        processor.model_name = "gemini-test"
        processor.openai_client = object()
        processor.openai_model_name = "gpt-test"
        processor._refresh_clients = lambda: None
        processor._complete = complete
        processor._translation_batches = lambda items, *args: [items[i:i + 2] for i in range(0, len(items), 2)]
        progress = []
        original_sleep = asyncio.sleep

        async def no_sleep(seconds):
            return None

        asyncio.sleep = no_sleep
        try:
            with mock.patch("processors.summary_processor.GEMINI_FALLBACK_MODELS", ()):
                result = asyncio.run(processor.translate_transcript(
                    [{"start": i, "duration": 1, "text": f"line {i}"} for i in range(4)],
                    "de", on_progress=progress.append,
                ))
        finally:
            asyncio.sleep = original_sleep

        self.assertEqual(
            [item["text"] for item in result["transcript"]],
            ["gemini: line 0", "gemini: line 1", "openai: line 2", "openai: line 3"],
        )
        self.assertEqual(result["provider"], "OpenAI fallback")
        openai_ids = [ids for provider, ids, _ in calls if provider == "openai"]
        self.assertEqual(openai_ids, [["segment-2", "segment-3"]], "Finished Gemini lines are not translated again")
        self.assertTrue(all(fast for _, _, fast in calls), "Translation asks for minimal reasoning")
        self.assertTrue(any("continuing the remaining 2 lines" in p["message"] for p in progress))
        self.assertEqual(progress[-1]["completed_segments"], 4)
        self.assertEqual(progress[-1]["total_segments"], 4)

    def test_fast_gemini_requests_disable_thinking_and_recover_if_rejected(self):
        configs = []

        class Models:
            def generate_content(self, **kwargs):
                configs.append(kwargs["config"])
                if len(configs) == 1:
                    raise RuntimeError("400 INVALID_ARGUMENT: thinking_level is not supported")
                return SimpleNamespace(text="{}")

        processor = SummaryProcessor.__new__(SummaryProcessor)
        processor.client = SimpleNamespace(models=Models())
        thinking = object()
        with mock.patch.object(SummaryProcessor, "_fast_thinking_config", staticmethod(lambda model: thinking)), \
                mock.patch("processors.summary_processor.types.GenerateContentConfig", lambda **kwargs: kwargs, create=True):
            self.assertEqual(processor._complete("gemini", "gemini-x", "p", 0.2, True, 100, True), "{}")
            processor._complete("gemini", "gemini-x", "p", 0.2, True, 100, True)

        self.assertIs(configs[0]["thinking_config"], thinking)
        self.assertNotIn("thinking_config", configs[1])
        self.assertNotIn("thinking_config", configs[2], "A model that rejects the setting is remembered")


class SummaryProviderFallbackTests(unittest.TestCase):
    def test_summary_language_defaults_to_transcript_and_supports_selection(self):
        self.assertIn("same language", SummaryProcessor._summary_language_instruction("transcript"))
        self.assertIn("German", SummaryProcessor._summary_language_instruction("de"))
        with self.assertRaisesRegex(ValueError, "Unsupported summary language"):
            SummaryProcessor._summary_language_instruction("invalid")

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
        processor._save_chapters = lambda *args: None
        return processor

    def test_summary_default_prefers_groq_and_preserves_explicit_choice(self):
        processor = self._processor(None, None, None)
        self.assertEqual(
            processor._provider_chain(prefer_groq=True)[0], ("groq", "groq-test")
        )
        self.assertEqual(
            processor._provider_chain("gemini-3.8-flash", prefer_groq=True)[0],
            ("gemini", "gemini-3.8-flash"),
        )
        processor.groq_client = None
        self.assertEqual(
            processor._provider_chain(prefer_groq=True)[0],
            ("gemini", "gemini-3.6-flash"),
        )

    def test_selected_language_is_sent_to_summary_provider(self):
        for language, expected in (("transcript", "same language"), ("de", "German")):
            with self.subTest(language=language):
                processor = self._processor(None, None, None)
                prompts = []

                def complete(provider, model, prompt, *args):
                    prompts.append(prompt)
                    return '[{"timestamp": "00:00", "title": "Introduction"}]'

                processor._complete = complete
                response = asyncio.run(processor.generate_summary(
                    [{"start": 0, "text": "Welcome"}], language=language
                ))
                self.assertTrue(response.content["success"], response.content)
                self.assertIn(expected, prompts[0])

    def test_unicode_summary_prompt_uses_utf8_on_windows_and_colab(self):
        processor = self._processor(None, None, None)
        processor._complete = lambda *args: '[{"timestamp": "00:00", "title": "Introduction"}]'
        real_open = open
        with tempfile.TemporaryDirectory() as directory:
            prompt_path = Path(directory) / "debug.txt"

            def windows_open(filename, mode="r", **kwargs):
                if filename == "debug.txt":
                    self.assertEqual(kwargs.get("encoding"), "utf-8")
                    return real_open(prompt_path, mode, **kwargs)
                return real_open(filename, mode, **kwargs)

            text = "Arabic: \u0645\u0631\u062d\u0628\u0627; Chinese: \u4f60\u597d; emoji: \U0001f600"
            with mock.patch("builtins.open", side_effect=windows_open):
                response = asyncio.run(processor.generate_summary(
                    [{"start": 0, "text": text}]
                ))
            self.assertTrue(response.content["success"], response.content)
            self.assertIn(text, prompt_path.read_text(encoding="utf-8"))

    def test_chain_tries_other_gemini_models_then_groq_before_paid_openai(self):
        processor = self._processor(None, None, None)
        self.assertEqual(processor._provider_chain("gemini-2.5-flash"), [
            ("gemini", "gemini-2.5-flash"),
            ("gemini", "gemini-3.6-flash"),
            ("gemini", "gemini-3.8-flash"),
            ("gemini", "gemini-2.5-flash-lite"),
            ("groq", "groq-test"),
            ("openai", "gpt-test"),
        ])

    def test_retired_gemini_model_is_skipped_and_remembered(self):
        class RetiredModel:
            def __init__(self):
                self.models = []

            def generate_content(self, model, **kwargs):
                self.models.append(model)
                if model == "gemini-2.5-flash":
                    raise RuntimeError(
                        "404 NOT_FOUND. This model models/gemini-2.5-flash is no longer "
                        "available to new users."
                    )
                return SimpleNamespace(text='[{"timestamp": "00:00", "title": "Intro"}]')

        gemini = RetiredModel()
        processor = self._processor(gemini, None, None)
        response = asyncio.run(processor.generate_summary(
            [{"start": 0, "duration": 5, "text": "Welcome"}], "video", "gemini-2.5-flash"
        ))

        self.assertTrue(response.content["success"], response.content)
        self.assertEqual(gemini.models, ["gemini-2.5-flash", "gemini-3.6-flash"])
        self.assertNotIn(("gemini", "gemini-2.5-flash"), processor._provider_chain())

    def test_retry_delay_uses_the_wait_the_api_requests(self):
        self.assertEqual(
            SummaryProcessor._retry_delay(RuntimeError("Please try again in 7.5s.")), 8.5
        )
        self.assertEqual(
            SummaryProcessor._retry_delay(RuntimeError("Please try again in 1m2.5s.")), 63.5
        )
        self.assertEqual(
            SummaryProcessor._retry_delay(RuntimeError("try again in 500ms")), 1.5
        )

    def test_long_transcript_is_split_into_parts_for_groq(self):
        from processors import summary_processor as module
        processor = self._processor(None, None, None)
        prompts = []

        def complete(provider, model, prompt, *args):
            self.assertEqual(provider, "groq")
            prompts.append(prompt)
            if len(prompt) > 3000 and len(prompts) == 1:
                raise RuntimeError("Error code: 413 Request too large for model on tokens per minute (TPM)")
            if len(prompts) == 3:
                raise RuntimeError("Error code: 429 rate_limit_exceeded. Please try again in 0.01s.")
            stamp = prompt.split("from ", 1)[1][:8]
            return '{"chapters": [{"timestamp": "%s", "title": "Part at %s"}]}' % (stamp, stamp)

        processor._complete = complete
        transcript = [{"start": index * 10, "text": "word " * 40} for index in range(60)]
        sleeps = []

        async def no_sleep(seconds):
            sleeps.append(seconds)

        with mock.patch.object(module, "GROQ_CHAPTER_CHUNK_CHARS", 6000), \
                mock.patch.object(module.asyncio, "sleep", no_sleep):
            response = asyncio.run(processor.generate_summary(transcript, "video"))

        self.assertTrue(response.content["success"], response.content)
        self.assertEqual(response.content["model"], "groq-test")
        starts = [chapter["timestamp"] for chapter in response.content["chapters"]]
        self.assertEqual(starts[0], "00:00")
        self.assertEqual(len(starts), len(set(starts)))
        self.assertGreaterEqual(len(starts), 3)
        self.assertEqual(len(sleeps), 1)

    def test_chapters_with_sections_merge_short_chapters_and_keep_options(self):
        processor = self._processor(None, None, None)
        prompts = []

        def complete(provider, model, prompt, *args):
            prompts.append(prompt)
            return json.dumps({"chapters": [
                {"timestamp": "00:00:00", "title": "Greeting", "summary": "Welcome."},
                {"timestamp": "00:00:30", "title": "Gradient descent", "summary": "How models learn.",
                 "sections": [
                     {"timestamp": "00:00:40", "title": "Loss functions", "point": "Loss measures error."},
                     {"timestamp": "00:03:00", "title": "Step size", "point": "Small steps are stable."},
                 ]},
                {"timestamp": "00:06:00", "title": "Aside", "summary": "A short remark."},
                {"timestamp": "00:06:20", "title": "Evaluation", "sections": [
                    {"timestamp": "00:06:20", "title": "Test data", "point": "Test data stays unseen."},
                ]},
            ]})

        processor._complete = complete
        transcript = [{"start": index * 10, "duration": 10, "text": "word"} for index in range(300)]
        response = asyncio.run(processor.generate_summary(
            transcript, None, detail="balanced", instructions="Focus on   exam topics",
        ))
        self.assertTrue(response.content["success"], response.content)
        chapters = response.content["chapters"]
        self.assertEqual([chapter["title"] for chapter in chapters], ["Gradient descent", "Evaluation"])
        self.assertEqual(chapters[0]["timestamp"], "00:00")
        self.assertEqual(
            [section["title"] for section in chapters[0]["sections"]],
            ["Greeting", "Loss functions", "Step size", "Aside"],
        )
        self.assertEqual(chapters[0]["sections"][0]["point"], "Welcome.")
        self.assertIn("Focus on exam topics", prompts[0])
        self.assertIn("sections", prompts[0])
        self.assertIn("One section per genuinely separate topic", prompts[0])
        self.assertNotIn("True sections", prompts[0])

    def test_topic_guidance_does_not_add_a_second_ai_pass_or_fixed_limit(self):
        processor = self._processor(None, None, None)
        sections = [
            {"timestamp": SummaryProcessor._stamp(index * 60), "title": f"Topic {index}",
             "point": f"Takeaway {index}."}
            for index in range(33)
        ]
        answers = iter([json.dumps({"chapters": [
            {"timestamp": "00:00:00", "title": "Main topic", "sections": sections},
        ]})])
        prompts = []
        processor._complete = lambda provider, model, prompt, *args: prompts.append(prompt) or next(answers)
        transcript = [{"start": index * 30, "duration": 30, "text": "word"} for index in range(100)]
        response = asyncio.run(processor.generate_summary(transcript, None, detail="balanced"))
        self.assertTrue(response.content["success"], response.content)
        result = response.content["chapters"][0]["sections"]
        self.assertEqual(len(prompts), 1, "No additional review call after generation")
        self.assertEqual(len(result), 33, "Distinct topics must not be arbitrarily capped")
        self.assertIn("actual topic changes, not a fixed quota", prompts[0])
        self.assertEqual(
            " ".join(section["point"] for section in result),
            " ".join(f"Takeaway {index}." for index in range(33)),
        )
        self.assertEqual([section["timestamp"] for section in result],
                         [f"{index:02d}:00" for index in range(33)])

    def test_transcript_continuations_do_not_trigger_an_extra_ai_review(self):
        processor = self._processor(None, None, None)
        sections = [
            {"timestamp": SummaryProcessor._stamp(index * 20), "title": f"Step {index}", "point": f"Fact {index}."}
            for index in range(12)
        ]
        responses = iter([
            json.dumps({"chapters": [{"timestamp": "00:00:00", "title": "Workflow", "sections": sections[:6]}]}),
            json.dumps({"continued_sections": sections[6:], "chapters": []}),
        ])
        prompts = []
        processor._complete = lambda provider, model, prompt, *args: prompts.append(prompt) or next(responses)
        transcript = [{"start": index * 10, "duration": 10, "text": "word " * 20} for index in range(24)]
        with mock.patch("processors.summary_processor.GROQ_CHAPTER_CHUNK_CHARS", 3500):
            response = asyncio.run(processor.generate_summary(transcript, None))
        self.assertTrue(response.content["success"], response.content)
        self.assertEqual(len(prompts), 2)
        result = response.content["chapters"][0]["sections"]
        self.assertEqual(len(result), 12)
        self.assertEqual([section["point"] for section in result],
                         [section["point"] for section in sections])
        self.assertIn("group continued explanations", prompts[1])

    def test_flat_structure_and_detail_level_change_the_prompt(self):
        processor = self._processor(None, None, None)
        prompts = []
        processor._complete = lambda provider, model, prompt, *args: (
            prompts.append(prompt) or '{"chapters": [{"timestamp": "00:00:00", "title": "Intro"}]}'
        )
        transcript = [{"start": index * 60, "duration": 60, "text": "word"} for index in range(36)]
        asyncio.run(processor.generate_summary(transcript, None, structure="flat", detail="brief"))
        asyncio.run(processor.generate_summary(transcript, None, structure="sections", detail="detailed"))
        self.assertIn("about 3 chapters", prompts[0])
        self.assertNotIn('"sections"', prompts[0])
        self.assertIn("about 12 chapters", prompts[1])
        self.assertIn('"sections"', prompts[1])
        response = asyncio.run(processor.generate_summary(transcript, None, detail="huge"))
        self.assertFalse(response.content["success"])

    def test_continued_sections_join_the_previous_chapter(self):
        from processors import summary_processor as module
        processor = self._processor(None, None, None)
        answers = iter([
            '{"chapters": [{"timestamp": "00:00:00", "title": "Basics", "sections": '
            '[{"timestamp": "00:00:00", "title": "Terms", "point": "Terms matter."}]}]}',
            '{"continued_sections": [{"timestamp": "00:05:00", "title": "More terms", "point": "More."}], '
            '"chapters": [{"timestamp": "00:08:00", "title": "Practice", "sections": []}]}',
        ])
        prompts = []
        processor._complete = lambda provider, model, prompt, *args: prompts.append(prompt) or next(answers)
        transcript = [{"start": index * 30, "duration": 30, "text": "word " * 40} for index in range(24)]
        with mock.patch.object(module, "GROQ_CHAPTER_CHUNK_CHARS", 3500):
            response = asyncio.run(processor.generate_summary(transcript, None))
        self.assertTrue(response.content["success"], response.content)
        self.assertIn('ended inside the chapter "Basics"', prompts[1])
        chapters = response.content["chapters"]
        self.assertEqual([section["title"] for section in chapters[0]["sections"]], ["Terms", "More terms"])
        self.assertEqual(chapters[1]["title"], "Practice")

    def test_invalid_json_is_retried_with_low_reasoning_then_split(self):
        from processors import summary_processor as module
        processor = self._processor(None, None, None)
        calls = []

        def complete(provider, model, prompt, temperature, json_output, max_tokens, fast=False):
            calls.append((len(prompt), fast))
            if len(calls) <= 2:
                raise RuntimeError("Error code: 400 json_validate_failed: Failed to generate JSON")
            stamp = prompt.split("from ", 1)[1][:8]
            return '{"chapters": [{"timestamp": "%s", "title": "Topic at %s"}]}' % (stamp, stamp)

        processor._complete = complete
        transcript = [{"start": index * 60, "text": "word " * 20} for index in range(20)]
        with mock.patch.object(module, "GROQ_CHAPTER_CHUNK_CHARS", 100000):
            response = asyncio.run(processor.generate_summary(transcript, "video"))
        self.assertTrue(response.content["success"], response.content)
        self.assertTrue(all(fast for _, fast in calls))
        # Two failures on the whole part, then the two halves succeed.
        self.assertEqual(len(calls), 4)
        self.assertLess(calls[2][0], calls[0][0])
        self.assertEqual(len(response.content["chapters"]), 2)

    def test_chapters_follow_the_open_translation_and_are_saved_with_it(self):
        processor = self._processor(None, None, None)
        saved, prompts = [], []
        processor._save_chapters = lambda *args: saved.append(args)

        def complete(provider, model, prompt, *args):
            prompts.append(prompt)
            return '{"chapters": [{"timestamp": "00:00:00", "title": "Einf\u00fchrung", "summary": "Die Lernenden werden in Python eingef\u00fchrt."}]}'

        processor._complete = complete
        transcript = [{"start": 0, "duration": 5, "text": "Willkommen"}]
        response = asyncio.run(processor.generate_summary(transcript, "video", transcript_language="de"))
        self.assertTrue(response.content["success"], response.content)
        self.assertEqual(response.content["language"], "de")
        self.assertIn("German", prompts[0])
        self.assertTrue(prompts[0].rstrip().endswith("the language of the transcript."))
        self.assertEqual(saved[0][2], "de")
        response = asyncio.run(processor.generate_summary(transcript, "video", transcript_language="xx"))
        self.assertFalse(response.content["success"])

    def test_arabic_transcript_is_detected_and_fillers_are_removed(self):
        arabic = [{"start": 0, "text": "\u0645\u0631\u062d\u0628\u0627 \u0628\u0643\u0645 \u0641\u064a \u0627\u0644\u0645\u062d\u0627\u0636\u0631\u0629"}]
        self.assertEqual(SummaryProcessor._detect_transcript_language(arabic), "ar")
        self.assertIsNone(SummaryProcessor._detect_transcript_language([{"start": 0, "text": "Hello class"}]))
        self.assertIn("Arabic", SummaryProcessor._summary_language_instruction("ar"))
        for text, expected in (
            ("The learner will learn how to submit assignments.", "How to submit assignments."),
            ("You will be introduced to the concept of entropy.", "The concept of entropy."),
            ("This section covers grading rules.", "Grading rules."),
            ("Learners understand that tests matter.", "Tests matter."),
            ("Gradient descent minimizes the loss.", "Gradient descent minimizes the loss."),
        ):
            self.assertEqual(SummaryProcessor._filler_free(text), expected)
        chapters = SummaryProcessor._normalize_chapters([{
            "timestamp": "00:00", "title": "Intro", "summary": "The learner will learn the course rules.",
            "sections": [{"timestamp": "00:00", "title": "Rules", "point": "Students will learn that exams are oral."}],
        }], [])
        self.assertEqual(chapters[0]["summary"], "The course rules.")
        self.assertEqual(chapters[0]["sections"][0]["point"], "Exams are oral.")

    def test_gemini_timeout_moves_on_with_a_clear_reason(self):
        processor = self._processor(None, None, None)
        processor.groq_client = None
        calls = []

        def complete(provider, model, *args):
            calls.append(model)
            if model == "gemini-3.6-flash":
                raise asyncio.TimeoutError()
            return '[{"timestamp": "00:00", "title": "Intro"}]'

        processor._complete = complete
        response = asyncio.run(processor.generate_summary([{"start": 0, "text": "Welcome"}], "video"))
        self.assertTrue(response.content["success"], response.content)
        self.assertEqual(calls[:2], ["gemini-3.6-flash", "gemini-3.8-flash"])

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
        self.assertEqual(gemini.models, ["gemini-3.6-flash", "gemini-3.8-flash"])
        self.assertEqual(response.content["provider"], "Gemini fallback")
        self.assertEqual(response.content["model"], "gemini-3.8-flash")

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
