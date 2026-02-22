import base64
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from config.settings import QUESTIONS_COLLECTION
from models.db import (
    questions_coll,
    users_coll,
    save_image_submission,
    create_chat_session,
    save_submission,
)
from services.grading import (
    judge_answer,
    grade_image_answer,
    compute_final_score,
    update_user_vector,
    classify_question,
    grade_answer as rubric_grade_answer,
    validate_score,
    RUBRICS,
)
from services.rag import search_chunks
from services.annotator import annotate_image

router = APIRouter(tags=["Grading"])


class JudgeRequest(BaseModel):
    user_id: str
    answer: str
    attempt: int


class SubmitImageRequest(BaseModel):
    user_id: str
    image_base64: str
    attempt: int = 1


class GradeRequest(BaseModel):
    question: str
    model_solution: str
    student_answer: str


def _get_problem(problem_id: str) -> dict:
    try:
        doc = questions_coll().find_one({"_id": ObjectId(problem_id)})
    except Exception:
        doc = None
    if not doc:
        raise HTTPException(404, f"Problem {problem_id} not found")
    return doc


def _get_user(user_id: str) -> dict:
    user = users_coll().find_one({"user_id": user_id})
    if not user:
        raise HTTPException(404, f"User {user_id} not found")
    return user


def _get_solution(problem_id: str, doc: dict) -> dict | None:
    if doc.get("solution_text") or doc.get("solution_image"):
        return {
            "solution_text": doc.get("solution_text", ""),
            "solution_image": doc.get("solution_image"),
        }
    return None


@router.post("/problem/{problem_id}/judge")
def judge(problem_id: str, req: JudgeRequest):
    doc = _get_problem(problem_id)
    user = _get_user(req.user_id)

    problem_text = doc.get("text", "")
    solution_text = doc.get("solution_text", "")

    if not solution_text:
        sol = _get_solution(problem_id, doc)
        solution_text = sol.get("solution_text", "") if sol else ""

    if not solution_text:
        raise HTTPException(400, "No solution available for this problem")

    result = judge_answer(problem_text, solution_text, req.answer)
    correctness = float(result.get("correctness", 0.0))
    feedback = result.get("feedback", "")
    is_complete = result.get("is_complete", correctness >= 0.7)

    hint_usage = user.get("hint_usage", {})
    hints_used = len(hint_usage.get(problem_id, []))
    final_score = compute_final_score(correctness, hints_used, req.attempt)

    response_data = {
        "correctness": round(correctness, 2),
        "final_score": round(final_score, 2),
        "feedback": feedback,
        "is_complete": is_complete,
        "hints_used": hints_used,
        "attempt": req.attempt,
    }

    if is_complete:
        problem_vector = doc.get("topic_vector", [])
        user_vector = user.get("topic_vector", [])

        if problem_vector and user_vector and len(problem_vector) == len(user_vector):
            new_vector = update_user_vector(user_vector, problem_vector, final_score)
            users_coll().update_one(
                {"user_id": req.user_id},
                {
                    "$set": {
                        "topic_vector": new_vector,
                        "updated_at": datetime.now(timezone.utc),
                    },
                    "$addToSet": {"completed_problems": doc["_id"]},
                },
            )
            response_data["vector_updated"] = True

        response_data["key_takeaways"] = doc.get("key_takeaways", [])

    return response_data


