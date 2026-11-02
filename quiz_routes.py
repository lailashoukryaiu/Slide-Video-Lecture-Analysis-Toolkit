"""Quiz API routes.

Add these two lines to main.py, right after the line that creates `app = FastAPI(...)`
(the quiz router is already there from before; keep it):

    from quiz_routes import router as quiz_router
    app.include_router(quiz_router)
"""

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from processors.quiz_generator import (
    BLOOM_GUIDE,
    QUESTION_TYPES,
    call_gemini,
    generate_quiz,
    save_quiz,
    student_view,
)
from processors.quiz_processor import parse_timestamp

BASE_DIR = Path(__file__).resolve().parent
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
PASS_THRESHOLD = 0.7
WEAK_THRESHOLD = 0.6
PARTIAL_SCORES = (0.0, 0.5, 0.8, 1.0)

router = APIRouter(prefix="/api/quiz", tags=["quiz"])


class GenerateRequest(BaseModel):
    video_id: str
    scope_type: str = "chapter"
    chapter_index: Optional[int] = None
    section_index: Optional[int] = None
    question_types: list[str] = Field(default_factory=lambda: list(QUESTION_TYPES))
    difficulty: str = "medium"
    bloom: str = "understand"
    count: int = Field(default=6, ge=1, le=20)


class SubmitRequest(BaseModel):
    answers: dict[str, str]
    student_name: str = "Student"


def _check_video_id(video_id):
    if not VIDEO_ID_PATTERN.match(video_id):
        raise HTTPException(status_code=400, detail="Invalid video_id")


def _quiz_folder(video_id):
    return BASE_DIR / "static" / "quizzes" / video_id


