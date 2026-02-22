"""
Create synthetic test users.

Usage:
    python -m scripts.seed_users
"""
import random
from datetime import datetime, timezone

from pymongo import UpdateOne

from models.db import users_coll, topics_coll


def make_vector(n: int, strategy: str) -> list[float]:
    match strategy:
        case "strong_theory":
            return [round(random.uniform(0.7, 1.0), 2) if i < n // 2
                    else round(random.uniform(0.0, 0.3), 2)
                    for i in range(n)]
        case "strong_practical":
            return [round(random.uniform(0.0, 0.3), 2) if i < n // 2
                    else round(random.uniform(0.7, 1.0), 2)
                    for i in range(n)]
        case "beginner":
            return [round(random.uniform(0.0, 0.2), 2) for _ in range(n)]
        case "advanced":
            return [round(random.uniform(0.7, 0.95), 2) for _ in range(n)]
        case "mixed":
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
    topics_doc = topics_coll().find_one({"_id": "master"})
    if not topics_doc:
        print("Error: no master tag list found. Run extract_topics first.")
        return

    tags = topics_doc["tags"]
    n_tags = len(tags)
    print(f"Loaded {n_tags} tags\n")

    random.seed(42)

    ops = []
    for user in USERS:
        vec = make_vector(n_tags, user["strategy"])
        print(f"  {user['name']:8s} ({user['user_id']}) — {user['description']}")

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

    result = users_coll().bulk_write(ops, ordered=False)
    total = result.upserted_count + result.modified_count
    print(f"\nUpserted {total} user(s)")


if __name__ == "__main__":
    main()
