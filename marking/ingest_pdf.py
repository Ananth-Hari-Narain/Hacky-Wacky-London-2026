import os
import sys
from datetime import datetime
from typing import List

from pypdf import PdfReader

from marking.db import qb
from marking.openai_client import client as openai_client


def extract_pdf_text(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    pages = []
    for p in reader.pages:
        pages.append((p.extract_text() or "").strip())
    full_text = "\n\n".join([t for t in pages if t]).strip()
    return full_text


def chunk_text(text: str, chunk_chars: int = 3500, overlap_chars: int = 600) -> List[str]:
    cleaned = text.replace("\r", "").strip()
    chunks = []
    i = 0
    while i < len(cleaned):
        chunk = cleaned[i : i + chunk_chars].strip()
        if chunk:
            chunks.append(chunk)
        i += (chunk_chars - overlap_chars)
    return chunks


def embed(text: str) -> List[float]:
    resp = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return resp.data[0].embedding


def ingest(pdf_path: str, target_collection: str):
    source = os.path.basename(pdf_path)
    text = extract_pdf_text(pdf_path)

    if not text:
        raise RuntimeError(
            "No text extracted from PDF. If this PDF is scanned, you'll need OCR."
        )

    chunks = chunk_text(text)
    print(f"[ingest] {source} -> {len(chunks)} chunks into {target_collection}")

    col = qb()[target_collection]

    for idx, ch in enumerate(chunks):
        doc = {
            "source": source,
            "chunk_index": idx,
            "text": ch,
            "embedding": embed(ch),
            "updated_at": datetime.utcnow()
        }
        col.insert_one(doc)
        if idx % 10 == 0:
            print(f"[ingest] inserted {idx}/{len(chunks)}")

    print("[ingest] ✅ done")


if __name__ == "__main__":
    """
    Usage:
      python marking/ingest_pdf.py solution ./solutions/first_PMT_solutions.pdf
      python marking/ingest_pdf.py notes ./lectures/week3_notes.pdf
    """
    if len(sys.argv) < 3:
        print("Usage: python marking/ingest_pdf.py <solution|notes> <pdf_path>")
        sys.exit(1)

    kind = sys.argv[1].strip().lower()
    pdf_path = sys.argv[2].strip()

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(pdf_path)

    if kind == "solution":
        target = "solution_chunks"
    elif kind == "notes":
        target = "notes_chunks"
    else:
        raise ValueError("First arg must be 'solution' or 'notes'")

    ingest(pdf_path, target)