@router.post("/problem/{problem_id}/submit-image")
def submit_image(problem_id: str, req: SubmitImageRequest):
    doc = _get_problem(problem_id)
    user = _get_user(req.user_id)

    question_text = doc.get("text", "") or doc.get("question", "")
    solution_text = doc.get("solution_text", "")

    if not solution_text:
        sol = _get_solution(problem_id, doc)
        solution_text = sol.get("solution_text", "") if sol else ""

    if not question_text:
        raise HTTPException(400, "No question text available for this problem.")

    rag_context = search_chunks(
        query=question_text + " " + solution_text[:200],
        collections=["solution_chunks", "notes_chunks", QUESTIONS_COLLECTION],
        top_k=5,
    )

    grading_solution = solution_text
    if not grading_solution.strip():
        grading_solution = (
            "[NO OFFICIAL SOLUTION AVAILABLE]\n"
            "Use the lecture notes context below as your primary reference for "
            "correct answers. Apply your expert CS knowledge to determine "
            "correctness, but be transparent about the lack of a model solution.\n\n"
            f"RELEVANT REFERENCE MATERIAL:\n{rag_context}"
        ) if rag_context else (
            "[NO OFFICIAL SOLUTION OR NOTES AVAILABLE]\n"
            "Use your expert computer science knowledge to grade this answer. "
            "Be transparent that you are grading based on standard CS theory "
            "rather than an official model solution."
        )

    grade_result = grade_image_answer(
        image_base64=req.image_base64,
        question_text=question_text,
        solution_text=grading_solution,
        rag_context=rag_context if solution_text.strip() else "",
    )

    try:
        annotated_b64 = annotate_image(
            req.image_base64,
            grade_result.get("annotations", []),
        )
    except Exception:
        annotated_b64 = req.image_base64

    raw_score = float(grade_result.get("score", 0))
    max_score = float(grade_result.get("max_score", 10))
    correctness = raw_score / max_score if max_score > 0 else 0.0
    correctness = max(0.0, min(1.0, correctness))

    hint_usage = user.get("hint_usage", {})
    hints_used = len(hint_usage.get(problem_id, []))
    final_score = compute_final_score(correctness, hints_used, req.attempt)
    is_complete = correctness >= 0.7
    vector_updated = False

    problem_vector = doc.get("topic_vector", [])
    user_vector = user.get("topic_vector", [])

    if problem_vector and user_vector and len(problem_vector) == len(user_vector):
        new_vector = update_user_vector(user_vector, problem_vector, final_score)
        update_fields = {
            "$set": {
                "topic_vector": new_vector,
                "updated_at": datetime.now(timezone.utc),
            },
        }
        if is_complete:
            update_fields["$addToSet"] = {"completed_problems": doc["_id"]}
        users_coll().update_one({"user_id": req.user_id}, update_fields)
        vector_updated = True

    try:
        annotated_bytes = base64.b64decode(annotated_b64)
    except Exception:
        annotated_bytes = None

    sub_doc = {
        "user_id": req.user_id,
        "question_id": problem_id,
        "question_text": question_text,
        "solution_text": solution_text,
        "grade_result": grade_result,
        "correctness": correctness,
        "final_score": final_score,
        "hints_used": hints_used,
        "attempt": req.attempt,
        "vector_updated": vector_updated,
        "annotated_image": annotated_bytes,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        submission_id = save_image_submission(sub_doc)
    except Exception:
        sub_doc.pop("annotated_image", None)
        submission_id = save_image_submission(sub_doc)

    session_id = create_chat_session({
        "user_id": req.user_id,
        "submission_id": submission_id,
        "context": {
            "question_text": question_text,
            "solution_text": solution_text,
            "student_transcription": grade_result.get("transcription", ""),
            "score": grade_result.get("score", 0),
            "overall_feedback": grade_result.get("overall_feedback", ""),
            "annotations": grade_result.get("annotations", []),
            "what_went_well": grade_result.get("what_went_well", []),
            "areas_for_improvement": grade_result.get("areas_for_improvement", []),
        },
        "messages": [],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    })

    return {
        "correctness": round(correctness, 2),
        "final_score": round(final_score, 2),
        "is_complete": is_complete,
        "hints_used": hints_used,
        "attempt": req.attempt,
        "vector_updated": vector_updated,
        "key_takeaways": doc.get("key_takeaways", []) if is_complete else [],
        "session_id": session_id,
        "submission_id": submission_id,
        "score": grade_result.get("score", 0),
        "max_score": grade_result.get("max_score", 10),
        "overall_feedback": grade_result.get("overall_feedback", ""),
        "what_went_well": grade_result.get("what_went_well", []),
        "areas_for_improvement": grade_result.get("areas_for_improvement", []),
        "annotations": grade_result.get("annotations", []),
        "transcription": grade_result.get("transcription", ""),
        "annotated_image_url": f"/submission/{submission_id}/annotated-image",
        "solution_image_url": f"/problem/{problem_id}/solution-image",
        "solution_text": solution_text,
    }


@router.post("/problem/{problem_id}/submit-image-upload")
async def submit_image_upload(
    problem_id: str,
    file: UploadFile = File(..., description="Student answer image (PNG/JPEG)"),
    user_id: str = Form(..., description="Student user ID"),
    attempt: int = Form(1, description="Attempt number (1-based)"),
):
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(400, "No image file provided.")
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    return submit_image(
        problem_id,
        SubmitImageRequest(user_id=user_id, image_base64=image_b64, attempt=attempt),
    )


@router.post("/grade")
def grade(payload: GradeRequest):
    cls = classify_question(payload.question)
    question_type = cls["question_type"]

    rubric = RUBRICS.get(question_type)
    if rubric is None:
        return {"error": f"No rubric found for question type '{question_type}'", "classifier": cls}

    graded = rubric_grade_answer(payload.question, payload.model_solution, payload.student_answer, rubric)
    validated = validate_score(graded, rubric)

    save_submission({
        "question": payload.question,
        "question_type": question_type,
        "classifier": cls,
        "student_answer": payload.student_answer,
        "rubric_version": "v1.0",
        "model_version": "gpt-4o",
        "result": validated,
    })

    return validated
