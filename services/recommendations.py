"""
Topic extraction, problem scoring, and user recommendations.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import fitz
from pymongo import UpdateOne

from config.settings import (
    EXTRACT_TOPICS_MODEL,
    SCORING_MODEL,
    MAX_LECTURE_CHARS,
    QUESTIONS_COLLECTION,
)
from config.prompts import EXTRACT_TOPICS_SYSTEM_PROMPT, SCORING_SYSTEM_PROMPT
from models.db import qb, questions_coll, topics_coll, problem_vectors_coll, users_coll
from services.openai_client import client as oai

log = logging.getLogger(__name__)


# =========================================================================
# PDF text extraction (simple — for lectures)
# =========================================================================

def _strip_page_numbers(page_text: str, page_num: int) -> str:
    lines = page_text.splitlines()
    while lines and lines[-1].strip() in (str(page_num), str(page_num - 1)):
        lines.pop()
    return "\n".join(lines)


def extract_full_text(pdf_path: Path) -> str:
    parts = []
    with fitz.open(pdf_path) as doc:
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if text:
                text = _strip_page_numbers(text, page_num)
                parts.append(text)
    return "\n\n".join(parts)


# =========================================================================
# Topic extraction
# =========================================================================

def extract_tags(lecture_text: str) -> list[str]:
    if len(lecture_text) > MAX_LECTURE_CHARS:
        lecture_text = lecture_text[:MAX_LECTURE_CHARS]

    response = oai.chat.completions.create(
        model=EXTRACT_TOPICS_MODEL,
        temperature=0,
        max_tokens=4096,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACT_TOPICS_SYSTEM_PROMPT},
            {"role": "user", "content": lecture_text},
        ],
    )

    raw = response.choices[0].message.content

    # If the response was truncated, salvage what we can
    if response.choices[0].finish_reason == "length":
        # Find the last complete quoted string before truncation
        last_quote = raw.rfind('"')
        if last_quote > 0:
            raw = raw[:last_quote + 1] + "]}"

    parsed = json.loads(raw)
    if isinstance(parsed, list):
        return parsed
    if "tags" in parsed:
        return parsed["tags"]
    return next(v for v in parsed.values() if isinstance(v, list))


def extract_topics_from_dir(lectures_dir: Path) -> list[str]:
    pdf_files = sorted(lectures_dir.glob("*.pdf"))
    if not pdf_files:
        return []

    all_text_parts = []
    source_files = []
    for pdf_path in pdf_files:
        text = extract_full_text(pdf_path)
        all_text_parts.append(f"=== {pdf_path.name} ===\n{text}")
        source_files.append(pdf_path.name)

    combined_text = "\n\n".join(all_text_parts)
    tags = sorted(set(extract_tags(combined_text)))

    topics_coll().update_one(
        {"_id": "master"},
        {
            "$set": {
                "tags": tags,
                "source_files": source_files,
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )

    return tags


# =========================================================================
# Incremental topic append + vector extension
# =========================================================================

def append_new_tags(text: str) -> tuple[list[str], int]:
    """
    Extract tags from *text*, merge into the master list (append-only),
    extend every existing topic_vector in the DB, and return
    (full_tag_list, number_of_new_tags_added).
    """
    # Get current master tags
    topics_doc = topics_coll().find_one({"_id": "master"})
    existing_tags: list[str] = topics_doc["tags"] if topics_doc else []
    existing_set = {t.lower() for t in existing_tags}

    # Extract tags from the new text
    new_raw = extract_tags(text)
    genuinely_new = [t for t in new_raw if t.lower() not in existing_set]

    if not genuinely_new:
        return existing_tags, 0

    # Append (never remove or reorder existing tags)
    merged = existing_tags + sorted(set(genuinely_new), key=str.lower)

    source_files = (topics_doc.get("source_files", []) if topics_doc else [])
    topics_coll().update_one(
        {"_id": "master"},
        {"$set": {
            "tags": merged,
            "source_files": source_files,
            "updated_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )

    # Extend every existing vector in the DB
    n_new = len(genuinely_new)
    _extend_all_vectors(n_new)

    log.info("Appended %d new tags → %d total", n_new, len(merged))
    return merged, n_new


def _extend_all_vectors(n_new: int):
    """Pad all topic_vector fields with 0.0 for *n_new* extra dimensions."""
    if n_new <= 0:
        return
    pad = [0.0] * n_new

    for coll_fn in (questions_coll, problem_vectors_coll, users_coll):
        coll = coll_fn()
        docs = coll.find(
            {"topic_vector": {"$exists": True}},
            {"_id": 1, "topic_vector": 1},
        )
        ops = []
        for doc in docs:
            ops.append(UpdateOne(
                {"_id": doc["_id"]},
                {"$set": {"topic_vector": doc["topic_vector"] + pad}},
            ))
        if ops:
            coll.bulk_write(ops, ordered=False)
            log.info("Extended %d vectors in %s", len(ops), coll.name)


def score_and_vectorise_problems(problem_ids: list):
    """
    Score only the specified problems against the current master tag list,
    then mirror their vectors into the problem_vectors collection.
    Scoring calls run in parallel for speed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    topics_doc = topics_coll().find_one({"_id": "master"})
    if not topics_doc:
        return
    tags = topics_doc["tags"]

    q_coll = questions_coll()
    pv_coll = problem_vectors_coll()

    from bson import ObjectId
    oids = [ObjectId(pid) if not isinstance(pid, ObjectId) else pid for pid in problem_ids]

    questions = [
        q for q in q_coll.find({"_id": {"$in": oids}})
        if q.get("question", "").lower() not in ("preamble", "appendix")
    ]

    if not questions:
        return

    # Score all problems in parallel (I/O-bound GPT calls)
    def _score_one(q):
        return q, score_problem(tags, q["text"])

    results = []
    with ThreadPoolExecutor(max_workers=min(len(questions), 10)) as pool:
        futures = {pool.submit(_score_one, q): q for q in questions}
        for future in as_completed(futures):
            results.append(future.result())

    # Write results to DB
    now = datetime.now(timezone.utc)
    for q, vector in results:
        q_coll.update_one(
            {"_id": q["_id"]},
            {"$set": {"topic_vector": vector, "updated_at": now}},
        )
        pv_coll.update_one(
            {"problem_id": q["_id"]},
            {"$set": {
                "problem_id": q["_id"],
                "source": q.get("source", ""),
                "question": q.get("question", ""),
                "text": q.get("text", ""),
                "topic_vector": vector,
                "updated_at": now,
            }},
            upsert=True,
        )

    log.info("Scored & vectorised %d problems", len(results))


