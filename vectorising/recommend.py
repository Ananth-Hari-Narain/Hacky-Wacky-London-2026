"""
recommend.py

Given a user, recommends the best next problems to work on based on their
weakness profile.

Algorithm:
    need[i] = 1.0 - user.topic_vector[i]   (invert strengths → weaknesses)
    score   = dot(need, problem.topic_vector)
    Higher score = problem covers more of the user's weak areas.

Usage:
    python recommend.py --user user_03
    python recommend.py --user user_01 --top 10

Environment variables (set in .env or shell):
    MONGODB_URI - MongoDB Atlas connection string
"""

import argparse
import os
import sys

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.server_api import ServerApi

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
MONGODB_DB = os.getenv("MONGODB_DB", "question_bank")


def get_db(uri: str, db_name: str):
    client = MongoClient(uri, server_api=ServerApi("1"), tlsCAFile=certifi.where())
    return client[db_name]


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def main():
    parser = argparse.ArgumentParser(description="Recommend problems for a user")
    parser.add_argument("--user", required=True, help="user_id (e.g. user_03)")
    parser.add_argument("--top", type=int, default=5, help="Number of recommendations (default: 5)")
    args = parser.parse_args()

    db = get_db(MONGODB_URI, MONGODB_DB)

    # 1. Fetch user
    user = db["users"].find_one({"user_id": args.user})
    if not user:
        print(f"Error: user '{args.user}' not found.")
        print("Available users:")
        for u in db["users"].find({}, {"user_id": 1, "name": 1}):
            print(f"  {u['user_id']} — {u.get('name', '?')}")
        sys.exit(1)

    user_vec = user["topic_vector"]
    completed = set(str(pid) for pid in user.get("completed_problems", []))
    print(f"User: {user['name']} ({user['user_id']})")
    print(f"  Completed: {len(completed)} problem(s)")

    # 2. Fetch master tags for display
    topics_doc = db["topics"].find_one({"_id": "master"})
    tags = topics_doc["tags"] if topics_doc else []

    # 3. Compute need vector (weakness = 1 - strength)
    need = [1.0 - v for v in user_vec]

    # Show top weaknesses
    if tags:
        weakness_scores = sorted(zip(tags, need), key=lambda x: -x[1])
        top_weak = weakness_scores[:5]
        print(f"  Top weaknesses: {', '.join(f'{t} ({n:.2f})' for t, n in top_weak)}")

    # 4. Fetch all problem vectors
    problems = list(db["problem_vectors"].find({"topic_vector": {"$exists": True}}))
    if not problems:
        print("\nNo problem vectors found. Run build_problem_vectors.py first.")
        sys.exit(1)

    # 5. Score and rank
    scored = []
    for p in problems:
        pid = str(p.get("problem_id", p["_id"]))
        if pid in completed:
            continue
        score = dot(need, p["topic_vector"])
        scored.append((score, p))

    scored.sort(key=lambda x: -x[0])
    top_n = scored[: args.top]

    # 6. Display recommendations
    print(f"\n{'='*60}")
    print(f"Top {len(top_n)} recommended problems:")
    print(f"{'='*60}\n")

    for rank, (score, p) in enumerate(top_n, 1):
        source = p.get("source", "?")
        question = p.get("question", "?")
        print(f"  #{rank}  {source} — {question}  (score: {score:.2f})")

        # Show which weak topics this problem targets
        if tags:
            pvec = p["topic_vector"]
            targeted = [
                (tags[i], need[i] * pvec[i])
                for i in range(len(tags))
                if pvec[i] > 0.1 and need[i] > 0.3
            ]
            targeted.sort(key=lambda x: -x[1])
            if targeted:
                detail = ", ".join(f"{t} ({s:.2f})" for t, s in targeted[:5])
                print(f"       targets: {detail}")
        print()


if __name__ == "__main__":
    main()
