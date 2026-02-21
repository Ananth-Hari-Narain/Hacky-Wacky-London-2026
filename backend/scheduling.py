"""
AI Education Assistant - Scheduling Backend

Handles automatic scheduling of assignment work sessions around a student's
existing timetable (lectures, societies, sleep, etc.), using per-module
strength scores to estimate task durations.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class BlockType(Enum):
    LECTURE = "lecture"
    SEMINAR = "seminar"
    LAB = "lab"
    SOCIETY = "society"
    SLEEP = "sleep"
    SPORT = "sport"
    WORK = "work"          # part-time job, etc.
    PERSONAL = "personal"
    STUDY_SESSION = "study_session"   # blocks we *create*
    OTHER = "other"


class Priority(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


# ---------------------------------------------------------------------------
# Core Data Models
# ---------------------------------------------------------------------------

@dataclass
class TimeBlock:
    """A single block of time in the student's calendar."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    block_type: BlockType = BlockType.OTHER
    start: datetime = field(default_factory=datetime.now)
    end: datetime = field(default_factory=datetime.now)
    notes: Optional[str] = None          # e.g. lecture notes, attendance flag
    attended: Optional[bool] = None      # None = unknown / future

    # ---- derived ----
    @property
    def duration_minutes(self) -> int:
        delta = self.end - self.start
        return max(0, int(delta.total_seconds() / 60))

    def overlaps(self, other: "TimeBlock") -> bool:
        return self.start < other.end and self.end > other.start

    def __repr__(self) -> str:
        return (
            f"TimeBlock({self.block_type.value!r}, "
            f"{self.start.strftime('%a %d %b %H:%M')} – "
            f"{self.end.strftime('%H:%M')}, {self.duration_minutes}min)"
        )