# =========================================================================
# Problem scoring
# =========================================================================

def score_problem(tags: list[str], problem_text: str) -> list[float]:
    tags_str = json.dumps(tags)

    response = oai.chat.completions.create(
        model=SCORING_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SCORING_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Topic tags:\n{tags_str}\n\nProblem text:\n{problem_text}",
            },
        ],
    )

    parsed = json.loads(response.choices[0].message.content)
    return [float(parsed.get(tag, 0.0)) for tag in tags]


def score_all_problems():
    topics_doc = topics_coll().find_one({"_id": "master"})
    if not topics_doc:
        raise RuntimeError("No master tag list found. Run extract_topics first.")

    tags = topics_doc["tags"]
    coll = questions_coll()

    questions = list(coll.find({
        "question": {"$not": {"$regex": "^(Preamble|Appendix)", "$options": "i"}}
    }))

    for q in questions:
        vector = score_problem(tags, q["text"])
        coll.update_one(
            {"_id": q["_id"]},
            {"$set": {
                "topic_vector": vector,
                "updated_at": datetime.now(timezone.utc),
            }},
        )

    return len(questions)


# =========================================================================
# Build problem vectors
# =========================================================================

def build_problem_vectors():
    coll = questions_coll()
    pv_coll = problem_vectors_coll()

    questions = list(coll.find({
        "topic_vector": {"$exists": True},
        "question": {"$not": {"$regex": "^(Preamble|Appendix)", "$options": "i"}},
    }))

    if not questions:
        return 0

    valid_ids = [q["_id"] for q in questions]

    # Remove any stale entries that no longer correspond to a question
    pv_coll.delete_many({"problem_id": {"$nin": valid_ids}})

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

    result = pv_coll.bulk_write(ops, ordered=False)
    return result.upserted_count + result.modified_count


# =========================================================================
# Recommendations
# =========================================================================

def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def get_recommendations(user_id: str, top: int = 5) -> dict:
    user = users_coll().find_one({"user_id": user_id})
    if not user:
        return {"error": f"User {user_id} not found"}

    user_vec = user.get("topic_vector", [])
    completed = set(str(pid) for pid in user.get("completed_problems", []))
    need = [1.0 - v for v in user_vec]

    problems = list(problem_vectors_coll().find({"topic_vector": {"$exists": True}}))

    scored = []
    for p in problems:
        pid = str(p.get("problem_id", p["_id"]))
        if pid in completed:
            continue
        score = _dot(need, p["topic_vector"])
        scored.append({
            "problem_id": pid,
            "source": p.get("source", ""),
            "question": p.get("question", ""),
            "score": round(score, 3),
        })

    scored.sort(key=lambda x: -x["score"])
    return {"user_id": user_id, "recommendations": scored[:top]}
