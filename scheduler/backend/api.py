"""
FastAPI server for the scheduling backend.

Endpoints
---------
GET    /api/schedule/{user_id}            – fetch all blocks for a user
GET    /api/schedule/{user_id}/fixed      – fetch only fixed blocks
GET    /api/schedule/{user_id}/generated  – fetch only generated (study) blocks
POST   /api/schedule/{user_id}/blocks     – add fixed blocks
POST   /api/schedule/{user_id}/generate   – run the scheduler and persist results
DELETE /api/schedule/{user_id}/blocks/{block_id} – delete a single fixed block
DELETE /api/schedule/{user_id}/generated  – clear generated blocks
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.scheduling import (
    Assignment,
    BlockType,
    Priority,
    Scheduler,
    SchedulerConfig,
    SchedulingStrategy,
    TimeBlock,
    make_assignment,
    make_block,
)
from database.timetable_db import (
    add_blocks as db_add_blocks,
    clear_generated_blocks,
    ensure_indexes,
    get_user_assignments,
    get_user_schedule,
    get_user_schedule_raw,
    update_block_status,
)

app = FastAPI(title="Scheduling API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure MongoDB indexes on startup
ensure_indexes()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {"status": "ok", "message": "Scheduling API is running. Use the frontend at http://localhost:8080"}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class BlockIn(BaseModel):
    id: Optional[str] = None
    title: str = ""
    block_type: str = "other"
    start: str
    end: str
    notes: Optional[str] = None
    attended: Optional[bool] = None


class AddBlocksRequest(BaseModel):
    blocks: list[BlockIn]


class AssignmentIn(BaseModel):
    title: str
    module_code: str = ""
    deadline: str
    base_estimated_minutes: int = 120
    strength_score: float = 0.5
    priority: str = "MEDIUM"
    description: Optional[str] = None


class SchedulerConfigIn(BaseModel):
    slot_minutes: int = 30
    min_gap_minutes: int = 10
    preferred_study_start: str = "08:00"
    preferred_study_end: str = "22:00"
    days_before_deadline_buffer: int = 0
    max_consecutive_slots: int = 4
    strategy: str = "spread_out"


class GenerateRequest(BaseModel):
    assignments: list[AssignmentIn] = []
    config: Optional[SchedulerConfigIn] = None
    scheduling_start: Optional[str] = None


class UpdateStatusRequest(BaseModel):
    status: str   # 'scheduled' | 'completed' | 'missed'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _timeblock_to_dict(block: TimeBlock) -> dict:
    return {
        "id": block.id,
        "title": block.title,
        "block_type": block.block_type.value,
        "start": block.start.isoformat(),
        "end": block.end.isoformat(),
        "duration_minutes": block.duration_minutes,
        "notes": block.notes,
        "attended": block.attended,
    }


def _block_in_to_timeblock(d: BlockIn) -> TimeBlock:
    return TimeBlock(
        id=d.id or "",
        title=d.title,
        block_type=BlockType(d.block_type),
        start=datetime.fromisoformat(d.start),
        end=datetime.fromisoformat(d.end),
        notes=d.notes,
        attended=d.attended,
    )


def _parse_time(s: str) -> time:
    parts = s.split(":")
    return time(int(parts[0]), int(parts[1]))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/schedule/{user_id}")
def get_schedule(user_id: str):
    """Return all blocks (fixed + generated) for a user, including status."""
    return get_user_schedule_raw(user_id)


@app.get("/api/schedule/{user_id}/fixed")
def get_fixed_schedule(user_id: str):
    """Return only fixed (user-entered) blocks."""
    return get_user_schedule_raw(user_id, source="fixed")


@app.get("/api/schedule/{user_id}/generated")
def get_generated_schedule(user_id: str):
    """Return only generated (study-session) blocks."""
    return get_user_schedule_raw(user_id, source="generated")


@app.post("/api/schedule/{user_id}/blocks")
def add_blocks(user_id: str, body: AddBlocksRequest):
    """Add fixed blocks to a user's schedule."""
    blocks = [_block_in_to_timeblock(b) for b in body.blocks]
    count = db_add_blocks(user_id, blocks, source="fixed")
    return {"added": count, "blocks": [_timeblock_to_dict(b) for b in blocks]}


@app.delete("/api/schedule/{user_id}/blocks/{block_id}")
def delete_block(user_id: str, block_id: str):
    """Delete a single fixed block by its block_id."""
    from database.timetable_db import _get_db

    db = _get_db()
    result = db["timetable_blocks"].delete_one(
        {"user_id": user_id, "block_id": block_id, "source": "fixed"}
    )
    return {"deleted": result.deleted_count}


@app.delete("/api/schedule/{user_id}/generated")
def delete_generated(user_id: str):
    """Clear all generated blocks for a user."""
    deleted = clear_generated_blocks(user_id)
    return {"deleted": deleted}


@app.patch("/api/schedule/{user_id}/blocks/{block_id}/status")
def update_status(user_id: str, block_id: str, body: UpdateStatusRequest):
    """Update the status of a block (scheduled / completed / missed)."""
    count = update_block_status(user_id, block_id, body.status)
    return {"updated": count, "block_id": block_id, "status": body.status}


@app.post("/api/schedule/{user_id}/generate")
def generate_schedule(user_id: str, body: GenerateRequest):
    """Run the scheduler over the user's fixed blocks + provided assignments."""
    # Build assignments – if none supplied, pull from DB
    if body.assignments:
        assignments = [
            make_assignment(
                title=a.title,
                module_code=a.module_code,
                deadline=datetime.fromisoformat(a.deadline),
                base_estimated_minutes=a.base_estimated_minutes,
                strength_score=a.strength_score,
                priority=Priority[a.priority.upper()],
                description=a.description,
            )
            for a in body.assignments
        ]
    else:
        db_assignments = get_user_assignments(user_id)
        assignments = [
            make_assignment(
                title=a["name"],
                module_code=a["topic"],
                deadline=a["due_date"] if isinstance(a["due_date"], datetime) else datetime.fromisoformat(str(a["due_date"])),
                base_estimated_minutes=a.get("duration_minutes", 240),
                strength_score=a.get("strength_score", 0.5),
                priority=Priority.MEDIUM,
            )
            for a in db_assignments
        ]

    # Build config
    rc = body.config or SchedulerConfigIn()
    config = SchedulerConfig(
        slot_minutes=rc.slot_minutes,
        min_gap_minutes=rc.min_gap_minutes,
        preferred_study_start=_parse_time(rc.preferred_study_start),
        preferred_study_end=_parse_time(rc.preferred_study_end),
        days_before_deadline_buffer=rc.days_before_deadline_buffer,
        max_consecutive_slots=rc.max_consecutive_slots,
        strategy=SchedulingStrategy(rc.strategy),
    )

    scheduling_start: Optional[datetime] = None
    if body.scheduling_start:
        scheduling_start = datetime.fromisoformat(body.scheduling_start)

    # Get the user's fixed blocks from DB
    fixed_blocks = get_user_schedule(user_id, source="fixed")

    # Clear old generated blocks
    clear_generated_blocks(user_id)

    # Run scheduler
    scheduler = Scheduler(config)
    result = scheduler.build(
        timetable_blocks=fixed_blocks,
        assignments=assignments,
        scheduling_start=scheduling_start,
    )

    # Persist generated study blocks
    db_add_blocks(user_id, result.new_study_blocks, source="generated")

    return result.to_dict()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
