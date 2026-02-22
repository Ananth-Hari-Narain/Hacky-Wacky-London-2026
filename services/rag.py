"""
RAG (Retrieval-Augmented Generation) helper.

Searches MongoDB collections using cosine similarity against a query
embedding, and returns the top-k most relevant text passages.
"""
from typing import List

from config.settings import EMBEDDING_MODEL
from models.db import qb
from services.openai_client import client as openai_client


def _embed(text: str) -> List[float]:
    resp = openai_client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    return resp.data[0].embedding


def _cosine_sim(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def search_chunks(query: str, collections: List[str], top_k: int = 3) -> str:
    if not query.strip():
        return ""

    query_emb = _embed(query)
    db = qb()

    results = []
    for col_name in collections:
        col = db[col_name]
        for doc in col.find({}, {"text": 1, "embedding": 1, "source": 1}):
            emb = doc.get("embedding")
            if not emb:
                continue
            sim = _cosine_sim(query_emb, emb)
            results.append((sim, doc.get("text", ""), doc.get("source", "")))

    results.sort(key=lambda x: x[0], reverse=True)

    if not results:
        return ""

    chunks = []
    for _, text, source in results[:top_k]:
        chunks.append(f"[Source: {source}]\n{text}")
    return "\n\n---\n\n".join(chunks)
