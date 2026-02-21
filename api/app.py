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

import certifi
from bson import ObjectId
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from openai import OpenAI
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.server_api import ServerApi

from api.judging import judge_answer
from api.scoring import compute_final_score, update_user_vector

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MONGODB_URI = os.environ["MONGODB_URI"]
MONGODB_DB = os.getenv("MONGODB_DB", "question_bank")
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _img_to_b64(data: bytes | None) -> str | None:
    if data is None:
        return None
    return base64.b64encode(data).decode("ascii")


def _get_problem(problem_id: str) -> dict:
    try:
        doc = db["questions"].find_one({"_id": ObjectId(problem_id)})
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
    if not img:
        raise HTTPException(404, "No solution image for this problem")
    return Response(content=bytes(img), media_type="image/png")


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
