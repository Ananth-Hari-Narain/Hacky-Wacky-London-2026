import os
from datetime import datetime, timezone
from dotenv import load_dotenv, find_dotenv
from pymongo import MongoClient
from bson import ObjectId

load_dotenv(find_dotenv())

# Support both MONGO_URI (legacy) and MONGODB_URI (main api convention)
mongo_uri = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI")
if not mongo_uri:
    raise RuntimeError("MONGO_URI not found. Put it in .env at the project root.")

_client = MongoClient(mongo_uri)
_db_name = os.getenv("MONGODB_DB", "question_bank")

# ── Shared client injection (called by api/app.py to avoid duplicate connections) ─
_external_client = None


def init_marking_db(client):
    """Let the main app share its MongoClient with the marking package."""
    global _external_client
    _external_client = client


def _get_client():
    return _external_client or _client


def qb():
    """Return the question_bank MongoDatabase."""
    return _get_client()[_db_name]


def _grader():
    """Return the grader MongoDatabase."""
    return _get_client()["grader"]


# ── Grader submissions (existing) ────────────────────────────────────────────
def save_submission(data: dict):
    _grader()["submissions"].insert_one(data)


# ── Image submissions ─────────────────────────────────────────────────────────
def save_image_submission(data: dict) -> str:
    result = _grader()["image_submissions"].insert_one(data)
    return str(result.inserted_id)


def get_image_submission(submission_id: str) -> dict:
    return _grader()["image_submissions"].find_one({"_id": ObjectId(submission_id)})


def update_image_submission(submission_id: str, fields: dict):
    _grader()["image_submissions"].update_one(
        {"_id": ObjectId(submission_id)},
        {"$set": fields},
    )


# ── Chat sessions ─────────────────────────────────────────────────────────────
def create_chat_session(data: dict) -> str:
    result = _grader()["chat_sessions"].insert_one(data)
    return str(result.inserted_id)


def get_chat_session(session_id: str) -> dict:
    return _grader()["chat_sessions"].find_one({"_id": ObjectId(session_id)})


def append_chat_message(session_id: str, role: str, content: str):
    _grader()["chat_sessions"].update_one(
        {"_id": ObjectId(session_id)},
        {
            "$push": {
                "messages": {
                    "role": role,
                    "content": content,
                    "timestamp": datetime.now(timezone.utc),
                }
            },
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
    )
