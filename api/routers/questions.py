"""Endpoints for AI-generated practice questions."""
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from models.db import questions_coll
from services.question_generator import generate_questions

router = APIRouter(prefix="/questions", tags=["Questions"])


class GenerateRequest(BaseModel):
    count: int = 3


@router.post("/generate")
def generate(req: GenerateRequest):
    """Trigger AI generation of new practice questions."""
    if req.count < 1 or req.count > 10:
        raise HTTPException(400, "Count must be between 1 and 10")

    try:
        results = generate_questions(count=req.count)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {
        "generated": len(results),
        "questions": results,
    }


@router.get("/generated")
def list_generated(skip: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    """List all AI-generated practice questions."""
    coll = questions_coll()

    cursor = coll.find(
        {"source": "ai-generated"},
        {
            "text": 1,
            "question": 1,
            "solution_text": 1,
            "hints": 1,
            "key_takeaways": 1,
            "chunk_index": 1,
            "source": 1,
            "updated_at": 1,
        },
    ).sort("chunk_index", 1).skip(skip).limit(limit)

    questions = []
    for doc in cursor:
        questions.append({
            "problem_id": str(doc["_id"]),
            "question": doc.get("question", ""),
            "text": doc.get("text", ""),
            "solution_text": doc.get("solution_text", ""),
            "hints": doc.get("hints", []),
            "hints_count": len(doc.get("hints", [])),
            "has_solution": bool(doc.get("solution_text")),
            "source": "ai-generated",
        })

    total = coll.count_documents({"source": "ai-generated"})

    return {
        "total": total,
        "questions": questions,
    }
