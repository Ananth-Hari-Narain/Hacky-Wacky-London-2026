"""
embed_pdfs.py

Processes PDFs from a directory, chunks their text, embeds each chunk
using the OpenAI text-embedding-3-small model, and upserts the results into
a MongoDB Atlas collection.

Usage:
    python embed_pdfs.py [--pdf-dir ./pdfs] [--chunk-size 800] [--chunk-overlap 100]

Environment variables (set in .env or shell):
    MONGODB_URI         - MongoDB Atlas connection string
    MONGODB_DB          - Database name
    MONGODB_COLLECTION  - Collection name
    OPENAI_API_KEY      - OpenAI API key
    PDF_DIR             - Directory containing PDFs (overridden by --pdf-dir)
"""

import argparse
import os
import sys
import time
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

MONGODB_URI = os.environ["MONGODB_URI"]
MONGODB_DB = os.getenv("MONGODB_DB", "learning_engine")
MONGODB_COLLECTION = os.getenv("MONGODB_COLLECTION", "curriculum_chunks")
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536  # text-embedding-3-small output dimension

# OpenAI allows up to 2048 inputs per request; 100 is a safe batch size.
EMBED_BATCH_SIZE = 100
EMBED_RATE_LIMIT_SLEEP = 0.0  # no artificial delay needed


# ---------------------------------------------------------------------------
# PDF parsing
# ---------------------------------------------------------------------------

def extract_text_by_page(pdf_path: Path) -> list[dict]:
    """Return a list of {page: int, text: str} dicts for every page."""
    pages = []
    with fitz.open(pdf_path) as doc:
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if text:
                pages.append({"page": page_num, "text": text})
    return pages


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    Split *text* into overlapping chunks of at most *chunk_size* characters.
    Splits prefer whitespace boundaries.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            # Walk back to the nearest whitespace so we don't cut mid-word
            boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        chunks.append(text[start:end].strip())
        start = end - overlap
    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

_openai_client: OpenAI | None = None

def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts in one OpenAI batch request."""
    global _openai_client
    response = _openai_client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]


# ---------------------------------------------------------------------------
# MongoDB helpers
# ---------------------------------------------------------------------------

def get_collection(uri: str, db_name: str, collection_name: str):
    client = MongoClient(uri, server_api=ServerApi("1"), tlsCAFile=certifi.where())
    return client[db_name][collection_name]


def upsert_chunks(collection, docs: list[dict]) -> int:
    """
    Upsert documents keyed on (source, page, chunk_index).
    Returns the number of modified/inserted documents.
    """
    ops = [
        UpdateOne(
            {
                "source": d["source"],
                "page": d["page"],
                "chunk_index": d["chunk_index"],
            },
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

def process_pdfs(pdf_dir: Path, chunk_size: int, chunk_overlap: int):
    global _openai_client
    _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    collection = get_collection(MONGODB_URI, MONGODB_DB, MONGODB_COLLECTION)

    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDFs found in {pdf_dir}")
        sys.exit(0)

    print(f"Found {len(pdf_files)} PDF(s) in {pdf_dir}")
    total_upserted = 0

    for pdf_path in pdf_files:
        print(f"\n--- Processing: {pdf_path.name} ---")
        pages = extract_text_by_page(pdf_path)
        print(f"  Extracted {len(pages)} page(s) with text")

        # Build all chunk documents for this file (without embeddings yet)
        chunk_docs = []
        for page_data in pages:
            chunks = chunk_text(page_data["text"], chunk_size, chunk_overlap)
            for idx, chunk in enumerate(chunks):
                chunk_docs.append(
                    {
                        "source": pdf_path.name,
                        "page": page_data["page"],
                        "chunk_index": idx,
                        "text": chunk,
                        "embedding": None,  # filled below
                        "updated_at": datetime.now(timezone.utc),
                    }
                )

        print(f"  Created {len(chunk_docs)} chunk(s)")

        # Embed in batches
        texts = [d["text"] for d in chunk_docs]
        embeddings: list[list[float]] = []

        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[i : i + EMBED_BATCH_SIZE]
            print(
                f"  Embedding chunks {i + 1}–{min(i + EMBED_BATCH_SIZE, len(texts))} "
                f"of {len(texts)} ...",
                end="\r",
            )
            batch_embeddings = embed_batch(batch)
            embeddings.extend(batch_embeddings)
            if i + EMBED_BATCH_SIZE < len(texts):
                time.sleep(EMBED_RATE_LIMIT_SLEEP)

        print()  # newline after \r progress

        for doc, emb in zip(chunk_docs, embeddings):
            doc["embedding"] = emb

        # Upsert into MongoDB
        upserted = upsert_chunks(collection, chunk_docs)
        total_upserted += upserted
        print(f"  Upserted {upserted} document(s) into MongoDB")

    print(f"\nDone. Total upserted: {total_upserted}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Embed PDFs and store in MongoDB Atlas")
    parser.add_argument(
        "--pdf-dir",
        default=os.getenv("PDF_DIR", "./pdfs"),
        help="Directory containing PDF files (default: ./pdfs or $PDF_DIR)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=800,
        help="Maximum characters per chunk (default: 800)",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=100,
        help="Overlap characters between consecutive chunks (default: 100)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    pdf_dir = Path(args.pdf_dir).expanduser().resolve()
    if not pdf_dir.is_dir():
        print(f"Error: PDF directory not found: {pdf_dir}")
        sys.exit(1)
    process_pdfs(pdf_dir, args.chunk_size, args.chunk_overlap)
