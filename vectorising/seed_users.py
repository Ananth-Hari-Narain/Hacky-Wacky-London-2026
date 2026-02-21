"""
seed_users.py

Creates synthetic test users in question_bank.users, each with a
topic_vector representing their proficiency (0.0 = weak, 1.0 = strong)
across every topic in the master tag list.

Usage:
    python seed_users.py

Environment variables (set in .env or shell):
    MONGODB_URI - MongoDB Atlas connection string
"""

import os
import random
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


def make_vector(n: int, strategy: str) -> list[float]:
    """Generate a topic proficiency vector of length n."""
    match strategy:
        case "strong_theory":
            # Strong on first half of topics (theory-heavy), weak on rest
            return [round(random.uniform(0.7, 1.0), 2) if i < n // 2
                    else round(random.uniform(0.0, 0.3), 2)
                    for i in range(n)]
        case "strong_practical":
            # Opposite: weak on first half, strong on second
            return [round(random.uniform(0.0, 0.3), 2) if i < n // 2
                    else round(random.uniform(0.7, 1.0), 2)
                    for i in range(n)]
        case "beginner":
            # Uniformly weak
            return [round(random.uniform(0.0, 0.2), 2) for _ in range(n)]
        case "advanced":
            # Uniformly strong
            return [round(random.uniform(0.7, 0.95), 2) for _ in range(n)]
        case "mixed":
            # Random mix
            return [round(random.uniform(0.0, 1.0), 2) for _ in range(n)]
        case _:
            return [0.0] * n


USERS = [
    {"user_id": "user_01", "name": "Alice",   "strategy": "strong_theory",
     "description": "Strong in theory, weak in practical topics"},
    {"user_id": "user_02", "name": "Bob",     "strategy": "strong_practical",
     "description": "Strong in practical topics, weak in theory"},
    {"user_id": "user_03", "name": "Charlie", "strategy": "beginner",
     "description": "New student, weak across the board"},
    {"user_id": "user_04", "name": "Diana",   "strategy": "advanced",
     "description": "Top student, strong across the board"},
    {"user_id": "user_05", "name": "Eve",     "strategy": "mixed",
     "description": "Mixed strengths and weaknesses"},
]


def main():
    db = get_db(MONGODB_URI, MONGODB_DB)

    # Fetch master tag list
    topics_doc = db["topics"].find_one({"_id": "master"})
    if not topics_doc:
        print("Error: no master tag list found. Run extract_topics.py first.")
        sys.exit(1)

    tags = topics_doc["tags"]
    n_tags = len(tags)
    print(f"Loaded {n_tags} tags\n")

    random.seed(42)  # reproducible

    ops = []
    for user in USERS:
        vec = make_vector(n_tags, user["strategy"])
        print(f"  {user['name']:8s} ({user['user_id']}) — {user['description']}")

        # Show a few sample scores
        samples = [(tags[i], vec[i]) for i in range(min(5, n_tags))]
        sample_str = ", ".join(f"{t}: {v:.2f}" for t, v in samples)
        print(f"           sample: {sample_str} ...")

        ops.append(UpdateOne(
            {"user_id": user["user_id"]},
            {"$set": {
                "user_id": user["user_id"],
                "name": user["name"],
                "description": user["description"],
                "topic_vector": vec,
                "completed_problems": [],
                "updated_at": datetime.now(timezone.utc),
            }},
            upsert=True,
        ))

    result = db["users"].bulk_write(ops, ordered=False)
    total = result.upserted_count + result.modified_count
    print(f"\nUpserted {total} user(s) into question_bank.users")


if __name__ == "__main__":
    main()
