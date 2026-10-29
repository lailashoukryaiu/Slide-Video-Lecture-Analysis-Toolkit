"""Quiz generation: scope, slide text, prompt, LLM call, rule checks, judge, regeneration, save.

Uses the same GOOGLE_API_KEY and GEMINI_MODEL that the chapter summaries use.
Run from the project root:  python -m processors.quiz_generator
"""

import json
import math
import os
import random
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from processors.quiz_processor import (
    build_scope,
    extract_content,
    load_lecture_data,
    parse_timestamp,
)

load_dotenv()

QUESTION_TYPES = ("mcq", "true_false", "fill_in_blank", "short_answer")
DIFFICULTY_GUIDE = {
    "easy": "Recall and comprehension: key terms, definitions, basic facts.",
    "medium": "Application and analysis: apply ideas to new cases, compare, explain cause and effect.",
    "hard": "Evaluation and synthesis: assess approaches, combine concepts, justify decisions.",
}
BLOOM_GUIDE = {
    "remember": "Recall facts and definitions that the lecture states.",
    "understand": "Explain an idea in your own words, or say what a concept means.",
    "apply": "Use a concept on a new small case or example.",
}
MAX_ROUNDS = 3
DUPLICATE_THRESHOLD = 0.7
MAX_PROMPT_CHARS = 30000   # longer scopes are split into time windows
WINDOW_SECONDS = 600        # 10 minutes per window
BLANK = "__________"
ANSWER_FIELDS = ("correct_answer", "acceptable_answers", "model_answer", "key_points", "explanation")
FALLBACK_MODELS = ("gemini-3.6-flash", "gemini-3.5-flash-lite")
WORD = re.compile(r"[a-z']+")


def format_clock(seconds):
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


def _parse_json(text):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    return json.loads(cleaned)


def call_gemini(prompt, temperature=0.2):
    """Try the main model first. When it is busy (429 or 503), wait and retry, then fall back."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is not set in .env")
    client = genai.Client(api_key=api_key)
    models = [os.getenv("GEMINI_MODEL", "gemini-3.8-flash"), *FALLBACK_MODELS]
    last_error = None
    for model in models:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=temperature,
                        response_mime_type="application/json",
                    ),
                )
                return response.text
            except genai_errors.APIError as error:
                last_error = error
                if error.code == 404:
                    break  # this model is retired for new users, try the next one
                if error.code not in (429, 503):
                    raise
                print(f"{model} is busy (HTTP {error.code}), retrying...")
                time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"All Gemini models are busy right now: {last_error}")


class ModelOutputError(RuntimeError):
    """The model replied, but not with usable JSON, even after a retry."""


def llm_json(llm, prompt, retries=1):
    """Call the model and parse its JSON reply. A malformed reply is retried once."""
    last_error = None
    for _ in range(retries + 1):
        try:
            return _parse_json(llm(prompt))
        except json.JSONDecodeError as error:
            last_error = error
    raise ModelOutputError(f"Model did not return valid JSON after retry: {last_error}")


def load_slides(base_dir, video_id):
    """Slide text from the OCR scene file: list of (time_seconds, text)."""
    path = Path(base_dir) / "static" / "scenes" / f"{video_id}.json"
    if not path.exists():
        return []
    try:
        scenes = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    slides = []
    for scene in scenes if isinstance(scenes, list) else []:
        if not isinstance(scene, dict):
            continue
        text = str(scene.get("slide_text") or "").strip()
        if not text:
            yolo = scene.get("yolo_detections") or {}
            if yolo.get("success"):
                texts = [str(d.get("ocr_text", "")).strip()
                         for d in yolo.get("detections", []) if isinstance(d, dict)]
                text = " ".join(t for t in texts if t)
        if text:
            slides.append((float(scene.get("time_seconds", 0) or 0), text))
    return slides


def build_prompt(content, scope, question_types, difficulty, bloom, count, exclude):
    title = content["title"]
    summary = content["summary"]
    transcript = content["transcript_text"]
    slide_text = content.get("slide_text") or "(no slide text in this part)"
    types_text = ", ".join(question_types)
    guide = DIFFICULTY_GUIDE[difficulty]
    bloom_guide = BLOOM_GUIDE[bloom]
    start_clock = format_clock(scope.start)
    end_clock = format_clock(scope.end)
    avoid = "\n".join(f"- {q}" for q in exclude) if exclude else "(none)"

    return f"""You are an expert educational quiz creator. Write quiz questions from the lecture content below.

