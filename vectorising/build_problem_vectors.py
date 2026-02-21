"""
build_problem_vectors.py

Copies topic_vector data from question_bank.questions into a dedicated
question_bank.problem_vectors collection for use by the recommender.

Usage:
    python build_problem_vectors.py

Environment variables (set in .env or shell):
    MONGODB_URI - MongoDB Atlas connection string
"""

import os
import sys
from datetime import datetime, timezone

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne
from pymongo.server_api import ServerApi

load_dotenv()

MONGODB_URI = os.environ["MONGODB_URI"]
MONGODB_DB = os.getenv("MONGODB_DB", "question_bank")


def get_db(uri: str, db_name: str):
    client = MongoClient(uri, server_api=ServerApi("1"), tlsCAFile=certifi.where())
    return client[db_name]


def main():
    db = get_db(MONGODB_URI, MONGODB_DB)

    # Fetch all questions that have a topic_vector (skip Preamble/Appendix)
    questions = list(db["questions"].find({
        "topic_vector": {"$exists": True},
        "question": {"$not": {"$regex": "^(Preamble|Appendix)", "$options": "i"}},
    }))

    if not questions:
        print("No scored questions found. Run score_problems.py first.")
        sys.exit(1)

    print(f"Found {len(questions)} scored problem(s)")

    ops = [
        UpdateOne(
            {"problem_id": q["_id"]},
            {"$set": {
                "problem_id": q["_id"],
                "source": q.get("source", ""),
                "question": q.get("question", ""),
                "text": q.get("text", ""),
                "topic_vector": q["topic_vector"],
                "updated_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
        for q in questions
    ]

    result = db["problem_vectors"].bulk_write(ops, ordered=False)
    total = result.upserted_count + result.modified_count
    print(f"Upserted {total} document(s) into problem_vectors")


if __name__ == "__main__":
    main()
