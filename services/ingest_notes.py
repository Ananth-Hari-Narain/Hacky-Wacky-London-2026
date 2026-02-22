"""
RAG ingestion for lecture notes and solution PDFs.

Chunks text using whitespace-aware splitting, embeds with OpenAI,
and stores in MongoDB for retrieval during grading.
"""
from datetime import datetime, timezone
from pathlib import Path

import fitz
from pymongo import UpdateOne

from config.settings import EMBED_BATCH_SIZE
from models.db import qb
from services.pipeline import embed_batch


def extract_text_by_page(pdf_path: Path) -> list[dict]:
    pages = []
    with fitz.open(pdf_path) as doc:
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if text:
                pages.append({"page": page_num, "text": text})
    return pages


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        chunks.append(text[start:end].strip())
        start = end - overlap
    return [c for c in chunks if c]


def ingest_pdf(pdf_path: Path, target_collection: str) -> int:
    source = pdf_path.name
    pages = extract_text_by_page(pdf_path)
    collection = qb()[target_collection]

    chunk_docs = []
    for page_data in pages:
        chunks = chunk_text(page_data["text"])
        for idx, chunk in enumerate(chunks):
            chunk_docs.append({
                "source": source,
                "page": page_data["page"],
                "chunk_index": idx,
                "text": chunk,
                "embedding": None,
                "updated_at": datetime.now(timezone.utc),
            })

    if chunk_docs:
        texts = [d["text"] for d in chunk_docs]
        all_embeddings = []
        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[i:i + EMBED_BATCH_SIZE]
            all_embeddings.extend(embed_batch(batch))
        for doc, emb in zip(chunk_docs, all_embeddings):
            doc["embedding"] = emb

    if chunk_docs:
        ops = [
            UpdateOne(
                {"source": d["source"], "page": d["page"],
                 "chunk_index": d["chunk_index"]},
                {"$set": d},
                upsert=True,
            )
            for d in chunk_docs
        ]
        collection.bulk_write(ops, ordered=False)

    return len(chunk_docs)


def ingest_notes_dir(notes_dir: Path) -> int:
    total = 0
    for pdf in sorted(notes_dir.glob("*.pdf")):
        total += ingest_pdf(pdf, "notes_chunks")
    return total
