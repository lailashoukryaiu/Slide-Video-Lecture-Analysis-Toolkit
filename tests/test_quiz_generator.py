"""Offline tests for the quiz pipeline. No API calls: a fake LLM answers every prompt.

Run from the project root:
    python -m unittest tests.test_quiz_generator -v
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from processors.quiz_generator import generate_quiz, student_view
from processors.quiz_processor import build_scope, extract_content, load_lecture_data
from quiz_routes import _weak_chapters

VIDEO_ID = "testvideo01"

SUMMARIES = [
    {"timestamp": "00:00", "title": "Intro", "summary": "Overview of the lecture.",
     "sections": [{"timestamp": "00:00", "title": "Overview", "point": "The lecture covers structures."}]},
    {"timestamp": "01:00", "title": "Stacks and queues", "summary": "Stacks and queues differ in order.",
     "sections": [{"timestamp": "01:00", "title": "Stack basics", "point": "A stack is LIFO."}]},
    {"timestamp": "08:20", "title": "Empty part", "summary": "Nothing is said here.",
     "sections": []},
]

SEGMENTS = [
    {"text": "Welcome to the lecture on data structures.", "start": 0, "duration": 10},
    {"text": "Today we study queues and their first in first out order.", "start": 30, "duration": 10},
    {"text": "Stacks are last in first out structures, so the newest element leaves first.", "start": 60, "duration": 10},
    {"text": "A queue removes the oldest element from the front.", "start": 90, "duration": 10},
    {"text": "Sorting is a different topic we cover later.", "start": 120, "duration": 10},
]

GOOD_MCQ = {
    "type": "mcq",
    "question": "What does LIFO mean for a stack?",
    "options": [
        {"key": "A", "text": "First in, first out"},
        {"key": "B", "text": "Last in, first out"},
        {"key": "C", "text": "Random order"},
        {"key": "D", "text": "Sorted order"},
    ],
    "correct_answer": "B",
    "explanation": "A stack removes the last element that was added.",
    "source_timestamp": "01:00",
    "source_text": "Stacks are last in first out structures",
}

GOOD_TF = {
    "type": "true_false",
    "question": "A queue removes the oldest element first.",
    "correct_answer": "true",
    "explanation": "Queues are first in, first out.",
    "source_timestamp": "01:30",
    "source_text": "A queue removes the oldest element from the front",
}

GOOD_SHORT = {
    "type": "short_answer",
    "question": "Explain how a queue removes elements.",
    "model_answer": "It removes the oldest element from the front.",
    "key_points": ["oldest element", "front"],
    "explanation": "Queues remove from the front.",
    "source_timestamp": "01:30",
    "source_text": "queue removes the oldest element from the front",
}


class FakeLLM:
    """Answers generation prompts and judge prompts from two queues."""

    def __init__(self, generations, judgments=None):
        self.generations = list(generations)
        self.judgments = list(judgments or [])
        self.calls = 0

    def __call__(self, prompt):
        self.calls += 1
        if "strict exam reviewer" in prompt:
            payload = self.judgments.pop(0) if self.judgments else {"results": []}
        else:
            payload = self.generations.pop(0) if self.generations else {"questions": []}
        return json.dumps(payload)


def all_grounded(count):
    return {"results": [
        {"index": i, "grounded": True, "single_correct": True, "distractors_ok": True,
         "explanation_ok": True, "unambiguous": True, "reason": ""}
        for i in range(count)
    ]}


class QuizPipelineTests(unittest.TestCase):

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        (self.base / "static" / "summaries").mkdir(parents=True)
        (self.base / "static" / "transcripts").mkdir(parents=True)
        (self.base / "static" / "summaries" / f"{VIDEO_ID}.json").write_text(json.dumps(SUMMARIES))
        (self.base / "static" / "transcripts" / f"{VIDEO_ID}_whisper.json").write_text(json.dumps(SEGMENTS))

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    # --- scopes ---

    def test_full_scope_has_every_segment(self):
        chapters, segments = load_lecture_data(self.base, VIDEO_ID)
        scope = build_scope(chapters, segments, "full")
        content = extract_content(chapters, segments, scope)
        self.assertEqual(content["segment_count"], 5)

    def test_chapter_scope_only_has_its_segments(self):
        chapters, segments = load_lecture_data(self.base, VIDEO_ID)
        scope = build_scope(chapters, segments, "chapter", 1)
        content = extract_content(chapters, segments, scope)
        self.assertEqual(content["segment_count"], 3)  # 60s, 90s, 120s

    def test_section_scope_ends_at_next_chapter(self):
        chapters, segments = load_lecture_data(self.base, VIDEO_ID)
        scope = build_scope(chapters, segments, "section", 1, 0)
        self.assertEqual(scope.start, 60)
        self.assertEqual(scope.end, 500)  # next chapter starts at 08:20

    def test_empty_scope_is_rejected(self):
        with self.assertRaises(ValueError):
            generate_quiz(self.base, VIDEO_ID, "chapter", 2, llm=FakeLLM([]))

    # --- generation, rules, judge, regeneration ---

    def test_bad_items_are_filtered_and_duplicates_dropped(self):
        duplicate = dict(GOOD_MCQ, question="What does LIFO mean for a stack?")
        outside = dict(GOOD_TF, source_timestamp="00:10")
        llm = FakeLLM(
            generations=[{"questions": [GOOD_MCQ, outside, duplicate, GOOD_TF]}],
            judgments=[all_grounded(2)],
        )
        quiz = generate_quiz(self.base, VIDEO_ID, "chapter", 1, count=2, llm=llm)
        self.assertEqual(len(quiz["questions"]), 2)
        reasons = quiz["metadata"]["rejection_reasons"]
        self.assertEqual(reasons.get("timestamp outside scope"), 1)
        self.assertEqual(reasons.get("duplicate question"), 1)
        self.assertEqual(llm.calls, 2)  # one generation call + one judge call

    def test_regenerates_until_count_is_reached(self):
        llm = FakeLLM(
            generations=[
                {"questions": [GOOD_MCQ]},
                {"questions": [GOOD_TF, GOOD_SHORT]},
            ],
            judgments=[all_grounded(1), all_grounded(2)],
        )
        quiz = generate_quiz(self.base, VIDEO_ID, "chapter", 1, count=3, llm=llm)
        self.assertEqual(len(quiz["questions"]), 3)
        self.assertEqual(quiz["metadata"]["rounds"], 2)

    def test_judge_rejects_ungrounded_questions(self):
        bad_judgment = {"results": [{"index": 0, "grounded": False, "single_correct": True,
                                     "distractors_ok": True, "reason": "not in lecture"}]}
        llm = FakeLLM(
            generations=[{"questions": [GOOD_MCQ]}] * 3,
            judgments=[bad_judgment] * 3,
        )
        with self.assertRaises(RuntimeError):
            generate_quiz(self.base, VIDEO_ID, "chapter", 1, count=1, llm=llm)
        self.assertEqual(llm.calls, 6)  # three rounds, generation plus judge each time

    def test_judge_rejects_explanation_that_contradicts_answer(self):
        bad = {"results": [{"index": 0, "grounded": True, "single_correct": True, "distractors_ok": True,
                            "explanation_ok": False, "unambiguous": True, "reason": "wrong explanation"}]}
        llm = FakeLLM(generations=[{"questions": [GOOD_MCQ]}] * 3, judgments=[bad] * 3)
        with self.assertRaises(RuntimeError):
            generate_quiz(self.base, VIDEO_ID, "chapter", 1, count=1, llm=llm)

    def test_judge_rejects_ambiguous_true_false(self):
        bad = {"results": [{"index": 0, "grounded": True, "single_correct": True, "distractors_ok": True,
                            "explanation_ok": True, "unambiguous": False, "reason": "ambiguous"}]}
        llm = FakeLLM(generations=[{"questions": [GOOD_TF]}] * 3, judgments=[bad] * 3)
        with self.assertRaises(RuntimeError):
            generate_quiz(self.base, VIDEO_ID, "chapter", 1, count=1, llm=llm)

    def test_malformed_json_is_retried_once(self):
        replies = iter(["not json at all {", json.dumps({"questions": [GOOD_MCQ]}),
                        json.dumps(all_grounded(1))])
        quiz = generate_quiz(self.base, VIDEO_ID, "chapter", 1, count=1, llm=lambda p: next(replies))
        self.assertEqual(len(quiz["questions"]), 1)

    def test_malformed_json_twice_is_a_model_error(self):
        from processors.quiz_generator import ModelOutputError
        with self.assertRaises(ModelOutputError):
            generate_quiz(self.base, VIDEO_ID, "chapter", 1, count=1, llm=lambda p: "{broken")

    def test_judge_can_be_turned_off(self):
        llm = FakeLLM(generations=[{"questions": [GOOD_MCQ]}])
        quiz = generate_quiz(self.base, VIDEO_ID, "chapter", 1, count=1, llm=llm, use_judge=False)
        self.assertEqual(quiz["metadata"]["judge"], "off")
        self.assertEqual(llm.calls, 1)

    def test_bloom_level_is_stored(self):
        llm = FakeLLM(generations=[{"questions": [GOOD_MCQ]}], judgments=[all_grounded(1)])
        quiz = generate_quiz(self.base, VIDEO_ID, "chapter", 1, count=1, bloom="apply", llm=llm)
        self.assertEqual(quiz["config"]["bloom"], "apply")

    def test_unknown_bloom_level_is_rejected(self):
        with self.assertRaises(ValueError):
            generate_quiz(self.base, VIDEO_ID, "chapter", 1, bloom="evaluate", llm=FakeLLM([]))

    # --- student view and feedback ---

    def test_student_view_hides_answers(self):
        llm = FakeLLM(generations=[{"questions": [GOOD_MCQ]}], judgments=[all_grounded(1)])
        quiz = generate_quiz(self.base, VIDEO_ID, "chapter", 1, count=1, llm=llm)
        text = json.dumps(student_view(quiz))
        for secret in ("correct_answer", "explanation", '"text": "Stacks are'):
            self.assertNotIn(secret, text)

    def test_weak_chapters_are_reported(self):
        chapters = SUMMARIES
        attempt = {"results": [
            {"score": 0.0, "source": {"timestamp": "01:00"}},
            {"score": 0.0, "source": {"timestamp": "01:30"}},
            {"score": 1.0, "source": {"timestamp": "00:10"}},
            {"score": None, "source": {"timestamp": "00:20"}},
        ]}
        weak = _weak_chapters(attempt, chapters)
        self.assertEqual(len(weak), 1)
        self.assertEqual(weak[0]["title"], "Stacks and queues")
        self.assertEqual(weak[0]["average"], 0.0)


from unittest import mock

import quiz_routes
from processors.quiz_generator import load_slides
from processors.quiz_processor import load_lecture_data


class NewFeatureTests(unittest.TestCase):
    setUp = QuizPipelineTests.setUp
    tearDown = QuizPipelineTests.tearDown


    def test_letter_grades(self):
        self.assertEqual(quiz_routes._letter(95), "A")
        self.assertEqual(quiz_routes._letter(80), "B")
        self.assertEqual(quiz_routes._letter(70), "C")
        self.assertEqual(quiz_routes._letter(60), "D")
        self.assertEqual(quiz_routes._letter(59.9), "F")

    def test_slide_text_can_support_a_question(self):
        scenes = [{"time_seconds": 75, "yolo_detections": {"success": True, "detections": [
            {"ocr_text": "Enqueue adds items at the tail"}]}}]
        (self.base / "static" / "scenes").mkdir(parents=True)
        (self.base / "static" / "scenes" / f"{VIDEO_ID}.json").write_text(json.dumps(scenes))
        self.assertEqual(load_slides(self.base, VIDEO_ID), [(75.0, "Enqueue adds items at the tail")])

        slide_only = dict(GOOD_TF, question="Enqueue adds items at the tail of a queue.",
                          correct_answer="true", source_timestamp="01:15",
                          source_text="Enqueue adds items at the tail")
        llm = FakeLLM(generations=[{"questions": [slide_only]}], judgments=[all_grounded(1)])
        quiz = generate_quiz(self.base, VIDEO_ID, "chapter", 1, count=1, llm=llm)
        self.assertTrue(quiz["metadata"]["slides_used"])
        self.assertEqual(len(quiz["questions"]), 1)

    def test_youtube_captions_are_used_when_no_whisper_file(self):
        folder = self.base / "static" / "transcripts"
        (folder / f"{VIDEO_ID}_whisper.json").rename(folder / f"{VIDEO_ID}_youtube.json")
        chapters, segments = load_lecture_data(self.base, VIDEO_ID)
        self.assertEqual(len(segments), 5)

    def test_long_scope_is_split_into_windows(self):
        third = {
            "type": "short_answer", "question": "What does sorting cover?",
            "model_answer": "Sorting is a different topic.", "key_points": ["sorting"],
            "explanation": "Sorting is covered later.", "source_timestamp": "02:05",
            "source_text": "Sorting is a different topic we cover later",
        }
        llm = FakeLLM(
            generations=[{"questions": [GOOD_MCQ]}, {"questions": [GOOD_TF]}, {"questions": [third]}],
            judgments=[all_grounded(1), all_grounded(1), all_grounded(1)],
        )
        with mock.patch("processors.quiz_generator.MAX_PROMPT_CHARS", 1), \
             mock.patch("processors.quiz_generator.WINDOW_SECONDS", 30):
            quiz = generate_quiz(self.base, VIDEO_ID, "chapter", 1, count=3, llm=llm)
        # Chapter 1 runs from 01:00 to 08:20, so 30-second windows give 15 spans; only 3 need questions.
        self.assertEqual(quiz["metadata"]["windows"], 15)
        self.assertEqual(len(quiz["questions"]), 3)
        self.assertEqual(quiz["metadata"]["shortfall"], 0)

    def test_fill_in_partial_credit_from_ai(self):
        question = {"id": "q1", "type": "fill_in_blank", "question": "Items are added to the __________.",
                    "correct_answer": "tail", "acceptable_answers": ["tail"],
                    "explanation": "New items go to the tail.", "source": {"timestamp": "01:00"}}
        with mock.patch("quiz_routes.call_gemini", return_value='{"score": 0.8, "feedback": "close synonym"}'):
            result = quiz_routes._grade(question, "end")
        self.assertEqual(result["score"], 0.8)
        self.assertTrue(result["is_correct"])

    def test_overall_feedback_falls_back_when_ai_fails(self):
        quiz = {"scope": {"title": "Queue"}, "questions": []}
        weak = [{"title": "Queue implementation", "timestamp": "01:21", "average": 0.5, "questions": 2}]
        with mock.patch("quiz_routes.call_gemini", side_effect=RuntimeError("busy")):
            overall, recs = quiz_routes._overall_feedback(quiz, [], 50.0, "F", weak)
        self.assertIn("grade F", overall)
        self.assertEqual(recs[0]["timestamp"], "01:21")

    def test_feedback_prompt_states_the_real_result(self):
        quiz = {"scope": {"title": "Queue"}, "questions": [
            {"id": "q1", "question": "Q1?"}, {"id": "q2", "question": "Q2?"}]}
        results = [
            {"question_id": "q1", "type": "mcq", "score": 0.0, "is_correct": False,
             "source": {"timestamp": "01:00"}},
            {"question_id": "q2", "type": "true_false", "score": 1.0, "is_correct": True,
             "source": {"timestamp": "01:30"}},
        ]
        captured = {}

        def fake(prompt, temperature=0.2):
            captured["prompt"] = prompt
            return '{"overall": "Keep practising.", "recommendations": []}'

        with mock.patch("quiz_routes.call_gemini", side_effect=fake):
            quiz_routes._overall_feedback(quiz, results, 50.0, "F", [])
        self.assertIn("1 of 2 questions correct", captured["prompt"])
        self.assertIn("Do not praise more than the result justifies", captured["prompt"])

    def test_student_progress_shows_change(self):
        routes_base = Path(tempfile.mkdtemp())
        try:
            folder = routes_base / "static" / "quizzes" / VIDEO_ID
            (folder / "attempts").mkdir(parents=True)
            quiz_id = "11111111-2222-3333-4444-555555555555"
            (folder / f"{quiz_id}.json").write_text(json.dumps({"quiz_id": quiz_id, "scope": {"title": "Queue"}}))
            for n, pct in enumerate([40.0, 80.0], start=1):
                (folder / "attempts" / f"a{n}.json").write_text(json.dumps({
                    "attempt_id": f"a{n}", "quiz_id": quiz_id, "submitted_at": f"2026-10-1{n}T10:00:00+00:00",
                    "score": {"percentage": pct, "grade": "B", "graded": 5, "total": 5}, "results": []}))
            with mock.patch.object(quiz_routes, "BASE_DIR", routes_base):
                progress = quiz_routes.student_progress(VIDEO_ID)
            self.assertEqual(progress["count"], 2)
            self.assertEqual(progress["change_since_first"], 40.0)
        finally:
            shutil.rmtree(routes_base, ignore_errors=True)


from fastapi import HTTPException

class PublishTests(unittest.TestCase):

    def test_students_only_see_published_quizzes(self):
        base = Path(tempfile.mkdtemp())
        try:
            folder = base / "static" / "quizzes" / VIDEO_ID
            folder.mkdir(parents=True)
            quiz_id = "22222222-3333-4444-5555-666666666666"
            (folder / f"{quiz_id}.json").write_text(json.dumps({
                "quiz_id": quiz_id, "scope": {"title": "Queue"},
                "config": {"difficulty": "easy", "bloom": "remember"},
                "questions": [], "metadata": {"created_at": "2026-10-27T10:00:00+00:00"},
                "published": False}))
            with mock.patch.object(quiz_routes, "BASE_DIR", base):
                self.assertEqual(quiz_routes.list_quizzes(VIDEO_ID, role="student"), [])
                self.assertEqual(len(quiz_routes.list_quizzes(VIDEO_ID, role="instructor")), 1)
                with self.assertRaises(HTTPException):
                    quiz_routes.get_quiz(quiz_id, VIDEO_ID, role="student")
                quiz_routes.publish_quiz(quiz_id, VIDEO_ID, publish=True, role="instructor")
                self.assertEqual(len(quiz_routes.list_quizzes(VIDEO_ID, role="student")), 1)
                with self.assertRaises(HTTPException):
                    quiz_routes.publish_quiz(quiz_id, VIDEO_ID, publish=False, role="student")
        finally:
            shutil.rmtree(base, ignore_errors=True)


class AttemptNameTests(unittest.TestCase):

    def test_attempts_keep_the_student_name_and_hide_from_students(self):
        base = Path(tempfile.mkdtemp())
        try:
            folder = base / "static" / "quizzes" / VIDEO_ID
            folder.mkdir(parents=True)
            quiz_id = "33333333-4444-5555-6666-777777777777"
            (folder / f"{quiz_id}.json").write_text(json.dumps({
                "quiz_id": quiz_id, "scope": {"title": "Queue"}, "published": True,
                "questions": [{"id": "q1", "type": "mcq", "question": "Q?", "options": [],
                               "correct_answer": "A", "explanation": "x", "source": {"timestamp": "01:00"}}],
            }))
            body = quiz_routes.SubmitRequest(answers={"q1": "A"}, student_name="  Huda  ")
            with mock.patch.object(quiz_routes, "BASE_DIR", base), \
                 mock.patch("quiz_routes.call_gemini", side_effect=RuntimeError("offline")):
                attempt = quiz_routes.submit_quiz(quiz_id, VIDEO_ID, body, role="student")
                self.assertEqual(attempt["student_name"], "Huda")
                rows = quiz_routes.list_attempts(quiz_id, VIDEO_ID, role="instructor")
                self.assertEqual(rows[0]["student_name"], "Huda")
                with self.assertRaises(HTTPException):
                    quiz_routes.list_attempts(quiz_id, VIDEO_ID, role="student")
        finally:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