Rules:
- Every question must be answerable from the transcript or the slide text only. Do not use outside knowledge.
- Each question gives source_timestamp (MM:SS, between {start_clock} and {end_clock}) and source_text (a phrase copied word for word from the transcript or the slide text).
- mcq: exactly 4 options with keys A, B, C, D, one correct answer, and plausible distractors of similar length.
- true_false: one unambiguous statement from the lecture.
- fill_in_blank: the question contains the blank {BLANK} in place of one key term.
- short_answer: a specific question, a model_answer of 1 to 3 sentences, and 3 to 5 key_points.
- Every question needs a non-empty explanation.
- Difficulty: {difficulty}. {guide}
- Bloom level: {bloom}. {bloom_guide}
- Return only JSON that matches the schema. No commentary.

Schema:
{{"questions": [{{"type": "mcq | true_false | fill_in_blank | short_answer", "question": "...", "options": [{{"key": "A", "text": "..."}}], "correct_answer": "...", "acceptable_answers": ["..."], "model_answer": "...", "key_points": ["..."], "explanation": "...", "source_timestamp": "MM:SS", "source_text": "..."}}]}}
Include only the fields that apply to each type.

Question types to use: {types_text}
Number of questions: {count}

Do NOT repeat these questions:
{avoid}

Lecture: {title}
Chapter summary: {summary}

Transcript:
{transcript}

