"""
embed_questions.py

Processes PDFs from a directory, uses GPT-4o-mini to identify question
boundaries (agentic chunking), embeds each question using OpenAI
text-embedding-3-small, and upserts the results into MongoDB Atlas.

Usage:
    python embed_questions.py [--pdf-dir ./pdfs]

Environment variables (set in .env or shell):
    MONGODB_URI         - MongoDB Atlas connection string
    MONGODB_DB          - Database name
    MONGODB_COLLECTION  - Collection name
    OPENAI_API_KEY      - OpenAI API key
    PDF_DIR             - Directory containing PDFs (overridden by --pdf-dir)
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
from pymongo import MongoClient, UpdateOne
from pymongo.server_api import ServerApi

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MONGODB_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
MONGODB_DB = os.getenv("MONGODB_DB", "question_bank")
MONGODB_COLLECTION = os.getenv("MONGODB_COLLECTION", "questions")
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
EMBEDDING_MODEL = "text-embedding-3-small"
CHUNKING_MODEL = "gpt-4o-mini"
EMBED_BATCH_SIZE = 100

CHUNKING_SYSTEM_PROMPT = """\
You are a document segmentation assistant. You will receive the full text of \
a PDF with line numbers like [1], [2], etc. Your job is to identify where \
each distinct question, problem, or exercise begins.

Return ONLY a JSON array of objects, each with:
- "line": the line number where this section starts
- "label": use the format "Question N" where N is the ACTUAL number used in \
  the document (e.g. if the text says "5. A graph is simple..." the label must \
  be "Question 5", NOT a sequential counter).

Rules:
- The first entry should cover any preamble/header text (use label "Preamble"). \
If there is no preamble, start with the first question.
- Each entry marks the START of a new section. Text runs from one start line \
to the next.
- Keep sub-parts of the same question together (e.g. parts a, b, c of Q1 \
should be one chunk) UNLESS they are clearly separate problems.
- IMPORTANT: The label number MUST match the question number in the document text. \
  Do not renumber sequentially.
