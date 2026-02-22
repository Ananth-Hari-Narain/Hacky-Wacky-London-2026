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

# ── Grader database (existing) ────────────────────────────────────────────────
_grader_db = _client["grader"]
submissions = _grader_db["submissions"]


def save_submission(data: dict):
    submissions.insert_one(data)


# ── Question bank database (shared with api/) ─────────────────────────────────
_db_name = os.getenv("MONGODB_DB", "question_bank")


def qb():
    """Return the question_bank MongoDatabase."""
    return _client[_db_name]


# ── Image submissions ─────────────────────────────────────────────────────────
def save_image_submission(data: dict) -> str:
    result = _grader_db["image_submissions"].insert_one(data)
    return str(result.inserted_id)


def get_image_submission(submission_id: str) -> dict:
    return _grader_db["image_submissions"].find_one({"_id": ObjectId(submission_id)})


# ── Chat sessions ─────────────────────────────────────────────────────────────
def create_chat_session(data: dict) -> str:
    result = _grader_db["chat_sessions"].insert_one(data)
    return str(result.inserted_id)


def get_chat_session(session_id: str) -> dict:
    return _grader_db["chat_sessions"].find_one({"_id": ObjectId(session_id)})


def append_chat_message(session_id: str, role: str, content: str):
    _grader_db["chat_sessions"].update_one(
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
