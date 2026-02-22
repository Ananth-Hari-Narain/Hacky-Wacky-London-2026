"""
app.py

FastAPI application for the adaptive learning engine.

Run with:
    uvicorn api.app:app --reload

Interactive docs:
    http://localhost:8000/docs     (Swagger UI — try endpoints in-browser)
    http://localhost:8000/redoc    (alternative read-only docs)

===========================================================================
ENDPOINTS
===========================================================================

GET /recommend?user_id={id}&top={n}
    Returns top-N problem recommendations ranked by the user's weaknesses.
    Response: { user_id, recommendations: [{ problem_id, source, question, score }] }

GET /problem/{problem_id}
    Returns problem metadata + base64-encoded question image.
    Response: { problem_id, source, question, text, question_image, hints_available, has_solution }
    - question_image is a base64-encoded PNG string (or null if not rendered).
    - Use in HTML:  <img src="data:image/png;base64,{question_image}" />

GET /problem/{problem_id}/image
    Serves the question image as a raw PNG. Open directly in a browser tab
    or use as an <img src="..."> URL.
    Response: image/png binary

GET /problem/{problem_id}/solution-image
    Serves the solution image as a raw PNG.
    Response: image/png binary

POST /problem/{problem_id}/hint
    Request a progressive hint (1 = vague, 2 = moderate, 3 = specific).
    Body:     { "user_id": "...", "hint_number": 1 }
    Response: { hint_number, hint, hints_remaining }

POST /problem/{problem_id}/judge
    Submit a student answer for GPT-based grading against the exemplar solution.
    Body:     { "user_id": "...", "answer": "...", "attempt": 1 }
    Response: { correctness, final_score, feedback, is_complete, hints_used, attempt }
    - If is_complete=true, the user's topic_vector is updated and key_takeaways are returned.
    - final_score accounts for penalties: -0.1 per hint used, -0.15 per extra attempt.

GET /users
    List all users.
    Response: { users: [{ user_id, name, description }] }

GET /user/{user_id}
    Get a user's profile including top strengths and weaknesses.
    Response: { user_id, name, description, completed_count, top_strengths, top_weaknesses }

===========================================================================
DISPLAYING IMAGES
===========================================================================

Option A — Raw PNG endpoint (simplest):
    Browser:    http://localhost:8000/problem/{id}/image
    HTML:       <img src="http://localhost:8000/problem/{id}/image" />
    JS fetch:   fetch(`/problem/${id}/image`).then(r => r.blob()).then(b => URL.createObjectURL(b))

Option B — Base64 from /problem/{id} JSON response:
    HTML:       <img src="data:image/png;base64,{response.question_image}" />
    React:      <img src={`data:image/png;base64,${data.question_image}`} />

Option C — Save to file (testing):
    curl -o question.png http://localhost:8000/problem/{id}/image && open question.png
"""

import base64
import os
from datetime import datetime, timezone
from pathlib import Path

import certifi
from bson import ObjectId
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.server_api import ServerApi

from api.judging import judge_answer
from api.scoring import compute_final_score, update_user_vector

# Marking system imports (vision grading, annotation, chat, RAG)
from marking.db import (
    init_marking_db, save_image_submission, get_image_submission,
    create_chat_session,
)
from marking.vision_grader import grade_image_answer
from marking.annotator import annotate_image
from marking.rag import search_chunks
from marking.chat import get_ai_reply, get_chat_history

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MONGODB_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI (or MONGO_URI) not found in .env")
MONGODB_DB = os.getenv("MONGODB_DB", "question_bank")
MONGODB_COLLECTION = os.getenv("MONGODB_COLLECTION", "questions")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not found in .env")

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

