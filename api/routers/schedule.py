"""
Calendar / scheduling endpoints.

Wraps the scheduler backend (scheduler/backend/) as a FastAPI router
so it can be served alongside the existing learning engine API.
"""
import sys
from pathlib import Path

# Add scheduler/ to sys.path so its internal imports resolve
# (scheduler code uses `from backend.scheduling import ...`)
_scheduler_root = str(Path(__file__).resolve().parent.parent.parent / "scheduler")
if _scheduler_root not in sys.path:
    sys.path.insert(0, _scheduler_root)

from datetime import datetime, time
from typing import Optional

from fastapi import APIRouter
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

router = APIRouter(tags=["Schedule"])

# Ensure MongoDB indexes on startup
ensure_indexes()


# ── Pydantic models (mirrored from scheduler/backend/api.py) ──────────────

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
    estimated_minutes_override: Optional[int] = None
    strength_score: float = 0.5
    priority: str = "MEDIUM"
    description: Optional[str] = None


class SchedulerConfigIn(BaseModel):
    slot_minutes: int = 60
    min_gap_minutes: int = 5
    preferred_study_start: str = "08:00"
    preferred_study_end: str = "22:00"
    days_before_deadline_buffer: int = 0
    max_consecutive_slots: int = 3
    strategy: str = "spread_out"


class GenerateRequest(BaseModel):
    assignments: list[AssignmentIn] = []
    config: Optional[SchedulerConfigIn] = None
    scheduling_start: Optional[str] = None


class UpdateStatusRequest(BaseModel):
    status: str


class MoveBlockRequest(BaseModel):
    start: str
    end: str


# ── Helpers ────────────────────────────────────────────────────────────────

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
    import uuid
    return TimeBlock(
        id=d.id or str(uuid.uuid4()),
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


# ── Routes ─────────────────────────────────────────────────────────────────

@router.get("/api/schedule/{user_id}")
def get_schedule(user_id: str):
    return get_user_schedule_raw(user_id)


@router.post("/api/schedule/{user_id}/blocks")
def add_blocks(user_id: str, body: AddBlocksRequest):
    blocks = [_block_in_to_timeblock(b) for b in body.blocks]
    count = db_add_blocks(user_id, blocks, source="fixed")
    return {"added": count, "blocks": [_timeblock_to_dict(b) for b in blocks]}


@router.delete("/api/schedule/{user_id}/blocks/{block_id}")
def delete_block(user_id: str, block_id: str):
    from database.timetable_db import _get_db
    db = _get_db()
    result = db["timetable_blocks"].delete_one(
        {"user_id": user_id, "block_id": block_id}
    )
    return {"deleted": result.deleted_count}


@router.patch("/api/schedule/{user_id}/blocks/{block_id}/status")
def update_status(user_id: str, block_id: str, body: UpdateStatusRequest):
    count = update_block_status(user_id, block_id, body.status)
    return {"updated": count, "block_id": block_id, "status": body.status}


@router.patch("/api/schedule/{user_id}/blocks/{block_id}/move")
def move_block(user_id: str, block_id: str, body: MoveBlockRequest):
    from database.timetable_db import _get_db
    db = _get_db()
    new_start = datetime.fromisoformat(body.start.replace("Z", "+00:00")).replace(tzinfo=None)
    new_end = datetime.fromisoformat(body.end.replace("Z", "+00:00")).replace(tzinfo=None)
    result = db["timetable_blocks"].update_one(
        {"user_id": user_id, "block_id": block_id},
        {"$set": {"start_time": new_start, "end_time": new_end}},
    )
    return {"updated": result.modified_count, "block_id": block_id}


@router.post("/api/schedule/{user_id}/generate")
def generate_schedule(user_id: str, body: GenerateRequest):
    if body.assignments:
        assignments = []
        for a in body.assignments:
            asn = make_assignment(
                title=a.title,
                module_code=a.module_code,
                deadline=datetime.fromisoformat(a.deadline),
                base_estimated_minutes=a.base_estimated_minutes,
                strength_score=a.strength_score,
                priority=Priority[a.priority.upper()],
                description=a.description,
            )
            if a.estimated_minutes_override is not None:
                asn.estimated_minutes_override = a.estimated_minutes_override
            assignments.append(asn)
    else:
        db_assignments = get_user_assignments(user_id)
        assignments = [
            make_assignment(
                title=a["name"],
                module_code=a["topic"],
                deadline=(
                    a["due_date"]
                    if isinstance(a["due_date"], datetime)
                    else datetime.fromisoformat(str(a["due_date"]))
                ),
                base_estimated_minutes=a.get("duration_minutes", 240),
                strength_score=a.get("strength_score", 0.5),
                priority=Priority.MEDIUM,
            )
            for a in db_assignments
        ]

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
        scheduling_start = datetime.fromisoformat(
            body.scheduling_start.replace("Z", "+00:00")
        ).replace(tzinfo=None)

    fixed_blocks = get_user_schedule(user_id, source="fixed")

    # Only clear generated blocks for the tasks being (re)scheduled,
    # so previously scheduled tasks remain on the calendar.
    task_titles = set()
    for a in assignments:
        if a.module_code:
            task_titles.add(f"Study: {a.module_code} – {a.title}")
        else:
            task_titles.add(f"Study: {a.title}")

    from database.timetable_db import _get_db
    db = _get_db()
    if task_titles:
        db["timetable_blocks"].delete_many({
            "user_id": user_id,
            "source": "generated",
            "title": {"$in": list(task_titles)},
        })

    # Include existing generated blocks (from other tasks) so the
    # scheduler avoids double-booking those time slots.
    existing_generated = get_user_schedule(user_id, source="generated")
    all_existing_blocks = fixed_blocks + existing_generated

    scheduler = Scheduler(config)
    result = scheduler.build(
        timetable_blocks=all_existing_blocks,
        assignments=assignments,
        scheduling_start=scheduling_start,
    )

    db_add_blocks(user_id, result.new_study_blocks, source="generated")
    return result.to_dict()


@router.delete("/api/schedule/{user_id}/generated")
def delete_generated(user_id: str):
    deleted = clear_generated_blocks(user_id)
    return {"deleted": deleted}