- If the document ends with non-question content (e.g. acknowledgements, \
comparisons, notes, appendices), create a final entry with label "Appendix" \
so it is separated from the last real question.
- Return valid JSON only, no markdown fences, no commentary.\
"""


# ---------------------------------------------------------------------------
# PDF parsing
# ---------------------------------------------------------------------------

def _strip_page_numbers(page_text: str, page_num: int) -> str:
    """Remove standalone page-number lines that PDF renderers add as footers."""
    lines = page_text.splitlines()
    # Strip trailing lines that are just the page number (e.g. "1", "2", "iv")
    while lines and lines[-1].strip() in (str(page_num), str(page_num - 1)):
        lines.pop()
    return "\n".join(lines)


def extract_full_text(pdf_path: Path) -> str:
    """Extract all text from a PDF, stripping page-number footers."""
    parts = []
    with fitz.open(pdf_path) as doc:
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if text:
                text = _strip_page_numbers(text, page_num)
                parts.append(text)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Agentic chunking — LLM identifies question boundaries
# ---------------------------------------------------------------------------

def number_lines(text: str) -> tuple[str, list[str]]:
    """
    Prepend [N] to each line. Returns the numbered text and the raw lines.
    """
    raw_lines = text.splitlines()
    numbered = [f"[{i + 1}] {line}" for i, line in enumerate(raw_lines)]
    return "\n".join(numbered), raw_lines


def identify_breaks(client: OpenAI, text: str) -> list[dict]:
    """
    Send numbered text to GPT-4o-mini and get back question break points.
    Returns a list of {"line": int, "label": str} sorted by line number.
    """
    numbered_text, raw_lines = number_lines(text)
    total_lines = len(raw_lines)

    response = client.chat.completions.create(
        model=CHUNKING_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": CHUNKING_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"The document has {total_lines} lines. "
                    f"Here is the full text:\n\n{numbered_text}"
                ),
            },
        ],
    )

    raw_json = response.choices[0].message.content
    parsed = json.loads(raw_json)

    # The model may wrap the array in an object like {"breaks": [...]}
    if isinstance(parsed, list):
        breaks = parsed
    else:
        # Take the first list value in the dict
        breaks = next(v for v in parsed.values() if isinstance(v, list))

    # Sort by line number and clamp to valid range
    breaks = sorted(breaks, key=lambda b: b["line"])
    breaks = [b for b in breaks if 1 <= b["line"] <= total_lines]

    return breaks, raw_lines


def split_at_breaks(breaks: list[dict], raw_lines: list[str]) -> list[dict]:
    """
    Given break points and raw lines, produce chunks.
    Returns list of {"label": str, "text": str}.
    """
    chunks = []
    for i, brk in enumerate(breaks):
        start = brk["line"] - 1  # 0-indexed
        end = breaks[i + 1]["line"] - 1 if i + 1 < len(breaks) else len(raw_lines)
        chunk_text = "\n".join(raw_lines[start:end]).strip()
        if chunk_text:
            chunks.append({"label": brk["label"], "text": chunk_text})
    return chunks


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def embed_batch(client: OpenAI, texts: list[str]) -> list[list[float]]:
    """Embed a list of texts in one OpenAI batch request."""
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]


# ---------------------------------------------------------------------------
# MongoDB helpers
# ---------------------------------------------------------------------------

def get_collection(uri: str, db_name: str, collection_name: str):
    client = MongoClient(uri, server_api=ServerApi("1"), tlsCAFile=certifi.where())
    return client[db_name][collection_name]


def upsert_chunks(collection, docs: list[dict]) -> int:
    ops = [
        UpdateOne(
            {"source": d["source"], "chunk_index": d["chunk_index"]},
            {"$set": d},
            upsert=True,
        )
        for d in docs
    ]
    result = collection.bulk_write(ops, ordered=False)
    return result.upserted_count + result.modified_count


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process_pdfs(pdf_dir: Path):
    client = OpenAI(api_key=OPENAI_API_KEY)
    collection = get_collection(MONGODB_URI, MONGODB_DB, MONGODB_COLLECTION)

    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDFs found in {pdf_dir}")
        sys.exit(0)

    print(f"Found {len(pdf_files)} PDF(s) in {pdf_dir}")
    total_upserted = 0

    for pdf_path in pdf_files:
        print(f"\n--- Processing: {pdf_path.name} ---")
        full_text = extract_full_text(pdf_path)

        # Agentic chunking: ask GPT where the breaks are
        print(f"  Asking {CHUNKING_MODEL} to identify question boundaries ...")
        breaks, raw_lines = identify_breaks(client, full_text)
        chunks = split_at_breaks(breaks, raw_lines)

        print(f"  Split into {len(chunks)} chunk(s):")
        for c in chunks:
            print(f"    - {c['label']} ({len(c['text'])} chars)")

        # Build documents
        chunk_docs = [
            {
                "source": pdf_path.name,
                "chunk_index": i,
                "question": c["label"],
                "text": c["text"],
                "embedding": None,
                "updated_at": datetime.now(timezone.utc),
            }
            for i, c in enumerate(chunks)
        ]

        # Embed in batches
        texts = [d["text"] for d in chunk_docs]
        embeddings: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[i : i + EMBED_BATCH_SIZE]
            print(f"  Embedding {i + 1}–{min(i + EMBED_BATCH_SIZE, len(texts))} of {len(texts)} ...")
            embeddings.extend(embed_batch(client, batch))

        for doc, emb in zip(chunk_docs, embeddings):
            doc["embedding"] = emb

        # Upsert into MongoDB
        upserted = upsert_chunks(collection, chunk_docs)
        total_upserted += upserted
        print(f"  Upserted {upserted} document(s)")

    print(f"\nDone. Total upserted: {total_upserted}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Embed PDFs by question and store in MongoDB Atlas")
    parser.add_argument(
        "--pdf-dir",
        default=os.getenv("PDF_DIR", "./pdfs"),
        help="Directory containing PDF files (default: ./pdfs or $PDF_DIR)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    pdf_dir = Path(args.pdf_dir).expanduser().resolve()
    if not pdf_dir.is_dir():
        print(f"Error: PDF directory not found: {pdf_dir}")
        sys.exit(1)
    process_pdfs(pdf_dir)
