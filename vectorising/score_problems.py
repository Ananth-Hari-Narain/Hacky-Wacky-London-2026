"""
score_problems.py

Reads the master topic tag list from MongoDB, then for each problem in
question_bank.questions, asks GPT-4o-mini to rate how relevant each topic
is (0.0–1.0). The resulting topic_vector is stored on each problem document.

Usage:
    python score_problems.py

Environment variables (set in .env or shell):
    MONGODB_URI    - MongoDB Atlas connection string
    OPENAI_API_KEY - OpenAI API key
"""

import json
import os
import sys
from datetime import datetime, timezone

import certifi
from dotenv import load_dotenv
from openai import OpenAI
from pymongo import MongoClient
from pymongo.server_api import ServerApi

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MONGODB_URI = os.environ["MONGODB_URI"]
MONGODB_DB = os.getenv("MONGODB_DB", "question_bank")
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
MODEL = "gpt-4o-mini"

SCORING_SYSTEM_PROMPT = """\
You are an academic problem classifier. You will receive:
1. A list of topic tags.
2. The text of a single exam/exercise problem.

Your job is to rate how relevant each topic is to solving this problem. \
You MUST use the full range from 0.0 to 1.0 in increments of 0.1.

Scoring guide:
- 0.0 = completely irrelevant, topic never appears
- 0.1 = barely related, only a passing mention
- 0.2 = minor background knowledge needed
- 0.3 = supporting concept, used as a tool within the problem
- 0.4 = moderately relevant, tested indirectly
- 0.5 = significant part of the problem
- 0.6 = major component, directly tested
- 0.7 = core topic, most of the problem involves this
- 0.8 = primary focus of the problem
- 0.9 = the problem is almost entirely about this
- 1.0 = the problem is fundamentally and exclusively about this

Rules:
- Most topics should score 0.0 for any given problem.
- Do NOT default to 0.0, 0.5, and 1.0. Use intermediate values like \
0.2, 0.3, 0.6, 0.7, 0.8 where appropriate.
- A problem can have multiple topics scoring above 0.5 if it truly \
covers multiple areas.
- Return ONLY a JSON object mapping each tag to its float score. \
Include ALL tags, even those scoring 0.0.\
"""


# ---------------------------------------------------------------------------
# MongoDB helpers
# ---------------------------------------------------------------------------

def get_db(uri: str, db_name: str):
    client = MongoClient(uri, server_api=ServerApi("1"), tlsCAFile=certifi.where())
    return client[db_name]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_problem(client: OpenAI, tags: list[str], problem_text: str) -> list[float]:
    """
    Ask GPT-4o-mini to score each tag for relevance to the problem.
    Returns a list of floats in the same order as *tags*.
    """
    tags_str = json.dumps(tags)

    response = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SCORING_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Topic tags:\n{tags_str}\n\n"
                    f"Problem text:\n{problem_text}"
                ),
            },
        ],
    )

    parsed = json.loads(response.choices[0].message.content)

    # Build the ordered vector — default 0.0 for any tag the model missed
    vector = [float(parsed.get(tag, 0.0)) for tag in tags]
    return vector


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    oai = OpenAI(api_key=OPENAI_API_KEY)
    db = get_db(MONGODB_URI, MONGODB_DB)

    # 1. Fetch the master tag list
    topics_doc = db["topics"].find_one({"_id": "master"})
    if not topics_doc:
        print("Error: no master tag list found. Run extract_topics.py first.")
        sys.exit(1)

    tags = topics_doc["tags"]
    print(f"Loaded {len(tags)} tags from question_bank.topics")

    # 2. Fetch all problems (skip Preamble / Appendix)
    questions = list(
        db["questions"].find({
            "question": {"$not": {"$regex": "^(Preamble|Appendix)", "$options": "i"}}
        })
    )
    if not questions:
        print("No questions found in question_bank.questions")
        sys.exit(0)

    print(f"Found {len(questions)} problem(s) to score\n")

    # 3. Score each problem
    for i, q in enumerate(questions):
        label = q.get("question", q.get("chunk_index", "?"))
        source = q.get("source", "?")
        print(f"  [{i + 1}/{len(questions)}] {source} — {label} ... ", end="", flush=True)

        vector = score_problem(oai, tags, q["text"])

        # Print nonzero scores
        nonzero = [(tags[j], v) for j, v in enumerate(vector) if v > 0]
        if nonzero:
            scores_str = ", ".join(f"{tag}: {v:.1f}" for tag, v in nonzero)
            print(scores_str)
        else:
            print("(no topics matched)")

        # 4. Update the document
        db["questions"].update_one(
            {"_id": q["_id"]},
            {
                "$set": {
                    "topic_vector": vector,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )

    print(f"\nDone. Scored {len(questions)} problem(s).")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