def _load_full_quiz(video_id, quiz_id):
    _check_video_id(video_id)
    try:
        uuid.UUID(quiz_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid quiz_id")
    path = _quiz_folder(video_id) / f"{quiz_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Quiz not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _attempt_files(video_id, quiz_id):
    """Attempts for one quiz, oldest first, as (path, data) pairs."""
    folder = _quiz_folder(video_id) / "attempts"
    if not folder.exists():
        return []
    found = []
    for path in folder.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("quiz_id") == quiz_id:
            found.append((path, data))
    return sorted(found, key=lambda pair: pair[1]["submitted_at"])


def _load_chapters(video_id):
    path = BASE_DIR / "static" / "summaries" / f"{video_id}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _chapter_for(seconds, chapters):
    index = None
    for i, chapter in enumerate(chapters):
        if parse_timestamp(chapter["timestamp"]) <= seconds:
            index = i
    return index


def _weak_chapters(attempt, chapters, threshold=WEAK_THRESHOLD):
    """Chapters whose average score in this attempt is below the threshold."""
    buckets = {}
    for result in attempt["results"]:
        if result["score"] is None:
            continue
        index = _chapter_for(parse_timestamp(result["source"]["timestamp"]), chapters)
        if index is not None:
            buckets.setdefault(index, []).append(result["score"])

    weak = []
    for index, scores in sorted(buckets.items()):
        average = sum(scores) / len(scores)
        if average < threshold:
            weak.append({
                "chapter_index": index,
                "title": chapters[index]["title"],
                "timestamp": chapters[index]["timestamp"],
                "average": round(average, 2),
                "questions": len(scores),
            })
    return weak


def _letter(percentage):
    if percentage >= 90:
        return "A"
    if percentage >= 80:
        return "B"
    if percentage >= 70:
        return "C"
    if percentage >= 60:
        return "D"
    return "F"


def _grade_short_answer(question, answer):
    if not answer:
        return {
            "score": 0.0,
            "feedback": "No answer was written.",
            "missing_points": question["key_points"],
        }
    prompt = (
        "Grade a student's short answer against the model answer and the key points.\n"
        f"Question: {question['question']}\n"
        f"Model answer: {question['model_answer']}\n"
        f"Key points: {'; '.join(question['key_points'])}\n"
        f"Student answer: {answer}\n\n"
        "Score from 0.0 to 1.0. 1.0 covers every key point, about 0.5 covers half, "
        "0.0 is wrong or off topic.\n"
        'Return only JSON like {"score": 0.5, "feedback": "2 to 3 sentences, specific and encouraging", '
        '"missing_points": ["..."]}'
    )
    try:
        text = call_gemini(prompt, temperature=0.2).strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        data = json.loads(text)
        score = min(1.0, max(0.0, float(data.get("score", 0))))
        return {
            "score": round(score, 2),
            "feedback": str(data.get("feedback", "")).strip(),
            "missing_points": [str(p) for p in data.get("missing_points", [])],
        }
    except Exception:
        return {
            "score": None,
            "feedback": "This answer could not be graded automatically. Try again later.",
            "missing_points": [],
        }


def _judge_fill(question, answer):
    """Partial credit for a fill-in answer that is not an accepted word. Returns (score or None, feedback)."""
    prompt = (
        "A student answered a fill-in-the-blank question. Decide how close the answer is to the correct term.\n"
        f"Question: {question['question']}\n"
        f"Correct answer: {question['correct_answer']}\n"
        f"Student answer: {answer}\n\n"
        "Score: 1.0 = same meaning as the correct answer, 0.8 = a correct synonym or a more specific correct term, "
        "0.5 = related but incomplete, 0.0 = wrong.\n"
        'Return only JSON like {"score": 0.8, "feedback": "one short sentence"}'
    )
    try:
        text = call_gemini(prompt, temperature=0.2).strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        data = json.loads(text)
        raw_score = min(1.0, max(0.0, float(data.get("score", 0))))
        snapped = min(PARTIAL_SCORES, key=lambda allowed: abs(allowed - raw_score))
        return snapped, str(data.get("feedback", "")).strip()
    except Exception:
        return None, "This answer could not be checked automatically. Try again later."


def _grade(question, answer):
    qtype = question["type"]
    result = {
        "question_id": question["id"],
        "type": qtype,
        "student_answer": answer,
        "source": question["source"],
        "score": None,
        "is_correct": None,
        "feedback": "",
    }

    if qtype in ("mcq", "true_false"):
        correct = question["correct_answer"]
        is_correct = answer.lower() == correct.lower()
        result["correct_answer"] = correct
        result["score"] = 1.0 if is_correct else 0.0
        result["is_correct"] = is_correct
        result["feedback"] = "Correct." if is_correct else f"Wrong. The answer is {correct}."
        return result

    if qtype == "fill_in_blank":
        result["correct_answer"] = question["correct_answer"]
        accepted = {a.lower() for a in question["acceptable_answers"]}
        if not answer:
            score, feedback = 0.0, f"No answer was written. The answer is {question['correct_answer']}."
        elif answer.lower() in accepted:
            score, feedback = 1.0, "Correct."
        else:
            score, feedback = _judge_fill(question, answer)
            if score is None:
                result["feedback"] = feedback
                return result
            feedback = (feedback or "").split(". ")[0][:160] or f"The answer is {question['correct_answer']}."
        result["score"] = score
        result["is_correct"] = score >= PASS_THRESHOLD
        result["feedback"] = feedback
        return result

    graded = _grade_short_answer(question, answer)
    result.update(
        score=graded["score"],
        feedback=graded["feedback"],
        missing_points=graded["missing_points"],
        model_answer=question["model_answer"],
    )
    if graded["score"] is not None:
        result["is_correct"] = graded["score"] >= PASS_THRESHOLD
    return result


def _fallback_feedback(percentage, grade, weak):
    if weak:
        overall = f"You scored {percentage}% (grade {grade}). Focus on: " + ", ".join(w["title"] for w in weak) + "."
    else:
        overall = f"You scored {percentage}% (grade {grade}). No chapter needs extra review."
    recommendations = [
        {"topic": w["title"], "timestamp": w["timestamp"], "reason": f"{round(w['average'] * 100)}% on this chapter"}
        for w in weak
    ]
    return overall, recommendations


def _overall_feedback(quiz, results, percentage, grade, weak):
    """One AI call for overall feedback and study recommendations. Falls back to a local summary."""
    questions = {q["id"]: q for q in quiz["questions"]}
    lines = []
    for r in results:
        q = questions.get(r["question_id"], {})
        score = "not graded" if r["score"] is None else r["score"]
        verdict = "correct" if r["is_correct"] else "wrong"
        lines.append(f"- {r['question_id']} [{r['type']}] {q.get('question', '')} | {verdict} (score {score}) | "
                     f"timestamp {r['source']['timestamp']}")
    correct = sum(1 for r in results if r["is_correct"])
    total = len(results)
    prompt = (
        "You are a strict but fair tutor reviewing a student's quiz result.\n"
        f"Quiz: {quiz['scope']['title']}. Result: {correct} of {total} questions correct, "
        f"score {percentage}% (grade {grade}).\n"
        "Questions:\n" + "\n".join(lines) + "\n\n"
        "Rules:\n"
        "- Start with the actual result. Do not praise more than the result justifies.\n"
        "- Praise only if the score is 80% or higher. Below 60%, be direct and say clearly what went wrong.\n"
        "- Keep the overall text to one or two short sentences. Put the detail of each wrong answer in the recommendations.\n"
        "- Then give up to 5 study recommendations, each with a topic, the lecture timestamp (MM:SS) to review, "
        "and a short reason.\n"
        "- Do not invent facts that are not in the questions above.\n"
        'Return only JSON like {"overall": "...", "recommendations": [{"topic": "...", "timestamp": "MM:SS", "reason": "..."}]}'
    )
    try:
        text = call_gemini(prompt, temperature=0.2).strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        data = json.loads(text)
        overall = str(data.get("overall", "")).strip()
        recommendations = []
        for item in data.get("recommendations", [])[:5]:
            if isinstance(item, dict):
                recommendations.append({
                    "topic": str(item.get("topic", "")).strip(),
                    "timestamp": str(item.get("timestamp", "")).strip(),
                    "reason": str(item.get("reason", "")).strip(),
                })
        if overall:
            return overall, recommendations
    except Exception:
        pass
    return _fallback_feedback(percentage, grade, weak)


@router.post("/generate")
def generate(req: GenerateRequest):
    """Create a quiz from a chapter or section. Returns the student view (no answers)."""
    _check_video_id(req.video_id)
    unknown = [t for t in req.question_types if t not in QUESTION_TYPES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown question types: {unknown}")
    if req.bloom not in BLOOM_GUIDE:
        raise HTTPException(status_code=400, detail=f"bloom must be one of {list(BLOOM_GUIDE)}")
    try:
        quiz = generate_quiz(
            BASE_DIR,
            req.video_id,
            req.scope_type,
            req.chapter_index,
            req.section_index,
            tuple(req.question_types),
            req.difficulty,
            req.count,
            req.bloom,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="This video has no transcript or chapters yet")
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"AI provider error: {error}")

    quiz["published"] = False
    save_quiz(BASE_DIR, req.video_id, quiz)
    return student_view(quiz)


@router.get("/list")
def list_quizzes(video_id: str, role: str = "instructor"):
    """Saved quizzes for one video, newest first."""
    _check_video_id(video_id)
    folder = _quiz_folder(video_id)
    if not folder.exists():
        return []
    items = []
    for path in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        quiz = json.loads(path.read_text(encoding="utf-8"))
        published = quiz.get("published", True)
        if role == "student" and not published:
            continue
        items.append({
            "quiz_id": quiz["quiz_id"],
            "published": published,
            "title": quiz["scope"]["title"],
            "difficulty": quiz["config"]["difficulty"],
            "bloom": quiz["config"].get("bloom"),
            "question_count": len(quiz["questions"]),
            "created_at": quiz["metadata"]["created_at"],
        })
    return items


@router.get("/progress")
def student_progress(video_id: str):
    """Every attempt for this video across quizzes, oldest first, with the change since the first attempt."""
    _check_video_id(video_id)
    attempts = []
    folder = _quiz_folder(video_id)
    if folder.exists():
        for quiz_path in folder.glob("*.json"):
            quiz = json.loads(quiz_path.read_text(encoding="utf-8"))
            for _, data in _attempt_files(video_id, quiz["quiz_id"]):
                attempts.append({
                    "quiz_id": quiz["quiz_id"],
                    "title": quiz["scope"]["title"],
                    "attempt_id": data["attempt_id"],
                    "submitted_at": data["submitted_at"],
                    "percentage": data["score"]["percentage"],
                    "grade": data["score"].get("grade"),
                })
    attempts.sort(key=lambda a: a["submitted_at"])
    if not attempts:
        return {"attempts": [], "count": 0, "average": None, "change_since_first": None}
    average = round(sum(a["percentage"] for a in attempts) / len(attempts), 1)
    change = round(attempts[-1]["percentage"] - attempts[0]["percentage"], 1) if len(attempts) > 1 else None
    return {"attempts": attempts, "count": len(attempts), "average": average, "change_since_first": change}


@router.get("/{quiz_id}")
def get_quiz(quiz_id: str, video_id: str, role: str = "instructor"):
    """The quiz for a student to answer. Answers are not included."""
    quiz = _load_full_quiz(video_id, quiz_id)
    if role == "student" and not quiz.get("published", True):
        raise HTTPException(status_code=404, detail="This quiz is not published yet")
    return student_view(quiz)


@router.post("/{quiz_id}/submit")
def submit_quiz(quiz_id: str, video_id: str, body: SubmitRequest, role: str = "instructor"):
    """Grade the answers, save the attempt, and return feedback, weak chapters and recommendations."""
    quiz = _load_full_quiz(video_id, quiz_id)
    if role == "student" and not quiz.get("published", True):
        raise HTTPException(status_code=403, detail="This quiz is not published yet")
    results = [_grade(q, body.answers.get(q["id"], "").strip()) for q in quiz["questions"]]

    graded = [r["score"] for r in results if r["score"] is not None]
    percentage = round(100 * sum(graded) / len(graded), 1) if graded else 0.0
    grade = _letter(percentage) if graded else None
    chapters = _load_chapters(video_id)
    for result in results:
        index = _chapter_for(parse_timestamp(result["source"]["timestamp"]), chapters)
        if index is not None:
            result["review"] = {
                "chapter_index": index,
                "title": chapters[index]["title"],
                "timestamp": chapters[index]["timestamp"],
            }
    weak = _weak_chapters({"results": results}, chapters)
    overall, recommendations = _overall_feedback(quiz, results, percentage, grade, weak)

    name = (body.student_name or "").strip()[:60] or "Student"
    attempt = {
        "attempt_id": str(uuid.uuid4()),
        "quiz_id": quiz_id,
        "student_name": name,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "score": {"percentage": percentage, "grade": grade, "graded": len(graded), "total": len(results)},
        "overall_feedback": overall,
        "recommendations": recommendations,
        "weak_chapters": weak,
        "results": results,
    }

    attempts_dir = _quiz_folder(video_id) / "attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)
    (attempts_dir / f"{attempt['attempt_id']}.json").write_text(
        json.dumps(attempt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return attempt


@router.get("/{quiz_id}/attempts")
def list_attempts(quiz_id: str, video_id: str, role: str = "instructor"):
    """Every attempt for this quiz, oldest first. Instructors only."""
    if role != "instructor":
        raise HTTPException(status_code=403, detail="Only instructors can see the attempts")
    _load_full_quiz(video_id, quiz_id)
    return [
        {
            "attempt_id": data["attempt_id"],
            "student_name": data.get("student_name", "Student"),
            "submitted_at": data["submitted_at"],
            "percentage": data["score"]["percentage"],
            "grade": data["score"].get("grade"),
            "graded": data["score"]["graded"],
            "total": data["score"]["total"],
        }
        for _, data in _attempt_files(video_id, quiz_id)
    ]


@router.get("/{quiz_id}/summary")
def quiz_summary(quiz_id: str, video_id: str):
    """Result of the latest attempt: grade, overall feedback, recommendations and weak chapters."""
    _load_full_quiz(video_id, quiz_id)
    attempts = _attempt_files(video_id, quiz_id)
    if not attempts:
        raise HTTPException(status_code=404, detail="No attempts yet for this quiz")
    latest = attempts[-1][1]
    weak = latest.get("weak_chapters")
    if weak is None:
        weak = _weak_chapters(latest, _load_chapters(video_id))
    if weak:
        suggestion = "Review these chapters: " + ", ".join(w["title"] for w in weak) + "."
    else:
        suggestion = "No chapter is below 60%. Keep going."
    return {
        "attempt_id": latest["attempt_id"],
        "submitted_at": latest["submitted_at"],
        "score": latest["score"],
        "overall_feedback": latest.get("overall_feedback", ""),
        "recommendations": latest.get("recommendations", []),
        "weak_chapters": weak,
        "suggestion": suggestion,
    }


@router.delete("/{quiz_id}")
def delete_quiz(quiz_id: str, video_id: str, role: str = "instructor"):
    """Delete a quiz and its attempts. Instructors only."""
    if role != "instructor":
        raise HTTPException(status_code=403, detail="Only instructors can delete quizzes")
    _load_full_quiz(video_id, quiz_id)
    (_quiz_folder(video_id) / f"{quiz_id}.json").unlink()
    for path, _ in _attempt_files(video_id, quiz_id):
        path.unlink()
    return {"deleted": quiz_id}


@router.post("/{quiz_id}/publish")
def publish_quiz(quiz_id: str, video_id: str, publish: bool = True, role: str = "instructor"):
    """Make a quiz visible to students, or hide it again as a draft. Instructors only."""
    if role != "instructor":
        raise HTTPException(status_code=403, detail="Only instructors can publish quizzes")
    quiz = _load_full_quiz(video_id, quiz_id)
    quiz["published"] = bool(publish)
    path = _quiz_folder(video_id) / f"{quiz_id}.json"
    path.write_text(json.dumps(quiz, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"quiz_id": quiz_id, "published": quiz["published"]}
