from datetime import datetime, timezone

import certifi
from bson import ObjectId
from pymongo import MongoClient
from pymongo.server_api import ServerApi

from config.settings import (
    MONGODB_URI,
    MONGODB_DB,
    GRADER_DB,
    QUESTIONS_COLLECTION,
    USERS_COLLECTION,
    TOPICS_COLLECTION,
    PROBLEM_VECTORS_COLLECTION,
    SOLUTION_CHUNKS_COLLECTION,
    NOTES_CHUNKS_COLLECTION,
    SUBMISSIONS_COLLECTION,
    IMAGE_SUBMISSIONS_COLLECTION,
    CHAT_SESSIONS_COLLECTION,
)

# --- Singleton client ---
_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(
            MONGODB_URI,
            server_api=ServerApi("1"),
            tlsCAFile=certifi.where(),
        )
    return _client


def get_db(db_name: str = MONGODB_DB):
    return get_client()[db_name]


def qb():
    return get_db(MONGODB_DB)


def _grader():
    return get_db(GRADER_DB)


# --- Collection accessors ---

def questions_coll():
    return qb()[QUESTIONS_COLLECTION]


def users_coll():
    return qb()[USERS_COLLECTION]


def topics_coll():
    return qb()[TOPICS_COLLECTION]


def problem_vectors_coll():
    return qb()[PROBLEM_VECTORS_COLLECTION]


def solution_chunks_coll():
    return qb()[SOLUTION_CHUNKS_COLLECTION]


def notes_chunks_coll():
    return qb()[NOTES_CHUNKS_COLLECTION]


# --- Grader CRUD ---

def save_submission(data: dict):
    _grader()[SUBMISSIONS_COLLECTION].insert_one(data)


def save_image_submission(data: dict) -> str:
    result = _grader()[IMAGE_SUBMISSIONS_COLLECTION].insert_one(data)
    return str(result.inserted_id)


def get_image_submission(submission_id: str) -> dict:
    return _grader()[IMAGE_SUBMISSIONS_COLLECTION].find_one(
        {"_id": ObjectId(submission_id)}
    )


def update_image_submission(submission_id: str, fields: dict):
    _grader()[IMAGE_SUBMISSIONS_COLLECTION].update_one(
        {"_id": ObjectId(submission_id)},
        {"$set": fields},
    )


def create_chat_session(data: dict) -> str:
    result = _grader()[CHAT_SESSIONS_COLLECTION].insert_one(data)
    return str(result.inserted_id)


def get_chat_session(session_id: str) -> dict:
    return _grader()[CHAT_SESSIONS_COLLECTION].find_one(
        {"_id": ObjectId(session_id)}
    )


def append_chat_message(session_id: str, role: str, content: str):
    _grader()[CHAT_SESSIONS_COLLECTION].update_one(
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