@dataclass
class Assignment:
    """An assessed piece of work with a deadline.
    Prerequisite: all strength scores between 0 and 1."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    module_code: str = ""
    deadline: datetime = field(default_factory=datetime.now)
    priority: Priority = Priority.MEDIUM

    # Strength score: 0.0 (very weak) – 1.0 (very strong)
    # Used to scale estimated work time.
    strength_score: float = 0.5

    # Optionally override the raw estimate (minutes) entirely
    estimated_minutes_override: Optional[int] = None

    # Base work estimate in minutes (before strength adjustment)
    base_estimated_minutes: int = 120

    # Notes / description
    description: Optional[str] = None

    # ---- scheduling state (populated by the scheduler) ----
    scheduled_blocks: list[TimeBlock] = field(default_factory=list)
    total_scheduled_minutes: int = 0
    is_fully_scheduled: bool = False

    # ---- derived ----
    @property
    def adjusted_estimated_minutes(self) -> int:
        """
        Weaker strength → more time needed.
        strength 1.0 → base estimate
        strength 0.0 → 2× base estimate
        Linear interpolation: multiplier = 2 - strength_score
        """
        if self.estimated_minutes_override is not None:
            return self.estimated_minutes_override
        multiplier = 2.0 - max(0.0, min(1.0, self.strength_score))
        return int(self.base_estimated_minutes * multiplier)

    @property
    def remaining_minutes(self) -> int:
        return max(0, self.adjusted_estimated_minutes - self.total_scheduled_minutes)

    @property
    def remaining_slots(self) -> int:
        """Number of 30-minute slots still needed."""
        return -(-self.remaining_minutes // 30)   # ceiling division

    def __repr__(self) -> str:
        return (
            f"Assignment({self.module_code!r} – {self.title!r}, "
            f"due {self.deadline.strftime('%d %b %Y %H:%M')}, "
            f"strength={self.strength_score:.2f}, "
            f"est={self.adjusted_estimated_minutes}min)"
        )


@dataclass
class SchedulerConfig:
    """Tuning parameters for the scheduler."""
    slot_minutes: int = 30                  # granularity of work slots
    min_gap_minutes: int = 5               # buffer between a block and a study session
    max_daily_study_hours: float = 7.0      # cap per day to avoid burnout
    preferred_study_start: time = time(9, 0)
    preferred_study_end: time = time(22, 0)
    days_before_deadline_buffer: int = 1    # stop scheduling within N days of deadline
    # Scheduling strategy: "earliest_first" or "spread"
    strategy: str = "spread"
    n_weeks: Optional[int] = 2


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class Scheduler:
    """
    Builds a study schedule for a student.

    Usage
    -----
    scheduler = Scheduler(config)
    schedule = scheduler.build(timetable_blocks, assignments)
    """

    def __init__(self, config: Optional[SchedulerConfig] = None) -> None:
        self.config = config or SchedulerConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        timetable_blocks: list[TimeBlock],
        assignments: list[Assignment],
        scheduling_start: Optional[datetime] = None,
    ) -> ScheduleResult:
        """
        Main entry point.

        Parameters
        ----------
        timetable_blocks:
            All existing fixed events (lectures, sleep, societies, etc.).
        assignments:
            Assignments that need work scheduling.
        scheduling_start:
            Earliest datetime from which new sessions can be placed.
            Defaults to *now*.

        Returns
        -------
        ScheduleResult containing new study blocks and any unschedulable slots.
        """
        now = scheduling_start or datetime.now()
        # Reset any previous scheduling on assignments
        for a in assignments:
            a.scheduled_blocks = []
            a.total_scheduled_minutes = 0
            a.is_fully_scheduled = False

        # Sort assignments by deadline (nearest first), then priority desc
        sorted_assignments = sorted(
            assignments,
            key=lambda a: (a.deadline, -a.priority.value),
        )

        # Build a mutable list of all blocked intervals
        blocked: list[TimeBlock] = list(timetable_blocks)

        new_study_blocks: list[TimeBlock] = []
        unscheduled: list[tuple[Assignment, int]] = []   # (assignment, minutes_missing)

        for assignment in sorted_assignments:
            if assignment.remaining_slots == 0:
                assignment.is_fully_scheduled = True
                continue

            # Latest day we should schedule is (deadline – buffer)
            latest_dt = assignment.deadline - timedelta(
                days=self.config.days_before_deadline_buffer
            )

            free_slots = self._find_free_slots(blocked, now, latest_dt)

            if self.config.strategy == "spread":
                free_slots = self._spread_slots(free_slots, now, latest_dt)

            slots_placed = 0
            for slot_start, slot_end in free_slots:
                if assignment.remaining_slots == 0:
                    break

                # Check daily cap
                day = slot_start.date()
                day_minutes = self._scheduled_minutes_on_day(
                    new_study_blocks, day
                )
                max_day_minutes = int(self.config.max_daily_study_hours * 60)
                if day_minutes >= max_day_minutes:
                    continue

                # Clamp slot to daily cap
                available = min(
                    self.config.slot_minutes,
                    max_day_minutes - day_minutes,
                )
                if available < self.config.slot_minutes:
                    continue   # not enough room for a full slot on this day

                study_block = TimeBlock(
                    title=f"Study: {assignment.module_code} – {assignment.title}",
                    block_type=BlockType.STUDY_SESSION,
                    start=slot_start,
                    end=slot_start + timedelta(minutes=self.config.slot_minutes),
                    notes=f"assignment_id={assignment.id}",
                )

                assignment.scheduled_blocks.append(study_block)
                assignment.total_scheduled_minutes += self.config.slot_minutes
                new_study_blocks.append(study_block)
                blocked.append(study_block)   # treat as occupied from now on
                slots_placed += 1

            if assignment.remaining_minutes == 0:
                assignment.is_fully_scheduled = True
            else:
                unscheduled.append((assignment, assignment.remaining_minutes))

        return ScheduleResult(
            new_study_blocks=new_study_blocks,
            assignments=sorted_assignments,
            unscheduled_assignments=unscheduled,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _find_free_slots(
        self,
        blocked: list[TimeBlock],
        search_from: datetime,
        search_until: datetime,
    ) -> list[tuple[datetime, datetime]]:
        """
        Return a sorted list of 30-minute slot starts that are:
          • within preferred study hours
          • not overlapping any existing block (with gap buffer)
          • within [search_from, search_until]
        """
        slots: list[tuple[datetime, datetime]] = []
        slot_delta = timedelta(minutes=self.config.slot_minutes)
        gap = timedelta(minutes=self.config.min_gap_minutes)

        # Expand each blocked interval by the gap buffer for collision detection
        buffered_blocked: list[tuple[datetime, datetime]] = []
        for b in blocked:
            buffered_blocked.append((b.start - gap, b.end + gap))

        # Walk minute-by-minute aligned to slot boundaries from search_from
        cursor = self._align_to_slot(search_from)

        while cursor + slot_delta <= search_until:
            slot_end = cursor + slot_delta

            # Within preferred hours?
            if not self._within_preferred_hours(cursor, slot_end):
                cursor += slot_delta
                continue

            # Overlaps any blocked interval?
            if self._is_blocked(cursor, slot_end, buffered_blocked):
                cursor += slot_delta
                continue

            slots.append((cursor, slot_end))
            cursor += slot_delta

        return slots

    def _spread_slots(
        self,
        slots: list[tuple[datetime, datetime]],
        start: datetime,
        end: datetime,
    ) -> list[tuple[datetime, datetime]]:
        """
        Re-order slots so they are distributed across the available date range
        rather than front-loaded, to encourage a healthy spread of study.
        Implements a simple interleaving: pick one slot per day in round-robin.
        """
        if not slots:
            return slots

        from collections import defaultdict
        day_buckets: dict[date, list[tuple[datetime, datetime]]] = defaultdict(list)
        for s, e in slots:
            day_buckets[s.date()].append((s, e))

        # Interleave day by day
        sorted_days = sorted(day_buckets.keys())
        result: list[tuple[datetime, datetime]] = []
        max_per_day = max(len(v) for v in day_buckets.values())
        for i in range(max_per_day):
            for d in sorted_days:
                if i < len(day_buckets[d]):
                    result.append(day_buckets[d][i])
        return result

    def _align_to_slot(self, dt: datetime) -> datetime:
        """Round up dt to the next multiple of slot_minutes."""
        minutes = dt.hour * 60 + dt.minute
        remainder = minutes % self.config.slot_minutes
        if remainder == 0:
            return dt.replace(second=0, microsecond=0)
        add = self.config.slot_minutes - remainder
        return (dt + timedelta(minutes=add)).replace(second=0, microsecond=0)

    def _within_preferred_hours(self, start: datetime, end: datetime) -> bool:
        pref_start = datetime.combine(start.date(), self.config.preferred_study_start)
        pref_end = datetime.combine(start.date(), self.config.preferred_study_end)
        return start >= pref_start and end <= pref_end

    @staticmethod
    def _is_blocked(
        slot_start: datetime,
        slot_end: datetime,
        buffered_blocked: list[tuple[datetime, datetime]],
    ) -> bool:
        for b_start, b_end in buffered_blocked:
            if slot_start < b_end and slot_end > b_start:
                return True
        return False

    @staticmethod
    def _scheduled_minutes_on_day(blocks: list[TimeBlock], day: date) -> int:
        return sum(
            b.duration_minutes
            for b in blocks
            if b.start.date() == day and b.block_type == BlockType.STUDY_SESSION
        )


# ---------------------------------------------------------------------------
# Result Container
# ---------------------------------------------------------------------------

@dataclass
class ScheduleResult:
    """Output of Scheduler.build()."""
    new_study_blocks: list[TimeBlock]
    assignments: list[Assignment]
    unscheduled_assignments: list[tuple[Assignment, int]]   # (assignment, missing_minutes)

    # ---- summary helpers ----

    @property
    def fully_scheduled_count(self) -> int:
        return sum(1 for a in self.assignments if a.is_fully_scheduled)

    @property
    def partially_scheduled_count(self) -> int:
        return len(self.unscheduled_assignments)

    def summary(self) -> str:
        lines = [
            "=" * 60,
            "  SCHEDULE SUMMARY",
            "=" * 60,
            f"  Assignments processed : {len(self.assignments)}",
            f"  Fully scheduled       : {self.fully_scheduled_count}",
            f"  Partially scheduled   : {self.partially_scheduled_count}",
            f"  New study blocks      : {len(self.new_study_blocks)}",
            "",
        ]
        for assignment in self.assignments:
            status = "✓ fully scheduled" if assignment.is_fully_scheduled else "⚠ partially scheduled"
            lines.append(
                f"  [{assignment.module_code}] {assignment.title}"
                f"  ({status})"
            )
            lines.append(
                f"       Est. work : {assignment.adjusted_estimated_minutes} min "
                f"| Scheduled : {assignment.total_scheduled_minutes} min "
                f"| Remaining : {assignment.remaining_minutes} min"
            )
            lines.append(
                f"       Deadline  : {assignment.deadline.strftime('%d %b %Y %H:%M')}"
                f"  |  Strength score : {assignment.strength_score:.2f}"
            )
            if assignment.scheduled_blocks:
                lines.append("       Sessions  :")
                for b in assignment.scheduled_blocks:
                    lines.append(
                        f"         • {b.start.strftime('%a %d %b %H:%M')} – "
                        f"{b.end.strftime('%H:%M')}"
                    )
            lines.append("")

        if self.unscheduled_assignments:
            lines.append("Could not fully schedule:")
            for a, missing in self.unscheduled_assignments:
                lines.append(
                    f"    - {a.title} ({a.module_code}): "
                    f"{missing} min unscheduled"
                )
        lines.append("=" * 60)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialisable representation for API responses."""
        return {
            "summary": {
                "assignments_processed": len(self.assignments),
                "fully_scheduled": self.fully_scheduled_count,
                "partially_scheduled": self.partially_scheduled_count,
                "new_study_blocks": len(self.new_study_blocks),
            },
            "assignments": [
                {
                    "id": a.id,
                    "title": a.title,
                    "module_code": a.module_code,
                    "deadline": a.deadline.isoformat(),
                    "priority": a.priority.name,
                    "strength_score": a.strength_score,
                    "estimated_minutes": a.adjusted_estimated_minutes,
                    "scheduled_minutes": a.total_scheduled_minutes,
                    "remaining_minutes": a.remaining_minutes,
                    "is_fully_scheduled": a.is_fully_scheduled,
                    "sessions": [
                        {
                            "id": b.id,
                            "start": b.start.isoformat(),
                            "end": b.end.isoformat(),
                            "duration_minutes": b.duration_minutes,
                        }
                        for b in a.scheduled_blocks
                    ],
                }
                for a in self.assignments
            ],
            "unscheduled": [
                {
                    "assignment_id": a.id,
                    "title": a.title,
                    "module_code": a.module_code,
                    "missing_minutes": missing,
                }
                for a, missing in self.unscheduled_assignments
            ],
        }