Slide text (OCR):
{slide_text}
"""


def _supported(source_text, evidence_text):
    """True if most long words in the quoted source appear in the evidence (transcript and slides)."""
    words = [w for w in WORD.findall(source_text.lower()) if len(w) > 3]
    if not words:
        return False
    evidence_words = set(WORD.findall(evidence_text.lower()))
    hits = sum(1 for w in words if w in evidence_words)
    return hits / len(words) >= 0.6


def _normalize(raw, scope, evidence_text, difficulty):
    """Return (item, None) if the rules pass, or (None, reason) if they do not."""
    if not isinstance(raw, dict):
        return None, "not an object"
    qtype = raw.get("type")
    question = str(raw.get("question", "")).strip()
    if qtype not in QUESTION_TYPES:
        return None, "unknown question type"
    if not question:
        return None, "empty question"

    try:
        timestamp = parse_timestamp(raw.get("source_timestamp", ""))
    except ValueError:
        return None, "bad timestamp"
    if not (scope.start <= timestamp <= scope.end):
        return None, "timestamp outside scope"
    source_text = str(raw.get("source_text", "")).strip()
    if not _supported(source_text, evidence_text):
        return None, "source text not in transcript or slides"

    explanation = str(raw.get("explanation", "")).strip()
    if not explanation:
        return None, "empty explanation"

    item = {
        "type": qtype,
        "difficulty": difficulty,
        "question": question,
        "explanation": explanation,
        "source": {"timestamp": format_clock(timestamp), "text": source_text},
    }

    if qtype == "mcq":
        options = [o for o in (raw.get("options") or []) if isinstance(o, dict)]
        texts = [str(o.get("text", "")).strip() for o in options]
        if len(texts) != 4 or len(set(texts)) != 4:
            return None, "mcq needs 4 distinct options"
        by_key = {str(o.get("key", "")).strip().upper(): str(o.get("text", "")).strip() for o in options}
        correct = str(raw.get("correct_answer", "")).strip()
        correct_text = by_key.get(correct.upper(), correct)
        if correct_text not in texts:
            return None, "mcq correct answer not in options"
        random.shuffle(texts)
        letters = ["A", "B", "C", "D"]
        item["options"] = [{"key": k, "text": t} for k, t in zip(letters, texts)]
        item["correct_answer"] = letters[texts.index(correct_text)]

    elif qtype == "true_false":
        value = str(raw.get("correct_answer", "")).strip().lower()
        if value not in ("true", "false"):
            return None, "true_false answer must be true or false"
        item["correct_answer"] = value

    elif qtype == "fill_in_blank":
        correct = str(raw.get("correct_answer", "")).strip()
        if BLANK not in question or not correct:
            return None, "fill_in_blank needs the blank and an answer"
        accepted = [str(a).strip() for a in (raw.get("acceptable_answers") or []) if str(a).strip()]
        item["correct_answer"] = correct
        item["acceptable_answers"] = list(dict.fromkeys([correct] + accepted))

    else:  # short_answer
        model_answer = str(raw.get("model_answer", "")).strip()
        key_points = [str(p).strip() for p in (raw.get("key_points") or []) if str(p).strip()]
        if not model_answer or not key_points:
            return None, "short_answer needs a model answer and key points"
        item["model_answer"] = model_answer
        item["key_points"] = key_points

    return item, None


def _is_duplicate(question, others):
    """Jaccard similarity on words. Questions above the threshold count as repeats."""
    words = set(WORD.findall(question.lower()))
    for other in others:
        other_words = set(WORD.findall(other["question"].lower()))
        union = words | other_words
        if union and len(words & other_words) / len(union) >= DUPLICATE_THRESHOLD:
            return True
    return False


def judge_items(evidence_text, items, llm):
    """One LLM call that checks every item. Returns (passed_items, rejection_reasons)."""
    lines = []
    for i, it in enumerate(items):
        detail = f"{i}. [{it['type']}] {it['question']}"
        if it["type"] == "mcq":
            options = " | ".join(f"{o['key']}) {o['text']}" for o in it["options"])
            detail += f" Options: {options}. Claimed correct: {it['correct_answer']}"
        elif it["type"] in ("true_false", "fill_in_blank"):
            detail += f" Claimed correct: {it['correct_answer']}"
        else:
            detail += f" Model answer: {it['model_answer']}"
        detail += f" Explanation: {it['explanation']}"
        lines.append(detail)

    prompt = (
        "You are a strict exam reviewer. You get lecture excerpts (transcript and slide text) and questions "
        "with their claimed correct answers and explanations.\n"
        'Reply ONLY with JSON: {"results": [{"index": 0, "grounded": true, "single_correct": true, '
        '"distractors_ok": true, "explanation_ok": true, "unambiguous": true, "reason": "short reason"}]}\n'
        "- grounded: the correct answer is clearly supported by the excerpts.\n"
        "- single_correct: no other option is also correct (true for non-MCQ).\n"
        "- distractors_ok: wrong options are plausible but clearly wrong (true for non-MCQ).\n"
        "- explanation_ok: the explanation supports the claimed correct answer and does not contradict the question.\n"
        "- unambiguous: the question has exactly one correct reading. For true_false the statement is clearly true or false. "
        "For fill_in_blank only one term fits the blank (true for other types).\n\n"
        f"Lecture excerpts:\n{evidence_text[:60000]}\n\nQuestions:\n" + "\n".join(lines)
    )
    data = llm_json(llm, prompt)
    results = {}
    for r in data.get("results", []) if isinstance(data, dict) else []:
        if isinstance(r, dict):
            try:
                results[int(r.get("index", -1))] = r
            except (TypeError, ValueError):
                pass

    passed, reasons = [], []
    for i, it in enumerate(items):
        r = results.get(i)
        if r is None:
            reasons.append("judge: no result")
            continue
        checks = ["grounded", "explanation_ok"]
        if it["type"] == "mcq":
            checks += ["single_correct", "distractors_ok"]
        if it["type"] in ("true_false", "fill_in_blank"):
            checks += ["unambiguous"]
        failed = [c for c in checks if not r.get(c)]
        if failed:
            reasons.append(f"judge: {failed[0]}")
        else:
            passed.append(it)
    return passed, reasons


def _balance_true_false(items):
    """Cap each True/False value at half the True/False questions."""
    tf_count = sum(1 for i in items if i["type"] == "true_false")
    if tf_count == 0:
        return items
    limit = math.ceil(tf_count / 2)
    counts = {"true": 0, "false": 0}
    kept = []
    for item in items:
        if item["type"] != "true_false":
            kept.append(item)
        elif counts[item["correct_answer"]] < limit:
            counts[item["correct_answer"]] += 1
            kept.append(item)
    return kept


def _collect(segments, slides, start, end):
    """Transcript text and slide text that fall inside [start, end)."""
    transcript = " ".join(
        str(s.get("text", "")).strip()
        for s in segments
        if start <= float(s["start"]) < end and str(s.get("text", "")).strip()
    )
    slide_text = " ".join(text for t, text in slides if start <= t < end)
    return transcript, slide_text


def _windows(start, end):
    """Split a long scope into consecutive time windows."""
    spans = []
    t = start
    while t < end:
        spans.append((t, min(end, t + WINDOW_SECONDS)))
        t += WINDOW_SECONDS
    return spans or [(start, end)]


def _generate_span(llm, ctx, span_start, span_end, need, exclude):
    """Generate up to `need` questions from one span, with regeneration rounds."""
    transcript, slide_text = _collect(ctx["segments"], ctx["slides"], span_start, span_end)
    evidence = (transcript + "\n" + slide_text).strip()
    judge_status = ctx["judge_status"]
    if not evidence:
        return [], [], 0, 0, judge_status

    span = SimpleNamespace(start=span_start, end=span_end)
    content = {
        "title": ctx["title"],
        "summary": ctx["summary"],
        "transcript_text": transcript,
        "slide_text": slide_text,
    }
    accepted, rejections, generated, rounds = [], [], 0, 0
    for _ in range(MAX_ROUNDS):
        remaining = need - len(accepted)
        if remaining <= 0:
            break
        rounds += 1
        prompt = build_prompt(content, span, ctx["question_types"], ctx["difficulty"], ctx["bloom"],
                              remaining, exclude + [a["question"] for a in accepted])
        raw = llm_json(llm, prompt)
        candidates = raw.get("questions", []) if isinstance(raw, dict) else []
        generated += len(candidates)

        pending = []
        for candidate in candidates:
            item, reason = _normalize(candidate, span, evidence, ctx["difficulty"])
            if item is None:
                rejections.append(reason)
            elif _is_duplicate(item["question"], accepted + pending):
                rejections.append("duplicate question")
            else:
                pending.append(item)

        if pending and ctx["use_judge"]:
            try:
                pending, reasons = judge_items(evidence, pending, llm)
                rejections.extend(reasons)
            except Exception as error:
                judge_status = f"skipped: {error}"

        accepted.extend(pending[:remaining])
    return accepted, rejections, generated, rounds, judge_status


def generate_quiz(
    base_dir,
    video_id,
    scope_type="chapter",
    chapter_index=None,
    section_index=None,
    question_types=QUESTION_TYPES,
    difficulty="medium",
    count=6,
    bloom="understand",
    llm=None,
    use_judge=True,
):
    """Generate, check and return a quiz. `llm` is any function prompt -> text (default: Gemini)."""
    if difficulty not in DIFFICULTY_GUIDE:
        raise ValueError(f"difficulty must be one of {list(DIFFICULTY_GUIDE)}")
    if bloom not in BLOOM_GUIDE:
        raise ValueError(f"bloom must be one of {list(BLOOM_GUIDE)}")
    llm = llm or call_gemini

    chapters, segments = load_lecture_data(base_dir, video_id)
    scope = build_scope(chapters, segments, scope_type, chapter_index, section_index)
    content = extract_content(chapters, segments, scope)
    slides = load_slides(base_dir, video_id)

    transcript_all, slide_all = _collect(segments, slides, scope.start, scope.end)
    if not (transcript_all + slide_all).strip():
        raise ValueError("This scope has no transcript content")

    # Short scopes are one prompt. Long scopes are split into time windows, and the count is shared out.
    if len(transcript_all) + len(slide_all) <= MAX_PROMPT_CHARS:
        spans = [(scope.start, scope.end)]
    else:
        spans = _windows(scope.start, scope.end)
    base_need, extra = divmod(count, len(spans))
    needs = [base_need + (1 if i < extra else 0) for i in range(len(spans))]

    ctx = {
        "segments": segments,
        "slides": slides,
        "title": content["title"],
        "summary": content["summary"],
        "question_types": question_types,
        "difficulty": difficulty,
        "bloom": bloom,
        "use_judge": use_judge,
        "judge_status": "on" if use_judge else "off",
    }

    accepted, rejections, generated, rounds_total = [], [], 0, 0
    for (span_start, span_end), need in zip(spans, needs):
        if need <= 0:
            continue
        got, rej, gen, rounds, judge_status = _generate_span(
            llm, ctx, span_start, span_end, need, [a["question"] for a in accepted]
        )
        accepted.extend(got)
        rejections.extend(rej)
        generated += gen
        rounds_total += rounds
        ctx["judge_status"] = judge_status

    if not accepted:
        raise RuntimeError(f"No valid questions after {rounds_total} round(s)")

    # Keep every question that passed the checks, up to the count. Dropping True/False
    # questions here is what made 6 requested questions come back as 5.
    accepted = accepted[:count]
    for number, item in enumerate(accepted, start=1):
        item["id"] = f"q{number}"

    return {
        "quiz_id": str(uuid.uuid4()),
        "video_id": video_id,
        "scope": {
            "type": scope.scope_type,
            "chapter_index": scope.chapter_index,
            "section_index": scope.section_index,
            "title": scope.title,
            "time_range": {"start": format_clock(scope.start), "end": format_clock(scope.end)},
        },
        "config": {
            "difficulty": difficulty,
            "bloom": bloom,
            "question_types": list(question_types),
            "requested_count": count,
        },
        "questions": accepted,
        "metadata": {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
            "generated": generated,
            "kept": len(accepted),
            "rejected": len(rejections),
            "rejection_reasons": dict(Counter(rejections)),
            "rounds": rounds_total,
            "windows": len(spans),
            "shortfall": max(0, count - len(accepted)),
            "slides_used": bool(slide_all.strip()),
            "judge": ctx["judge_status"],
        },
    }


def save_quiz(base_dir, video_id, quiz):
    """Save the full quiz, answers included, under static/quizzes/<video_id>/."""
    folder = Path(base_dir) / "static" / "quizzes" / video_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{quiz['quiz_id']}.json"
    path.write_text(json.dumps(quiz, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def student_view(quiz):
    """Copy of the quiz with answers and source text removed. Safe to send before submission."""
    view = json.loads(json.dumps(quiz))
    for question in view["questions"]:
        for field in ANSWER_FIELDS:
            question.pop(field, None)
        question["source"].pop("text", None)
    return view


if __name__ == "__main__":
    video = "wjI1WNcIntg"
    quiz = generate_quiz(".", video, scope_type="chapter", chapter_index=1, difficulty="medium", count=6)
    path = save_quiz(".", video, quiz)
    meta = quiz["metadata"]
    print(f"Saved to {path}")
    print(f"Generated {meta['generated']}, kept {meta['kept']}, rejected {meta['rejected']}")
    print(f"Reasons: {meta['rejection_reasons']}")
    for q in student_view(quiz)["questions"]:
        print(f"{q['id']} [{q['type']}] {q['question']}")
