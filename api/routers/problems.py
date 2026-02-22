import base64

from bson import ObjectId
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from config.settings import QUESTIONS_COLLECTION
from models.db import questions_coll, users_coll, get_image_submission

router = APIRouter(tags=["Problems"])


class HintRequest(BaseModel):
    user_id: str
    hint_number: int


def _img_to_b64(data: bytes | None) -> str | None:
    if data is None:
        return None
    return base64.b64encode(data).decode("ascii")


def _get_problem(problem_id: str) -> dict:
    try:
        doc = questions_coll().find_one({"_id": ObjectId(problem_id)})
    except Exception:
        doc = None
    if not doc:
        raise HTTPException(404, f"Problem {problem_id} not found")
    return doc


def _get_solution(problem_id: str, doc: dict) -> dict | None:
    if doc.get("solution_text") or doc.get("solution_image"):
        return {
            "solution_text": doc.get("solution_text", ""),
            "solution_image": doc.get("solution_image"),
        }
    return None


@router.get("/problem/{problem_id}")
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


@router.get("/problem/{problem_id}/image")
def get_problem_image(problem_id: str):
    doc = _get_problem(problem_id)
    img = doc.get("question_image")
    if not img:
        raise HTTPException(404, "No image for this problem")
    return Response(content=bytes(img), media_type="image/png")


@router.get("/problem/{problem_id}/solution-image")
def get_solution_image(problem_id: str):
    doc = _get_problem(problem_id)
    img = doc.get("solution_image")
    if not img:
        sol = _get_solution(problem_id, doc)
        if sol:
            img = sol.get("solution_image")
    if not img:
        raise HTTPException(404, "No solution image for this problem")
    return Response(content=bytes(img), media_type="image/png")


@router.get("/submission/{submission_id}/annotated-image")
def get_annotated_image(submission_id: str):
    sub = get_image_submission(submission_id)
    if not sub:
        raise HTTPException(404, f"Submission {submission_id} not found")
    img = sub.get("annotated_image")
    if not img:
        raise HTTPException(404, "No annotated image for this submission")
    return Response(content=bytes(img), media_type="image/jpeg")


@router.post("/problem/{problem_id}/hint")
def get_hint(problem_id: str, req: HintRequest):
    doc = _get_problem(problem_id)
    hints = doc.get("hints", [])

    if req.hint_number < 1 or req.hint_number > len(hints):
        raise HTTPException(400, f"Hint number must be 1-{len(hints)}")

    users_coll().update_one(
        {"user_id": req.user_id},
        {"$addToSet": {f"hint_usage.{problem_id}": req.hint_number}},
    )

    return {
        "hint_number": req.hint_number,
        "hint": hints[req.hint_number - 1],
        "hints_remaining": len(hints) - req.hint_number,
    }