# ---------------------------------------------------------------------------
# Convenience factory helpers
# ---------------------------------------------------------------------------

def make_block(
    title: str,
    block_type: BlockType,
    start: datetime,
    end: datetime,
    notes: Optional[str] = None,
    attended: Optional[bool] = None,
) -> TimeBlock:
    return TimeBlock(
        title=title,
        block_type=block_type,
        start=start,
        end=end,
        notes=notes,
        attended=attended,
    )


def make_assignment(
    title: str,
    module_code: str,
    deadline: datetime,
    base_estimated_minutes: int = 120,
    strength_score: float = 0.5,
    priority: Priority = Priority.MEDIUM,
    description: Optional[str] = None,
) -> Assignment:
    return Assignment(
        title=title,
        module_code=module_code,
        deadline=deadline,
        base_estimated_minutes=base_estimated_minutes,
        strength_score=strength_score,
        priority=priority,
        description=description,
    )


# ---------------------------------------------------------------------------
# Demo / smoke test
# ---------------------------------------------------------------------------

def _demo() -> None:
    """
    Quick demonstration of the scheduler with realistic sample data.
    Monday 2025-06-09  →  Friday 2025-06-13
    """
    monday = datetime(2025, 6, 9)

    def dt(day_offset: int, hour: int, minute: int = 0) -> datetime:
        return monday + timedelta(days=day_offset, hours=hour, minutes=minute)

    # ------------------------------------------------------------------
    # Student timetable
    # ------------------------------------------------------------------
    timetable: list[TimeBlock] = [
        # --- Monday ---
        make_block("Sleep",              BlockType.SLEEP,    dt(0, 0),   dt(0, 7, 30)),
        make_block("CS101 Lecture",      BlockType.LECTURE,  dt(0, 9),   dt(0, 10),
                   notes="Covered binary trees. Attended.", attended=True),
        make_block("MATH201 Lecture",    BlockType.LECTURE,  dt(0, 11),  dt(0, 12),
                   notes="Integration by parts.", attended=True),
        make_block("Lunch",              BlockType.PERSONAL, dt(0, 12),  dt(0, 13)),
        make_block("CS101 Lab",          BlockType.LAB,      dt(0, 14),  dt(0, 16)),
        make_block("Football Society",   BlockType.SOCIETY,  dt(0, 18),  dt(0, 20)),
        make_block("Sleep",              BlockType.SLEEP,    dt(0, 23),  dt(1, 7, 30)),

        # --- Tuesday ---
        make_block("PHYS301 Lecture",    BlockType.LECTURE,  dt(1, 10),  dt(1, 11),
                   notes="Quantum mechanics intro.", attended=True),
        make_block("MATH201 Seminar",    BlockType.SEMINAR,  dt(1, 13),  dt(1, 14)),
        make_block("Part-time job",      BlockType.WORK,     dt(1, 16),  dt(1, 20)),
        make_block("Sleep",              BlockType.SLEEP,    dt(1, 23),  dt(2, 7, 30)),

        # --- Wednesday ---
        make_block("CS101 Lecture",      BlockType.LECTURE,  dt(2, 9),   dt(2, 10),
                   notes="Graphs and BFS/DFS."),
        make_block("Gym",                BlockType.SPORT,    dt(2, 12),  dt(2, 13, 30)),
        make_block("PHYS301 Lab",        BlockType.LAB,      dt(2, 14),  dt(2, 17)),
        make_block("Chess Society",      BlockType.SOCIETY,  dt(2, 18),  dt(2, 19, 30)),
        make_block("Sleep",              BlockType.SLEEP,    dt(2, 23),  dt(3, 7, 30)),

        # --- Thursday ---
        make_block("MATH201 Lecture",    BlockType.LECTURE,  dt(3, 10),  dt(3, 11)),
        make_block("Lunch",              BlockType.PERSONAL, dt(3, 12),  dt(3, 13)),
        make_block("Part-time job",      BlockType.WORK,     dt(3, 15),  dt(3, 19)),
        make_block("Sleep",              BlockType.SLEEP,    dt(3, 23),  dt(4, 7, 30)),

        # --- Friday ---
        make_block("CS101 Seminar",      BlockType.SEMINAR,  dt(4, 9),   dt(4, 10)),
        make_block("PHYS301 Lecture",    BlockType.LECTURE,  dt(4, 11),  dt(4, 12)),
        make_block("Lunch / social",     BlockType.PERSONAL, dt(4, 12),  dt(4, 14)),
        make_block("Sleep",              BlockType.SLEEP,    dt(4, 23),  dt(5, 7, 30)),
    ]

    # ------------------------------------------------------------------
    # Assignments
    # ------------------------------------------------------------------
    assignments: list[Assignment] = [
        make_assignment(
            title="Binary Trees Essay",
            module_code="CS101",
            deadline=dt(3, 23, 59),          # Thursday night
            base_estimated_minutes=180,
            strength_score=0.75,             # fairly strong → ~225 min
            priority=Priority.HIGH,
            description="3000 word essay on binary tree applications.",
        ),
        make_assignment(
            title="Integration Problem Sheet",
            module_code="MATH201",
            deadline=dt(4, 17, 0),           # Friday 5pm
            base_estimated_minutes=120,
            strength_score=0.40,             # weak area → ~192 min
            priority=Priority.MEDIUM,
        ),
        make_assignment(
            title="Quantum Mechanics Report",
            module_code="PHYS301",
            deadline=dt(5, 23, 59),          # Saturday night
            base_estimated_minutes=240,
            strength_score=0.55,             # average → ~276 min
            priority=Priority.HIGH,
        ),
        make_assignment(
            title="Programming Assignment",
            module_code="CS101",
            deadline=dt(6, 17, 0),           # Sunday 5pm
            base_estimated_minutes=300,
            strength_score=0.90,             # strong → ~330 min
            priority=Priority.CRITICAL,
        ),
    ]

    # ------------------------------------------------------------------
    # Run scheduler
    # ------------------------------------------------------------------
    config = SchedulerConfig(
        slot_minutes=30,
        min_gap_minutes=10,
        max_daily_study_hours=5.0,
        preferred_study_start=time(8, 0),
        preferred_study_end=time(22, 30),
        days_before_deadline_buffer=0,
        strategy="spread",
    )

    scheduler = Scheduler(config)
    result = scheduler.build(
        timetable_blocks=timetable,
        assignments=assignments,
        scheduling_start=dt(0, 7, 30),    # schedule from Monday 07:30
    )

    print(result.summary())

    # Show JSON-serialisable dict (trimmed for readability)
    import json
    data = result.to_dict()
    print("\n  JSON preview (first assignment sessions):")
    first = data["assignments"][0]
    print(json.dumps(
        {k: first[k] for k in ("title", "module_code", "estimated_minutes",
                                "scheduled_minutes", "is_fully_scheduled", "sessions")},
        indent=4,
    ))


if __name__ == "__main__":
    _demo()