app = FastAPI(title="Adaptive Learning Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_mongo = MongoClient(MONGODB_URI, server_api=ServerApi("1"), tlsCAFile=certifi.where())
db = _mongo[MONGODB_DB]
oai = OpenAI(api_key=OPENAI_API_KEY)

# Serve the frontend
_static_dir = Path(__file__).resolve().parent / "static"


@app.get("/", include_in_schema=False)
def root():
    return FileResponse(_static_dir / "index.html")

# Share our MongoDB client with the marking package (avoids duplicate connections)
init_marking_db(_mongo)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class HintRequest(BaseModel):
    user_id: str
    hint_number: int  # 1-based


class JudgeRequest(BaseModel):
    user_id: str
    answer: str
    attempt: int  # 1-based


class SubmitImageRequest(BaseModel):
    user_id: str
    image_base64: str
    attempt: int = 1  # 1-based


class ChatRequest(BaseModel):
    session_id: str
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _img_to_b64(data: bytes | None) -> str | None:
    if data is None:
        return None
    return base64.b64encode(data).decode("ascii")


def _get_problem(problem_id: str) -> dict:
    try:
        doc = db[MONGODB_COLLECTION].find_one({"_id": ObjectId(problem_id)})
    except Exception:
        doc = None
    if not doc:
        raise HTTPException(404, f"Problem {problem_id} not found")
    return doc


def _get_user(user_id: str) -> dict:
    user = db["users"].find_one({"user_id": user_id})
    if not user:
        raise HTTPException(404, f"User {user_id} not found")
    return user


def _get_solution(problem_id: str, doc: dict) -> dict | None:
    """
    Find the solution for a problem from the question document fields.
    """
    if doc.get("solution_text") or doc.get("solution_image"):
        return {
            "solution_text": doc.get("solution_text", ""),
            "solution_image": doc.get("solution_image"),
        }
    return None


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/recommend")
def recommend(user_id: str, top: int = 5):
    user = _get_user(user_id)
    user_vec = user["topic_vector"]
    completed = set(str(pid) for pid in user.get("completed_problems", []))

    need = [1.0 - v for v in user_vec]

    problems = list(db["problem_vectors"].find({"topic_vector": {"$exists": True}}))

    scored = []
    for p in problems:
        pid = str(p.get("problem_id", p["_id"]))
        if pid in completed:
            continue
        score = _dot(need, p["topic_vector"])
        scored.append({
            "problem_id": pid,
            "source": p.get("source", ""),
            "question": p.get("question", ""),
            "score": round(score, 3),
        })

    scored.sort(key=lambda x: -x["score"])
    return {"user_id": user_id, "recommendations": scored[:top]}


@app.get("/problem/{problem_id}")
def get_problem(problem_id: str):
    doc = _get_problem(problem_id)
    return {
        "problem_id": str(doc["_id"]),
        "source": doc.get("source", ""),
        "question": doc.get("question", ""),
        "text": doc.get("text", ""),
        "question_image": _img_to_b64(doc.get("question_image")),
        "hints_available": len(doc.get("hints", [])),
        "has_solution": "solution_text" in doc or "solution_image" in doc,
    }


@app.get("/problem/{problem_id}/image")
def get_problem_image(problem_id: str):
    """Serves the question image as a PNG — viewable directly in a browser."""
    doc = _get_problem(problem_id)
    img = doc.get("question_image")
    if not img:
        raise HTTPException(404, "No image for this problem")
    return Response(content=bytes(img), media_type="image/png")


@app.get("/problem/{problem_id}/solution-image")
def get_solution_image(problem_id: str):
    """Serves the solution image as a PNG."""
    doc = _get_problem(problem_id)
    img = doc.get("solution_image")
    # Fallback: check solutions collection
    if not img:
        sol = _get_solution(problem_id, doc)
        if sol:
            img = sol.get("solution_image")
    if not img:
        raise HTTPException(404, "No solution image for this problem")
    return Response(content=bytes(img), media_type="image/png")


@app.get("/submission/{submission_id}/annotated-image")
def get_annotated_image(submission_id: str):
    """Serves the annotated answer image as a PNG — viewable directly in a browser."""
    sub = get_image_submission(submission_id)
    if not sub:
        raise HTTPException(404, f"Submission {submission_id} not found")
    img = sub.get("annotated_image")
    if not img:
        raise HTTPException(404, "No annotated image for this submission")
    return Response(content=bytes(img), media_type="image/jpeg")


@app.post("/problem/{problem_id}/hint")
def get_hint(problem_id: str, req: HintRequest):
    doc = _get_problem(problem_id)
    hints = doc.get("hints", [])

    if req.hint_number < 1 or req.hint_number > len(hints):
        raise HTTPException(400, f"Hint number must be 1-{len(hints)}")

    # Track hint usage on the user's session
    db["users"].update_one(
        {"user_id": req.user_id},
        {"$addToSet": {
            f"hint_usage.{problem_id}": req.hint_number,
        }},
    )

    return {
        "hint_number": req.hint_number,
        "hint": hints[req.hint_number - 1],
        "hints_remaining": len(hints) - req.hint_number,
    }


@app.post("/problem/{problem_id}/judge")
def judge(problem_id: str, req: JudgeRequest):
    doc = _get_problem(problem_id)
    user = _get_user(req.user_id)

    problem_text = doc.get("text", "")
    solution_text = doc.get("solution_text", "")

    # Fallback: check solutions collection
    if not solution_text:
        sol = _get_solution(problem_id, doc)
        solution_text = sol.get("solution_text", "") if sol else ""

    if not solution_text:
        raise HTTPException(400, "No solution available for this problem")

    # Judge the answer
    result = judge_answer(oai, problem_text, solution_text, req.answer)
    correctness = float(result.get("correctness", 0.0))
    feedback = result.get("feedback", "")
    is_complete = result.get("is_complete", correctness >= 0.7)

    # Count hints used for this problem
    hint_usage = user.get("hint_usage", {})
    hints_used = len(hint_usage.get(problem_id, []))

    # Compute final score and update user vector
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
        # Update user proficiency vector
        problem_vector = doc.get("topic_vector", [])
        user_vector = user.get("topic_vector", [])

        if problem_vector and user_vector and len(problem_vector) == len(user_vector):
            new_vector = update_user_vector(user_vector, problem_vector, final_score)
            db["users"].update_one(
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

        # Return key takeaways
        response_data["key_takeaways"] = doc.get("key_takeaways", [])

    return response_data


@app.get("/users")
def list_users():
    users = list(db["users"].find({}, {
        "user_id": 1, "name": 1, "description": 1, "_id": 0,
    }))
    return {"users": users}


@app.get("/user/{user_id}")
def get_user(user_id: str):
    user = _get_user(user_id)
    topics_doc = db["topics"].find_one({"_id": "master"})
    tags = topics_doc["tags"] if topics_doc else []

    profile = {
        "user_id": user["user_id"],
        "name": user.get("name", ""),
        "description": user.get("description", ""),
        "completed_count": len(user.get("completed_problems", [])),
    }

    # Show top strengths and weaknesses
    vec = user.get("topic_vector", [])
    if tags and vec:
        paired = list(zip(tags, vec))
        paired.sort(key=lambda x: -x[1])
        profile["top_strengths"] = [
            {"topic": t, "score": round(v, 2)} for t, v in paired[:5]
        ]
        profile["top_weaknesses"] = [
            {"topic": t, "score": round(v, 2)} for t, v in paired[-5:]
        ]

    return profile


# ---------------------------------------------------------------------------
# Image-based grading (vision) + adaptive learning update
# ---------------------------------------------------------------------------

@app.post("/problem/{problem_id}/submit-image")
def submit_image(problem_id: str, req: SubmitImageRequest):
    """
    Grade a student's handwritten answer image AND update their topic_vector.

    This is the unified endpoint that bridges vision grading with adaptive
    learning. After grading, the user's proficiency vector is updated so
    future /recommend calls reflect their progress.
    """
    # ── 1. Validate user and problem ─────────────────────────────────────────
    doc = _get_problem(problem_id)
    user = _get_user(req.user_id)

    # ── 2. Resolve question text + solution ──────────────────────────────────
    question_text = doc.get("text", "") or doc.get("question", "")
    solution_text = doc.get("solution_text", "")

    if not solution_text:
        sol = _get_solution(problem_id, doc)
        solution_text = sol.get("solution_text", "") if sol else ""

    if not question_text:
        raise HTTPException(400, "No question text available for this problem.")

    # ── 3. RAG context from ingested PDFs ────────────────────────────────────
    rag_context = search_chunks(
        query=question_text,
        collections=["solution_chunks", "notes_chunks"],
        top_k=3,
    )

    # ── 4. Vision grading ────────────────────────────────────────────────────
    grade_result = grade_image_answer(
        image_base64=req.image_base64,
        question_text=question_text,
        solution_text=solution_text,
        rag_context=rag_context,
    )

    # ── 5. Annotate image ────────────────────────────────────────────────────
    try:
        annotated_b64 = annotate_image(
            req.image_base64,
            grade_result.get("annotations", []),
        )
    except Exception:
        annotated_b64 = req.image_base64  # fallback to original

    # ── 6. Score normalisation + user vector update ──────────────────────────
    raw_score = float(grade_result.get("score", 0))
    max_score = float(grade_result.get("max_score", 10))
    correctness = raw_score / max_score if max_score > 0 else 0.0
    correctness = max(0.0, min(1.0, correctness))

    hint_usage = user.get("hint_usage", {})
    hints_used = len(hint_usage.get(problem_id, []))

    final_score = compute_final_score(correctness, hints_used, req.attempt)
    is_complete = correctness >= 0.7
    vector_updated = False

    if is_complete:
        problem_vector = doc.get("topic_vector", [])
        user_vector = user.get("topic_vector", [])

        if problem_vector and user_vector and len(problem_vector) == len(user_vector):
            new_vector = update_user_vector(user_vector, problem_vector, final_score)
            db["users"].update_one(
                {"user_id": req.user_id},
                {
                    "$set": {
                        "topic_vector": new_vector,
                        "updated_at": datetime.now(timezone.utc),
                    },
                    "$addToSet": {"completed_problems": doc["_id"]},
                },
            )
            vector_updated = True

    # ── 7. Convert annotated image to bytes for DB storage ──────────────────
    try:
        annotated_bytes = base64.b64decode(annotated_b64)
    except Exception:
        annotated_bytes = None

    # ── 8. Persist submission + create chat session ──────────────────────────
    sub_doc = {
        "user_id":          req.user_id,
        "question_id":      problem_id,
        "question_text":    question_text,
        "solution_text":    solution_text,
        "grade_result":     grade_result,
        "correctness":      correctness,
        "final_score":      final_score,
        "hints_used":       hints_used,
        "attempt":          req.attempt,
        "vector_updated":   vector_updated,
        "annotated_image":  annotated_bytes,
        "created_at":       datetime.now(timezone.utc),
    }
    try:
        submission_id = save_image_submission(sub_doc)
    except Exception:
        # Image too large for BSON — retry without the image bytes
        sub_doc.pop("annotated_image", None)
        submission_id = save_image_submission(sub_doc)

    session_id = create_chat_session({
        "user_id":       req.user_id,
        "submission_id": submission_id,
        "context": {
            "question_text":         question_text,
            "solution_text":         solution_text,
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

    # ── 9. Return response ───────────────────────────────────────────────────
    return {
        # Adaptive learning fields
        "correctness":       round(correctness, 2),
        "final_score":       round(final_score, 2),
        "is_complete":       is_complete,
        "hints_used":        hints_used,
        "attempt":           req.attempt,
        "vector_updated":    vector_updated,
        "key_takeaways":     doc.get("key_takeaways", []) if is_complete else [],
        # Vision grading fields
        "session_id":            session_id,
        "submission_id":         submission_id,
        "score":                 grade_result.get("score", 0),
        "max_score":             grade_result.get("max_score", 10),
        "overall_feedback":      grade_result.get("overall_feedback", ""),
        "what_went_well":        grade_result.get("what_went_well", []),
        "areas_for_improvement": grade_result.get("areas_for_improvement", []),
        "annotations":           grade_result.get("annotations", []),
        "transcription":         grade_result.get("transcription", ""),
        # Image URLs (open in browser to view)
        "annotated_image_url":   f"/submission/{submission_id}/annotated-image",
        "solution_image_url":    f"/problem/{problem_id}/solution-image",
        "solution_text":         solution_text,
    }


@app.post("/problem/{problem_id}/submit-image-upload")
async def submit_image_upload(
    problem_id: str,
    file: UploadFile = File(..., description="Student answer image (PNG/JPEG)"),
    user_id: str = Form(..., description="Student user ID"),
    attempt: int = Form(1, description="Attempt number (1-based)"),
):
    """Same as /submit-image but accepts a file upload (easier for Swagger testing)."""
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(400, "No image file provided.")
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    return submit_image(
        problem_id,
        SubmitImageRequest(user_id=user_id, image_base64=image_b64, attempt=attempt),
    )


# ---------------------------------------------------------------------------
# Tutor chat
# ---------------------------------------------------------------------------

@app.post("/chat")
def chat(payload: ChatRequest):
    """Send a message to the AI tutor about a graded submission."""
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
