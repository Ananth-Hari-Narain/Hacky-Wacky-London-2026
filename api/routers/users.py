from fastapi import APIRouter, HTTPException

from models.db import users_coll, topics_coll
from services.recommendations import get_recommendations

router = APIRouter(tags=["Users"])


def _get_user(user_id: str) -> dict:
    user = users_coll().find_one({"user_id": user_id})
    if not user:
        raise HTTPException(404, f"User {user_id} not found")
    return user


@router.get("/users")
def list_users():
    users = list(users_coll().find({}, {
        "user_id": 1, "name": 1, "description": 1, "_id": 0,
    }))
    return {"users": users}


@router.get("/user/{user_id}")
def get_user(user_id: str):
    user = _get_user(user_id)
    topics_doc = topics_coll().find_one({"_id": "master"})
    tags = topics_doc["tags"] if topics_doc else []

    profile = {
        "user_id": user["user_id"],
        "name": user.get("name", ""),
        "description": user.get("description", ""),
        "completed_count": len(user.get("completed_problems", [])),
    }

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


@router.get("/recommend")
def recommend(user_id: str, top: int = 5):
    result = get_recommendations(user_id, top)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result
