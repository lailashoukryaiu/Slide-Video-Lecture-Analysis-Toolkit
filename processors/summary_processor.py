import os
import json
import re
import asyncio
import time
from fastapi.responses import JSONResponse
from google import genai
from google.genai import types
from dotenv import load_dotenv
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

from project_paths import SUMMARIES_DIR

# Gemini's free-tier quotas are counted per model, so another model often
# still has quota when the selected one is exhausted.
GEMINI_FALLBACK_MODELS = (
    "gemini-3.8-flash", "gemini-3.6-flash", "gemini-2.5-flash-lite", "gemini-2.5-flash",
)
# Gemini retires older models for new API keys with 404 NOT_FOUND; skip those.
MODEL_UNAVAILABLE_MARKERS = (
    "404", "not_found", "not found", "no longer available", "model_not_found",
    "does not exist", "decommissioned", "deprecated",
)
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
PROVIDER_LABELS = {"gemini": "Gemini", "groq": "Groq", "openai": "OpenAI"}
# Concurrent translation requests for Gemini/OpenAI; Groq stays at one.
# A model that ran out of quota is skipped by automatic selection for a while.
QUOTA_COOLDOWN_SECONDS = max(60, int(os.getenv("AI_QUOTA_COOLDOWN_SECONDS", "3600")))
CHAPTER_TIMEOUT_SECONDS = 90
# About 4,000 tokens: one part plus its answer fits Groq's free 8,000 tokens per minute.
GROQ_CHAPTER_CHUNK_CHARS = 16000
TRANSLATION_CONCURRENCY = max(1, int(os.getenv("TRANSLATION_CONCURRENCY", "4")))

