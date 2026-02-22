"""
Timetable persistence layer using MongoDB.

Provides two core functions:
  • get_user_schedule(user_id)  – read all timetable blocks for a user
  • add_blocks(user_id, blocks, source)  – insert blocks with a 'fixed' or
    'generated' source tag so the scheduler can regenerate without overwriting
    the user's original entries.

The `source` field is the key distinction:
  'fixed'     – manually entered by the user (lectures, sleep, societies, …)
  'generated' – created by the scheduling algorithm (study sessions)

When regenerating a schedule you can safely:
    db.timetable_blocks.delete_many({"user_id": ..., "source": "generated"})
and then re-insert the new output.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi

# Import the data-model classes from the scheduling module.
from backend.scheduling import BlockType, TimeBlock


# ---------------------------------------------------------------------------
# MongoDB connection
# ---------------------------------------------------------------------------

MONGO_URI: str = os.environ.get(
    "MONGO_URI",
    "mongodb+srv://appuser:Test12@cluster0.c2ebdhv.mongodb.net/?appName=Cluster0",
)
DB_NAME: str = os.environ.get("MONGO_DB_NAME", "hacky_wacky")

_client: MongoClient | None = None


def _get_db():
    """Return the MongoDB database handle, creating the client on first call."""
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URI, server_api=ServerApi("1"))
        # Verify connectivity once
        _client.admin.command("ping")
    return _client[DB_NAME]


def ensure_indexes() -> None:
    """Create indexes for efficient queries (idempotent)."""
    db = _get_db()
    col = db["timetable_blocks"]
    col.create_index([("user_id", 1), ("source", 1)])
    col.create_index([("user_id", 1), ("start_time", 1), ("end_time", 1)])
    col.create_index([("block_id", 1)], unique=True)


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def get_user_schedule(
    user_id: str,
    *,
    source: Optional[str] = None,
) -> list[TimeBlock]:
    """
    Retrieve the current timetable blocks for a user.

    Parameters
    ----------
    user_id : str
        The user whose schedule to fetch.
    source : str | None
        Optional filter – pass ``'fixed'`` to get only user-entered blocks,
        ``'generated'`` to get only scheduler-created blocks, or ``None``
        (default) to get everything.

    Returns
    -------
    list[TimeBlock]
        Sorted by ``start_time`` ascending.
    """
    db = _get_db()
    query: dict = {"user_id": user_id}

    if source is not None:
        query["source"] = source

    docs = db["timetable_blocks"].find(query).sort("start_time", 1)
    return [_doc_to_timeblock(doc) for doc in docs]


def add_blocks(
    user_id: str,
    blocks: list[TimeBlock],
    source: str = "fixed",
) -> int:
    """
    Insert timetable blocks for a user.

    Parameters
    ----------
    user_id : str
        The owning user.
    blocks : list[TimeBlock]
        The blocks to persist.
    source : str
        ``'fixed'`` for user-entered blocks, ``'generated'`` for scheduler
        output.  Defaults to ``'fixed'``.

    Returns
    -------
    int
        Number of documents upserted.
    """
    if source not in ("fixed", "generated"):
        raise ValueError(f"source must be 'fixed' or 'generated', got {source!r}")

    db = _get_db()
    col = db["timetable_blocks"]

    from pymongo import UpdateOne

    ops = [
        UpdateOne(
            {"block_id": block.id},
            {"$set": {
                "block_id": block.id,
                "user_id": user_id,
                "title": block.title,
                "block_type": block.block_type.value,
                "start_time": block.start,
                "end_time": block.end,
                "notes": block.notes,
                "attended": block.attended,
                "source": source,
            }},
            upsert=True,
        )
        for block in blocks
    ]

    if ops:
        result = col.bulk_write(ops)
        return result.upserted_count + result.modified_count

    return 0


# ---------------------------------------------------------------------------
# Helpers for regeneration
# ---------------------------------------------------------------------------

def clear_generated_blocks(user_id: str) -> int:
    """
    Delete all scheduler-generated blocks for a user.

    Call this before re-running the scheduler so that only the user's
    manually entered ('fixed') blocks remain.

    Returns the number of documents deleted.
    """
    db = _get_db()
    result = db["timetable_blocks"].delete_many(
        {"user_id": user_id, "source": "generated"}
    )
    return result.deleted_count


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _doc_to_timeblock(doc: dict) -> TimeBlock:
    """Convert a MongoDB document into a TimeBlock dataclass."""
    start = doc["start_time"]
    end = doc["end_time"]

    # Handle both datetime objects and ISO-format strings
    if isinstance(start, str):
        start = datetime.fromisoformat(start)
    if isinstance(end, str):
        end = datetime.fromisoformat(end)

    return TimeBlock(
        id=doc["block_id"],
        title=doc["title"],
        block_type=BlockType(doc["block_type"]),
        start=start,
        end=end,
        notes=doc.get("notes"),
        attended=bool(doc["attended"]) if doc.get("attended") is not None else None,
    )


# ---------------------------------------------------------------------------
# Quick smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uuid
    from backend.scheduling import make_block

    ensure_indexes()

    uid = str(uuid.uuid4())

    # Create a dummy user document
    db = _get_db()
    db["users"].update_one(
        {"_id": uid},
        {"$set": {"name": "Test Student", "email": f"test-{uid[:8]}@example.com"}},
        upsert=True,
    )

    # Add some fixed blocks
    fixed = [
        make_block("CS101 Lecture", BlockType.LECTURE,
                   datetime(2025, 6, 9, 9), datetime(2025, 6, 9, 10)),
        make_block("Sleep", BlockType.SLEEP,
                   datetime(2025, 6, 9, 23), datetime(2025, 6, 10, 7, 30)),
    ]
    n = add_blocks(uid, fixed, source="fixed")
    print(f"Upserted {n} fixed blocks")

    # Add a generated study session
    generated = [
        make_block("Study: CS101 – Binary Trees Essay", BlockType.STUDY_SESSION,
                   datetime(2025, 6, 9, 10, 10), datetime(2025, 6, 9, 10, 40)),
    ]
    n = add_blocks(uid, generated, source="generated")
    print(f"Upserted {n} generated blocks")

    # Read back the full schedule
    schedule = get_user_schedule(uid)
    print(f"\nFull schedule ({len(schedule)} blocks):")
    for b in schedule:
        print(f"  {b}")

    # Read only fixed
    fixed_only = get_user_schedule(uid, source="fixed")
    print(f"\nFixed only ({len(fixed_only)} blocks):")
    for b in fixed_only:
        print(f"  {b}")

    # Clear generated and verify
    deleted = clear_generated_blocks(uid)
    print(f"\nCleared {deleted} generated blocks")
    remaining = get_user_schedule(uid)
    print(f"Remaining ({len(remaining)} blocks):")
    for b in remaining:
        print(f"  {b}")

    # Cleanup test data
    db["timetable_blocks"].delete_many({"user_id": uid})
    db["users"].delete_one({"_id": uid})
    print("\nSmoke test passed ✓")
