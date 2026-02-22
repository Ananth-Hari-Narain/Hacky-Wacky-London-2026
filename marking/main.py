import base64
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from marking.annotator import annotate_image
from marking.chat import get_ai_reply, get_chat_history
from marking.classifier import classify_question
from marking.db import (
    create_chat_session,
    qb,
    save_image_submission,
    save_submission,
)
from marking.grader import grade_answer, validate_score
from marking.rag import search_chunks
from marking.rubrics import RUBRICS
from marking.vision_grader import grade_image_answer

app = FastAPI(title="AI Grader API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response models ─────────────────────────────────────────────────

class GradeRequest(BaseModel):
    question: str
    model_solution: str
    student_answer: str


class GradeImageRequest(BaseModel):
    """
    Submit a student's answer image for grading.

    Supply either:
      - question_id   (MongoDB _id from the question_bank) — the question and
                       solution are fetched automatically, OR
      - question_text + solution_text  — provide them directly (useful for
                       testing without a seeded question bank).

    image_base64 must be a base64-encoded PNG or JPEG.
    """
    image_base64: str
    question_id: Optional[str] = None
    question_text: Optional[str] = None
    solution_text: Optional[str] = None
    user_id: Optional[str] = None


class ChatRequest(BaseModel):
    session_id: str
    message: str


# ── Existing text-based grade endpoint ───────────────────────────────────────

@app.get("/")
def root():
    return {"message": "AI Grader API running!"}


@app.post("/grade")
def grade(payload: GradeRequest):
    question       = payload.question
    model_solution = payload.model_solution
    student_answer = payload.student_answer

    cls           = classify_question(question)
    question_type = cls["question_type"]

    rubric = RUBRICS.get(question_type)
    if rubric is None:
        return {
            "error": f"No rubric found for question type '{question_type}'",
            "classifier": cls,
        }

    graded    = grade_answer(question, model_solution, student_answer, rubric)
    validated = validate_score(graded, rubric)

    save_submission({
        "question":       question,
        "question_type":  question_type,
        "classifier":     cls,
        "student_answer": student_answer,
        "rubric_version": "v1.0",
        "model_version":  "gpt-4o",
        "result":         validated,
    })

    return validated


# ── Image-based grading ───────────────────────────────────────────────────────

@app.post("/mark-image")
def mark_image(payload: GradeImageRequest):
    """
    Grade a student's answer submitted as an image.

    Steps:
      1. Resolve question text and solution (from DB or payload).
      2. RAG-search ingested PDFs for extra context.
      3. Call gpt-4o vision to grade the image.
      4. Annotate the image with numbered callouts + legend.
      5. Persist submission and create a chat session.
      6. Return everything the frontend needs.
    """
    question_text     = payload.question_text
    solution_text     = payload.solution_text
    solution_image_b64 = None

    # ── Step 1: fetch from question bank if question_id provided ──────────────
    if payload.question_id:
        db = qb()
        try:
            doc = db["questions"].find_one({"_id": ObjectId(payload.question_id)})
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid question_id format.")

        if not doc:
            raise HTTPException(status_code=404, detail="Question not found.")

        question_text = question_text or doc.get("text") or doc.get("question", "")
        solution_text = solution_text or doc.get("solution_text", "")

        sol_bytes = doc.get("solution_image")
        if sol_bytes:
            solution_image_b64 = base64.b64encode(sol_bytes).decode("utf-8")

    if not question_text:
        raise HTTPException(
            status_code=400,
            detail="No question text available. Provide question_id or question_text.",
        )

    # ── Step 2: RAG context from ingested PDFs ────────────────────────────────
    rag_context = search_chunks(
        query=question_text,
        collections=["solution_chunks", "notes_chunks"],
        top_k=3,
    )

    # ── Step 3: Vision grading ────────────────────────────────────────────────
    grade_result = grade_image_answer(
        image_base64=payload.image_base64,
        question_text=question_text,
        solution_text=solution_text or "",
        rag_context=rag_context,
    )

    # ── Step 4: Annotate image ────────────────────────────────────────────────
    try:
        annotated_b64 = annotate_image(
            payload.image_base64,
            grade_result.get("annotations", []),
        )
    except Exception:
        # Annotation is best-effort; fall back to original image
        annotated_b64 = payload.image_base64

    # ── Step 5: Persist and create chat session ───────────────────────────────
    submission_id = save_image_submission({
        "user_id":       payload.user_id,
        "question_id":   payload.question_id,
        "question_text": question_text,
        "solution_text": solution_text,
        "grade_result":  grade_result,
        "created_at":    datetime.now(timezone.utc),
    })

    session_id = create_chat_session({
        "user_id":       payload.user_id,
        "submission_id": submission_id,
        "context": {
            "question_text":         question_text,
            "solution_text":         solution_text or "",
            "student_transcription": grade_result.get("transcription", ""),
            "score":                 grade_result.get("score", 0),
            "overall_feedback":      grade_result.get("overall_feedback", ""),
            "annotations":           grade_result.get("annotations", []),
            "what_went_well":        grade_result.get("what_went_well", []),
            "areas_for_improvement": grade_result.get("areas_for_improvement", []),
        },
        "messages":   [],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    })

    # ── Step 6: Return response ───────────────────────────────────────────────
    return {
        "session_id":            session_id,
        "submission_id":         submission_id,
        "score":                 grade_result.get("score", 0),
        "max_score":             grade_result.get("max_score", 10),
        "overall_feedback":      grade_result.get("overall_feedback", ""),
        "what_went_well":        grade_result.get("what_went_well", []),
        "areas_for_improvement": grade_result.get("areas_for_improvement", []),
        "annotations":           grade_result.get("annotations", []),
        "transcription":         grade_result.get("transcription", ""),
        "annotated_image":       annotated_b64,       # base64 PNG
        "solution_text":         solution_text or "",
        "solution_image":        solution_image_b64,  # base64 PNG or null
    }


# ── Image upload version (file upload instead of base64) ─────────────────────

@app.post("/mark-upload")
async def mark_upload(
    file: UploadFile = File(..., description="Student answer image (PNG/JPEG)"),
    question_text: str = Form("", description="The exam question"),
    solution_text: str = Form("", description="The model solution"),
    question_id: str = Form("", description="MongoDB question _id (optional)"),
    user_id: str = Form("", description="Student user ID (optional)"),
):
    """
    Same as /mark-image but accepts a file upload instead of base64.
    Much easier to test in Swagger UI.
    """
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="No image file provided.")
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    # Reuse the same logic as mark_image
    payload = GradeImageRequest(
        image_base64=image_b64,
        question_id=question_id or None,
        question_text=question_text or None,
        solution_text=solution_text or None,
        user_id=user_id or None,
    )
    return mark_image(payload)


# ── Tutor chat ────────────────────────────────────────────────────────────────

@app.post("/chat")
def chat(payload: ChatRequest):
    """
    Send a message to the AI tutor for a graded submission.
    Requires a session_id returned by /mark-image.
    """
    try:
        reply = get_ai_reply(payload.session_id, payload.message)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chat error: {exc}")

    return {"session_id": payload.session_id, "reply": reply}


@app.get("/chat/{session_id}/history")
def chat_history(session_id: str):
    """Return the full message history for a chat session."""
    history = get_chat_history(session_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return {"session_id": session_id, "messages": history}