class SummaryProcessor:
    def __init__(self):
        # Initialize Gemini API
        load_dotenv()

        # Prefer the environment variable for normal/local execution.
        GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

        # Colab Secrets are available only from the interactive notebook
        # process. The server process receives the key through the environment.
        # Do not call google.colab.userdata from the FastAPI/Uvicorn process.

        self.client = None
        self.model = None
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        self.unavailable_models = set()
        self.quota_exhausted_until = {}
        self.openai_client = None
        self.openai_model_name = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        self.groq_client = None
        self.groq_model_name = os.getenv("GROQ_LLM_MODEL", "openai/gpt-oss-120b")
        self._refresh_openai_client()
        self._refresh_groq_client()

        try:
            if not GOOGLE_API_KEY:
                raise ValueError("GOOGLE_API_KEY is not configured")

            self.client = genai.Client(api_key=GOOGLE_API_KEY)
            self.model = True
        except Exception as e:
            print(f"Warning: Gemini API initialization failed: {str(e)}")
            self.model = None

    def _refresh_gemini_client(self):
        """Load a Gemini client if a key was configured after startup."""
        api_key = os.getenv("GOOGLE_API_KEY")
        if api_key and self.client is None:
            try:
                self.client = genai.Client(api_key=api_key)
                self.model = True
            except Exception as error:
                print(f"Warning: Gemini API initialization failed: {error}")

    def _refresh_openai_client(self):
        """Load an OpenAI client if a key was configured after startup."""
        api_key = os.getenv("OPENAI_API_KEY")
        if OpenAI and api_key and self.openai_client is None:
            try:
                self.openai_client = OpenAI(api_key=api_key)
            except Exception as error:
                print(f"Warning: OpenAI API initialization failed: {error}")

    def _refresh_groq_client(self):
        """Load a Groq client (OpenAI-compatible) if GROQ_API_KEY is configured."""
        api_key = os.getenv("GROQ_API_KEY")
        if OpenAI and api_key and getattr(self, "groq_client", None) is None:
            try:
                self.groq_client = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL)
            except Exception as error:
                print(f"Warning: Groq API initialization failed: {error}")

    def _refresh_clients(self):
        for refresh in (
            getattr(self, "_refresh_gemini_client", None),
            getattr(self, "_refresh_openai_client", None),
            getattr(self, "_refresh_groq_client", None),
        ):
            if refresh:
                refresh()

    @staticmethod
    def _provider_chain_entry(requested_model):
        if not requested_model:
            return None
        if requested_model.startswith("gpt-"):
            return ("openai", requested_model)
        if requested_model.startswith("groq:"):
            return ("groq", requested_model[len("groq:"):])
        return ("gemini", requested_model)

    def _provider_chain(self, requested_model=None, prefer_groq=False):
        """Return (provider, model) pairs to try, in order, skipping unconfigured ones."""
        configured = {
            "gemini": bool(self.model),
            "groq": bool(getattr(self, "groq_client", None)),
            "openai": bool(getattr(self, "openai_client", None)),
        }
        chain = []
        unavailable = getattr(self, "unavailable_models", set())
        now = time.time()
        exhausted = {
            pair for pair, until in getattr(self, "quota_exhausted_until", {}).items()
            if until > now
        }
        cooling = []

        def add(provider, model):
            if (
                model and configured[provider] and (provider, model) not in chain
                and (provider, model) not in unavailable
            ):
                if (provider, model) in exhausted:
                    cooling.append((provider, model))
                else:
                    chain.append((provider, model))

        if requested_model:
            requested = self._provider_chain_entry(requested_model)
            if requested not in unavailable and configured.get(requested[0]) and requested[1]:
                # An explicitly chosen model is always tried first.
                chain.append(requested)
        if prefer_groq and not requested_model:
            add("groq", getattr(self, "groq_model_name", None))
        add("gemini", self.model_name)
        for model in GEMINI_FALLBACK_MODELS:
            add("gemini", model)
        add("groq", getattr(self, "groq_model_name", None))
        add("openai", getattr(self, "openai_model_name", None))
        # Out-of-quota models stay as a last resort in case their quota has reset.
        return chain + [pair for pair in cooling if pair not in chain]

    def _mark_quota_exhausted(self, provider, model):
        if not hasattr(self, "quota_exhausted_until"):
            self.quota_exhausted_until = {}
        self.quota_exhausted_until[(provider, model)] = time.time() + QUOTA_COOLDOWN_SECONDS
        print(
            f"{PROVIDER_LABELS[provider]} {model} is out of quota; automatic selection "
            f"skips it for {QUOTA_COOLDOWN_SECONDS // 60} minutes"
        )

    @staticmethod
    def _fast_thinking_config(model):
        """Minimal reasoning for simple tasks such as translation, if the SDK supports it."""
        thinking_config = getattr(types, "ThinkingConfig", None)
        if thinking_config is None:
            return None
        fields = getattr(thinking_config, "model_fields", {})
        try:
            if model.startswith("gemini-2.5") and "thinking_budget" in fields:
                return thinking_config(thinking_budget=0)
            if "thinking_level" in fields:
                return thinking_config(thinking_level="minimal")
            if "thinking_budget" in fields:
                return thinking_config(thinking_budget=0)
        except Exception:
            return None
        return None

    def _complete(
        self, provider, model, prompt, temperature, json_output=True, max_tokens=None, fast=False,
    ):
        """Run one prompt against one provider and return the response text.

        fast=True turns model reasoning down; it is used for translation, which
        does not benefit from long thinking but pays for it in latency.
        """
        if provider == "gemini":
            config = {"temperature": temperature}
            if json_output:
                config["response_mime_type"] = "application/json"
            if max_tokens:
                config["max_output_tokens"] = max_tokens
            no_thinking_control = getattr(self, "_no_thinking_control", set())
            thinking = self._fast_thinking_config(model) if fast and model not in no_thinking_control else None
            if thinking is not None:
                try:
                    response = self.client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=types.GenerateContentConfig(**config, thinking_config=thinking),
                    )
                    return response.text
                except Exception as error:
                    if "thinking" not in str(error).lower():
                        raise
                    # This model rejects the reasoning setting; use its default from now on.
                    self._no_thinking_control = no_thinking_control | {model}
            response = self.client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(**config),
            )
            return response.text
        client = self.groq_client if provider == "groq" else self.openai_client
        options = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if json_output:
            options["response_format"] = {"type": "json_object"}
        if max_tokens:
            options["max_completion_tokens"] = max_tokens
        if fast and provider == "groq" and "gpt-oss" in model:
            options["extra_body"] = {"reasoning_effort": "low"}
        response = client.chat.completions.create(**options)
        return response.choices[0].message.content

    @staticmethod
    def _provider_label(provider, index):
        label = PROVIDER_LABELS[provider]
        return label if index == 0 else f"{label} fallback"

    @staticmethod
    def _no_provider_message():
        return (
            "No AI model is configured. Set GOOGLE_API_KEY (free), GROQ_API_KEY (free) "
            "or OPENAI_API_KEY in the Colab runtime and restart the server."
        )
    @staticmethod
    def _summary_language_instruction(language):
        languages = {"en": "English", "de": "German", "fr": "French", "es": "Spanish", "ar": "Arabic"}
        if language in (None, "", "transcript"):
            return (
                "Write all chapter titles and summary text in the same language as "
                "the supplied transcript. Infer its predominant language from its text; "
                "do not translate it into English."
            )
        if language not in languages:
            raise ValueError(f"Unsupported summary language: {language}")
        return f"Write all chapter titles and summary text in {languages[language]}."

    async def generate_summary(
        self, transcript: list, video_id: str = None, requested_model: str = None,
        language: str = "transcript",
    ):
        """Generate chapters, preferring configured Groq unless a model is selected."""
        try:
            language_instruction = self._summary_language_instruction(language)
            self._refresh_clients()
            chain = self._provider_chain(requested_model, prefer_groq=True)
            if not transcript or not chain:
                return JSONResponse({
                    "success": False,
                    "error": "No transcript is available." if not transcript
                    else self._no_provider_message(),
                })

            # Combine transcript text with timestamps
            full_text = ""
            for item in transcript:
                hours = int(item["start"] // 3600)
                minutes = int((item["start"] % 3600) // 60)
                seconds = int(item["start"] % 60)
                timestamp = f"{hours:02d}:{minutes:02d}:{seconds:02d}: "
                full_text += timestamp + item["text"] + "\n"
            

            prompt = f"""{language_instruction}
Based on the following transcript with timestamps, create chapters that outline the main topics.
For each chapter, provide:
The timestamp where the chapter starts (in MM:SS format)
A concise title of at most 5 words: a specific noun phrase naming the topic, not a sentence, with no trailing punctuation and no filler such as "Introduction to" or "Discussion of"
Format each chapter exactly like this example:
[
{{"timestamp": "00:00", "title": "Course overview"}},
{{"timestamp": "02:30", "title": "Gradient descent"}},
{{"timestamp": "05:45", "title": "Worked examples"}}
]
{full_text}"""

            # dump prompt into a debug file
            with open('debug.txt', 'w', encoding='utf-8') as f:
                f.write(prompt)
            response_text = None
            failures = []
            provider = None
            used_model = None
            for index, (candidate, model) in enumerate(chain):
                try:
                    if candidate == "groq" and len(full_text) > GROQ_CHAPTER_CHUNK_CHARS:
                        # Groq's free tier rejects long prompts (tokens per minute),
                        # so long lectures are split into parts that each fit.
                        response_text = await self._chunked_chapters(
                            candidate, model, transcript, language_instruction,
                        )
                    else:
                        response_text = await asyncio.wait_for(
                            asyncio.to_thread(
                                self._complete, candidate, model, prompt, 0.5,
                                candidate == "gemini",
                                2048 if candidate == "groq" else None,
                            ),
                            timeout=CHAPTER_TIMEOUT_SECONDS,
                        )
                    provider = self._provider_label(candidate, index)
                    used_model = model
                    break
                except Exception as error:
                    if isinstance(error, asyncio.TimeoutError):
                        error = RuntimeError(
                            f"no answer within {CHAPTER_TIMEOUT_SECONDS} seconds (the model is busy or slow)"
                        )
                    failures.append(f"{PROVIDER_LABELS[candidate]} {model}: {error}")
                    if "timed out" in str(error).lower() or "no answer within" in str(error):
                        continue
                    if self._is_model_unavailable(error):
                        self._mark_unavailable(candidate, model)
                        continue
                    if "resource_exhausted" in str(error).lower():
                        self._mark_quota_exhausted(candidate, model)
                    if not self._is_quota_error(error):
                        return JSONResponse({
                            "success": False,
                            "error": f"{PROVIDER_LABELS[candidate]} {model} could not generate chapters: {error}",
                        })
            if response_text is None:
                return JSONResponse({
                    "success": False,
                    "error": "Every configured AI model is out of quota, busy or unavailable. "
                             "Wait a while, or add GROQ_API_KEY (free) as another fallback. "
                             "Details: " + " | ".join(failures),
                })
            try:
                # Try to parse the response as JSON
                chapters = json.loads(response_text)
                if isinstance(chapters, dict) and isinstance(chapters.get("chapters"), list):
                    chapters = chapters["chapters"]
            except json.JSONDecodeError:
                # If parsing fails, try to extract JSON from the response text
                match = re.search(r'\[.*\]', response_text.replace('\n', ' '), re.DOTALL)
                if match:
                    chapters = json.loads(match.group())
                else:
                    raise ValueError("Could not parse Gemini response as JSON")
            
            if isinstance(chapters, list) and chapters and isinstance(chapters[0], list):
                chapters = [item for sublist in chapters for item in sublist]

            chapters = self._normalize_chapters(chapters, transcript)
            self._save_chapters(video_id, chapters)

            return JSONResponse({
                "success": True,
                "chapters": chapters,
                "provider": provider,
                "model": used_model,
                "notice": (
                    f"The first model was unavailable, out of quota or busy; {provider.replace(' fallback', '')} "
                    f"{used_model} generated these chapters."
                ) if provider.endswith("fallback") else None
            })

        except Exception as e:
            return JSONResponse({
                "success": False,
                "error": str(e)
            })

    @staticmethod
    def _parse_chapter_list(response_text):
        try:
            chapters = json.loads(response_text)
        except json.JSONDecodeError:
            match = re.search(r'\[.*\]', response_text.replace('\n', ' '), re.DOTALL)
            if not match:
                raise ValueError("Could not parse the AI response as JSON")
            chapters = json.loads(match.group())
        if isinstance(chapters, dict):
            chapters = next((value for value in chapters.values() if isinstance(value, list)), [])
        if isinstance(chapters, list) and chapters and isinstance(chapters[0], list):
            chapters = [item for sublist in chapters for item in sublist]
        return [item for item in chapters if isinstance(item, dict)]

    async def _chunked_chapters(self, provider, model, transcript, language_instruction):
        """Generate chapters part by part and return them as one JSON list."""
        def stamp(seconds):
            seconds = int(seconds)
            return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"

        parts, current, size = [], [], 0
        for item in transcript:
            line = f"{stamp(item['start'])}: {item['text']}\n"
            if current and size + len(line) > GROQ_CHAPTER_CHUNK_CHARS:
                parts.append(current)
                current, size = [], 0
            current.append(line)
            size += len(line)
        if current:
            parts.append(current)

        chapters = []
        number = 0
        while parts:
            lines = parts.pop(0)
            number += 1
            total = number + len(parts)
            prompt = f"""{language_instruction}
This is part {number} of {total} of one lecture transcript, from {lines[0][:8]} to {lines[-1][:8]}.
Create 2 to 6 chapters that outline the main topics of this part only.
For each chapter, provide the timestamp where it starts (HH:MM:SS, taken from the transcript) and
a concise title of at most 5 words: a specific noun phrase naming the topic, not a sentence, with no trailing punctuation and no filler such as "Introduction to" or "Discussion of".
Return JSON exactly like this: {{"chapters": [{{"timestamp": "00:00:00", "title": "Course overview"}}]}}
{''.join(lines)}"""
            text = None
            for attempt in range(6):
                try:
                    text = await asyncio.wait_for(
                        asyncio.to_thread(self._complete, provider, model, prompt, 0.5, True, 1500),
                        timeout=CHAPTER_TIMEOUT_SECONDS,
                    )
                    break
                except Exception as error:
                    message = str(error).lower()
                    if "request too large" in message and len(lines) > 1:
                        # Dense scripts use more tokens per character; split this part again.
                        middle = len(lines) // 2
                        parts[:0] = [lines[:middle], lines[middle:]]
                        number -= 1
                        break
                    if "request too large" in message or attempt == 5 or not self._is_quota_error(error):
                        raise
                    delay = min(self._retry_delay(error) or 20.0, 65.0)
                    print(f"{PROVIDER_LABELS[provider]} rate limit while generating chapters "
                          f"(part {number}/{total}); waiting {delay:.0f}s")
                    await asyncio.sleep(delay)
            if text is not None:
                chapters.extend(self._parse_chapter_list(text))
        return json.dumps(chapters)

    async def translate_transcript(
        self, transcript, target_language, requested_model=None, _chain=None, _chain_offset=0,
        on_progress=None, _done=None, _started_at=None,
    ):
        """Translate transcript segments in bounded concurrent batches.

        Batches that already succeeded are kept when falling back to another model.
        """
        if _chain is None:
            self._refresh_clients()
            _chain = self._provider_chain(requested_model, prefer_groq=True)
            requested_pair = self._provider_chain_entry(requested_model)
            if (
                requested_model
                and requested_pair not in getattr(self, "unavailable_models", set())
                and (not _chain or _chain[0] != requested_pair)
            ):
                raise RuntimeError(
                    f"Model {requested_model} was selected, but its API key is not configured."
                )
        if not _chain:
            raise RuntimeError(self._no_provider_message())
        provider, selected_model = _chain[0]
        done = {} if _done is None else _done
        started_at = _started_at or time.monotonic()

        translation_items = [
            {
                "id": f"segment-{index}",
                "position": index,
                "text": str(item.get("text", "")),
                "seconds": round(float(item.get("duration", 0) or 0), 1),
            }
            for index, item in enumerate(transcript)
            if isinstance(item, dict)
        ]
        if len(translation_items) != len(transcript):
            raise ValueError("Every transcript segment must be an object")
        remaining_items = [item for item in translation_items if item["id"] not in done]

        batches = (
            self._translation_batches(remaining_items, 30, 2500)
            if provider == "groq"
            else self._translation_batches(remaining_items)
        )
        # Groq's free tier counts the requested output tokens against a small
        # per-minute budget, so send smaller batches one at a time.
        semaphore = asyncio.Semaphore(1 if provider == "groq" else TRANSLATION_CONCURRENCY)
        max_output_tokens = 3000 if provider == "groq" else 8192
        attempts = 4 if provider == "groq" else 2
        context_lines = 4
        total_segments = len(translation_items)
        model_label = f"{PROVIDER_LABELS[provider]} {selected_model}"
        abort = {"error": None}
        finished_batches = {"count": 0}

        def report(message):
            if not on_progress:
                return
            try:
                on_progress({
                    "completed_segments": len(done),
                    "total_segments": total_segments,
                    "completed_batches": finished_batches["count"],
                    "total_batches": len(batches),
                    "provider": PROVIDER_LABELS[provider],
                    "model": selected_model,
                    "elapsed_seconds": round(time.monotonic() - started_at, 1),
                    "message": message,
                })
            except Exception as error:
                print(f"Translation progress callback failed: {error}")

        report(
            f"Translating {len(remaining_items)} lines in {len(batches)} batches with {model_label}"
            + (f" ({len(done)} lines already done)" if done else "")
        )

        async def translate_batch(batch, batch_number):
            async with semaphore:
                if abort["error"] is not None:
                    return
                start = batch[0]["position"]
                end = batch[-1]["position"] + 1
                context_before = [
                    item["text"]
                    for item in translation_items[max(0, start - context_lines):start]
                ]
                context_after = [
                    item["text"]
                    for item in translation_items[end:end + context_lines]
                ]

                async def request_translation(
                    requested_items, request_label, retry_invalid_response=True
                ):
                    payload = {
                        "context_before": context_before,
                        "lines": [
                            {
                                "id": item["id"],
                                "text": item["text"],
                                "seconds": item["seconds"],
                            }
                            for item in requested_items
                        ],
                        "context_after": context_after,
                    }
                    prompt = (
                        f"Translate every item in lines into language code {target_language}. "
                        "Use context_before and context_after only to understand the surrounding "
                        "meaning; do not translate or return those context lines. Preserve terminology "
                        "and fit each translation naturally within its approximate speaking duration. "
                        "Return every line ID exactly once. Never merge or split lines. Return only a "
                        "JSON object with a translations array containing objects with exactly these "
                        "fields: id and text.\n"
                        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                    )
                    raw_result = None
                    last_error = None
                    for attempt in range(attempts):
                        try:
                            raw_result = await asyncio.wait_for(
                                asyncio.to_thread(
                                    self._complete, provider, selected_model, prompt,
                                    0.2, True, max_output_tokens, True,
                                ),
                                timeout=120,
                            )
                            break
                        except Exception as error:
                            last_error = error
                            if self._is_model_unavailable(error):
                                break
                            if attempt < attempts - 1:
                                await asyncio.sleep(
                                    self._retry_delay(error) if provider == "groq" else 1
                                )
                    if raw_result is None:
                        raise RuntimeError(
                            f"Translation batch {batch_number} {request_label} request failed "
                            f"after retry: {last_error}"
                        ) from last_error

                    try:
                        parsed = json.loads(raw_result)
                    except (TypeError, json.JSONDecodeError) as error:
                        if retry_invalid_response:
                            await asyncio.sleep(1)
                            return await request_translation(
                                requested_items,
                                f"{request_label}-response-retry",
                                False,
                            )
                        raise ValueError(
                            f"Translation batch {batch_number} returned invalid JSON"
                        ) from error
                    if isinstance(parsed, dict):
                        parsed = parsed.get("translations")
                    if not isinstance(parsed, list):
                        if retry_invalid_response:
                            await asyncio.sleep(1)
                            return await request_translation(
                                requested_items,
                                f"{request_label}-response-retry",
                                False,
                            )
                        raise ValueError(
                            f"Translation batch {batch_number} returned an invalid response"
                        )
                    expected = {item["id"] for item in requested_items}
                    return {
                        str(item["id"]): str(item["text"]).strip()
                        for item in parsed
                        if (
                            isinstance(item, dict)
                            and str(item.get("id", "")) in expected
                            and str(item.get("text", "")).strip()
                        )
                    }

                try:
                    translated_by_id = await request_translation(batch, "initial")
                    missing = [
                        item for item in batch
                        if item["id"] not in translated_by_id
                    ]
                    if missing:
                        translated_by_id.update(
                            await request_translation(missing, "missing-segment")
                        )
                    still_missing = [
                        item["id"] for item in batch
                        if item["id"] not in translated_by_id
                    ]
                    if still_missing:
                        raise RuntimeError(
                            f"Translation batch {batch_number} omitted "
                            f"{len(still_missing)} segment(s) after a targeted retry"
                        )
                except Exception as error:
                    if abort["error"] is None:
                        abort["error"] = error
                    raise
                done.update(translated_by_id)
                finished_batches["count"] += 1
                report(
                    f"Translated batch {finished_batches['count']} of {len(batches)} "
                    f"with {model_label} \u2014 {len(done)} of {total_segments} lines done"
                )

        results = await asyncio.gather(
            *(
                translate_batch(batch, batch_number)
                for batch_number, batch in enumerate(batches, start=1)
            ),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            error = abort["error"] or errors[0]
            unavailable = self._is_model_unavailable(error)
            if unavailable:
                self._mark_unavailable(provider, selected_model)
            elif self._is_quota_error(error) and "resource_exhausted" in str(error).lower():
                self._mark_quota_exhausted(provider, selected_model)
            if len(_chain) < 2 or not (unavailable or self._is_quota_error(error)):
                raise error
            next_provider, next_model = _chain[1]
            print(
                f"{PROVIDER_LABELS[provider]} {selected_model} translation unavailable; "
                f"trying {PROVIDER_LABELS[next_provider]} {next_model} for the "
                f"{total_segments - len(done)} remaining lines: {error}"
            )
            report(
                f"{model_label} is busy or out of quota; continuing the remaining "
                f"{total_segments - len(done)} lines with {PROVIDER_LABELS[next_provider]} {next_model}"
            )
            result = await self.translate_transcript(
                transcript, target_language, _chain=_chain[1:],
                _chain_offset=_chain_offset + 1, on_progress=on_progress,
                _done=done, _started_at=started_at,
            )
            result.setdefault("fallback_reason", str(error)[:300])
            return result
        translated = [
            {**item, "text": done[f"segment-{index}"]}
            for index, item in enumerate(transcript)
        ]
        source_text = "\n".join(str(item.get("text", "")).strip() for item in transcript)
        translated_joined = "\n".join(
            str(item.get("text", "")).strip() for item in translated
        )
        if source_text and translated_joined == source_text:
            raise ValueError(
                "Translation model returned the original text unchanged. "
                "Choose another model or confirm that the selected target language differs from the source."
            )
        return {
            "transcript": translated,
            "provider": self._provider_label(provider, _chain_offset),
            "model": selected_model,
            "batch_count": len(batches),
            "context_lines": context_lines,
            "elapsed_seconds": round(time.monotonic() - started_at, 1),
        }

    @staticmethod
    def _translation_batches(items, maximum_items=60, maximum_characters=6000):
        batches = []
        current = []
        current_characters = 0
        for item in items:
            item_characters = len(item["text"])
            if current and (
                len(current) >= maximum_items
                or current_characters + item_characters > maximum_characters
            ):
                batches.append(current)
                current = []
                current_characters = 0
            current.append(item)
            current_characters += item_characters
        if current:
            batches.append(current)
        return batches

    @staticmethod
    def _retry_delay(error: Exception) -> float:
        """Use the wait time a rate-limited API asks for (e.g. 'try again in 7.5s')."""
        match = re.search(r"try again in\s+(?:(\d+)m)?([\d.]+)(ms|s)", str(error), re.IGNORECASE)
        if match:
            minutes = int(match.group(1) or 0)
            seconds = float(match.group(2)) / (1000 if match.group(3).lower() == "ms" else 1)
            return min(65.0, minutes * 60 + seconds + 1)
        text = str(error).lower()
        return 20.0 if ("429" in text or "rate" in text) else 2.0

    @staticmethod
    def _is_model_unavailable(error: Exception) -> bool:
        text = str(error).lower()
        return any(marker in text for marker in MODEL_UNAVAILABLE_MARKERS)

    def _mark_unavailable(self, provider, model):
        if not hasattr(self, "unavailable_models"):
            self.unavailable_models = set()
        self.unavailable_models.add((provider, model))
        print(f"{PROVIDER_LABELS[provider]} {model} is not available for this API key; skipping it")

    @staticmethod
    def _is_quota_error(error: Exception) -> bool:
        text = str(error).lower()
        return any(marker in text for marker in (
            "429", "503", "resource_exhausted", "quota", "rate limit", "rate_limit",
            "too many requests", "unavailable", "high demand", "overloaded", "request too large"
        ))

    @staticmethod
    def _fallback_chapters(transcript: list) -> list:
        """Create usable chapters without an external model."""
        chapters = []
        last_start = None
        for item in transcript:
            start = float(item.get("start", 0))
            text = " ".join(str(item.get("text", "")).split())
            if not text:
                continue
            if last_start is None or start - last_start >= 180:
                title = " ".join(text.split()[:6]).strip(".,!?")
                chapters.append({
                    "timestamp": f"{int(start // 60):02d}:{int(start % 60):02d}",
                    "title": title or f"Chapter {len(chapters) + 1}"
                })
                last_start = start
        return chapters or [{"timestamp": "00:00", "title": "Lecture"}]

    @staticmethod
    def _normalize_chapters(chapters: list, transcript: list) -> list:
        """Ensure every chapter has a usable timestamp and title."""
        if not isinstance(chapters, list):
            raise ValueError("Gemini returned chapters in an invalid format")

        normalized = []
        for index, chapter in enumerate(chapters):
            if not isinstance(chapter, dict):
                continue
            raw_timestamp = chapter.get("timestamp", "00:00")
            raw_title = " ".join(str(chapter.get("title", "")).split()).strip()
            try:
                timestamp_parts = [int(float(part)) for part in str(raw_timestamp).split(":")]
                if len(timestamp_parts) == 2:
                    total_seconds = timestamp_parts[0] * 60 + timestamp_parts[1]
                elif len(timestamp_parts) == 3:
                    total_seconds = timestamp_parts[0] * 3600 + timestamp_parts[1] * 60 + timestamp_parts[2]
                else:
                    raise ValueError
            except (TypeError, ValueError):
                total_seconds = int(float(transcript[index].get("start", 0))) if index < len(transcript) else 0

            if not raw_title:
                matching_text = next(
                    (
                        " ".join(str(item.get("text", "")).split())
                        for item in transcript
                        if abs(float(item.get("start", 0)) - total_seconds) < 30
                    ),
                    f"Chapter {index + 1}",
                )
                raw_title = " ".join(matching_text.split()[:6]).strip(".,!?") or f"Chapter {index + 1}"

            normalized.append({
                "timestamp": f"{total_seconds // 60:02d}:{total_seconds % 60:02d}",
                "title": raw_title,
            })
        if not normalized:
            raise ValueError("Gemini returned no valid chapters")
        return normalized

    @staticmethod
    def _save_chapters(video_id: str, chapters: list):
        if video_id:
            summary_path = SUMMARIES_DIR / f"{video_id}.json"
            with summary_path.open("w", encoding="utf-8") as file:
                json.dump(chapters, file)

    @staticmethod
    def _looks_like_legacy_fallback(chapters: list) -> bool:
        if not isinstance(chapters, list) or not chapters:
                return True
        titles = [str(item.get("title", "")).strip().lower() for item in chapters if isinstance(item, dict)]
        if len(titles) != len(chapters) or any(not title for title in titles):
                return True
        if all(title == f"chapter {index + 1}" for index, title in enumerate(titles)):
                return True
        if all(title == f"interval {index + 1}" for index, title in enumerate(titles)):
                return True
        timestamps = []
        for item in chapters:
                try:
                    timestamps.append(SummaryProcessor._timestamp_seconds(str(item["timestamp"])))
                except (KeyError, TypeError, ValueError):
                    return True
        gaps = [right - left for left, right in zip(timestamps, timestamps[1:])]
        return len(gaps) >= 2 and all(gap == 180 for gap in gaps)

    @staticmethod
    def _timestamp_seconds(timestamp: str) -> int:
        parts = [int(float(part)) for part in timestamp.split(":")]
        if len(parts) == 2:
                return parts[0] * 60 + parts[1]
        if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
        raise ValueError("Invalid timestamp")

    async def get_summary(self, video_id: str):
        """Get saved chapter summary for a video."""
        summary_path = str(SUMMARIES_DIR / f"{video_id}.json")
        if os.path.exists(summary_path):
            try:
                with open(summary_path, 'r', encoding='utf-8') as f:
                    chapters = json.load(f)
                if isinstance(chapters, list) and chapters and isinstance(chapters[0], list):
                    chapters = [item for sublist in chapters for item in sublist]
                if self._looks_like_legacy_fallback(chapters):
                    return JSONResponse({
                        "success": True,
                        "chapters": [],
                        "exists": False,
                        "stale": True
                    })
                chapters = self._normalize_chapters(chapters, [])
                return JSONResponse({
                    "success": True,
                    "chapters": chapters,
                    "exists": True
                })
            except Exception as e:
                return JSONResponse({
                    "success": False,
                    "error": str(e)
                })
        return JSONResponse({
            "success": True,
            "chapters": [],
            "exists": False
        })