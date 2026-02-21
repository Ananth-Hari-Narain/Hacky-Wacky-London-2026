"""
extract_topics.py

Reads lecture-note PDFs from a directory, sends the combined text to
GPT-4o-mini, and extracts a master list of topic tags. The tag list is
stored in MongoDB Atlas (question_bank.topics) as a single document.

Usage:
    python extract_topics.py [--lectures-dir ./lectures]

Environment variables (set in .env or shell):
    MONGODB_URI    - MongoDB Atlas connection string
    OPENAI_API_KEY - OpenAI API key
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import certifi
import fitz  # pymupdf
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

EXTRACT_SYSTEM_PROMPT = """\
You are an academic curriculum analyst. You will receive the combined text \
of university lecture notes. Your job is to extract every distinct topic, \
concept, technique, algorithm, data structure, and theorem covered.

Return ONLY a JSON object with a single key "tags" whose value is an array \
of short tag strings (2-5 words each). Be exhaustive — include every topic \
you can identify, no matter how small. Prefer specific tags over vague ones \
(e.g. "Depth-First Search" not "Graph Stuff").

Example output:
{"tags": ["Depth-First Search", "Breadth-First Search", "Binary Trees", ...]}\
"""

# GPT-4o-mini context is 128k tokens; we cap the lecture text to stay safe.
MAX_LECTURE_CHARS = 400_000


# ---------------------------------------------------------------------------
# PDF parsing (reused from embed_questions.py)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# MongoDB helpers
# ---------------------------------------------------------------------------

def get_db(uri: str, db_name: str):
    client = MongoClient(uri, server_api=ServerApi("1"), tlsCAFile=certifi.where())
    return client[db_name]


# ---------------------------------------------------------------------------
# Topic extraction
# ---------------------------------------------------------------------------

def extract_tags(client: OpenAI, lecture_text: str) -> list[str]:
    """Send lecture text to GPT-4o-mini and get back a list of topic tags."""
    # Truncate if too long
    if len(lecture_text) > MAX_LECTURE_CHARS:
        print(f"  Lecture text is {len(lecture_text)} chars, truncating to {MAX_LECTURE_CHARS}")
        lecture_text = lecture_text[:MAX_LECTURE_CHARS]

    response = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": lecture_text},
        ],
    )

    parsed = json.loads(response.choices[0].message.content)

    if isinstance(parsed, list):
        return parsed
    if "tags" in parsed:
        return parsed["tags"]
    # Fallback: take the first list value
    return next(v for v in parsed.values() if isinstance(v, list))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(lectures_dir: Path):
    oai = OpenAI(api_key=OPENAI_API_KEY)
    db = get_db(MONGODB_URI, MONGODB_DB)

    pdf_files = sorted(lectures_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDFs found in {lectures_dir}")
        sys.exit(0)

    print(f"Found {len(pdf_files)} lecture PDF(s)")

    # Combine all lecture text
    all_text_parts = []
    source_files = []
    for pdf_path in pdf_files:
        print(f"  Reading {pdf_path.name} ...")
        text = extract_full_text(pdf_path)
        all_text_parts.append(f"=== {pdf_path.name} ===\n{text}")
        source_files.append(pdf_path.name)

    combined_text = "\n\n".join(all_text_parts)
    print(f"\nTotal lecture text: {len(combined_text)} chars")

    # Extract tags
    print(f"Asking {MODEL} to extract topic tags ...")
    tags = extract_tags(oai, combined_text)

    # Deduplicate and sort
    tags = sorted(set(tags))
    print(f"\nExtracted {len(tags)} unique tags:")
    for i, tag in enumerate(tags):
        print(f"  {i + 1:3d}. {tag}")

    # Store in MongoDB
    topics_collection = db["topics"]
    topics_collection.update_one(
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
    print(f"\nStored {len(tags)} tags in {MONGODB_DB}.topics")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract topic tags from lecture PDFs")
    parser.add_argument(
        "--lectures-dir",
        default="./lectures",
        help="Directory containing lecture PDFs (default: ./lectures)",
    )
    args = parser.parse_args()
    lectures_dir = Path(args.lectures_dir).expanduser().resolve()
    if not lectures_dir.is_dir():
        print(f"Error: directory not found: {lectures_dir}")
        sys.exit(1)
    main(lectures_dir)
