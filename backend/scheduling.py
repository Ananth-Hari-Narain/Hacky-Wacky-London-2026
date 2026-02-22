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


class SchedulingStrategy(Enum):
    ASAP = "asap"
    SPREAD_OUT = "spread_out"


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
    max_study_hours_by_week: list[float] = field(default_factory=lambda: [5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0])   # max study hours per day of week
    preferred_study_start: time = time(9, 0)
    preferred_study_end: time = time(22, 0)
    days_before_deadline_buffer: int = 1    # stop scheduling within N days of deadline
    max_consecutive_slots: int = 4          # max number of back-to-back study slots for the same assignment (so 2 hours by default)
    strategy: SchedulingStrategy = SchedulingStrategy.ASAP

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
            if self.config.strategy == SchedulingStrategy.SPREAD_OUT:
                free_slots = self._spread_slots(free_slots, now, latest_dt)

            slots_placed = 0
            consecutive_slots = 0
            prev_slot_end: Optional[datetime] = None
            for slot_start, slot_end in free_slots:
                if assignment.remaining_slots == 0:
                    break

                if prev_slot_end == slot_start:
                    consecutive_slots += 1
                else:
                    consecutive_slots = 1
                prev_slot_end = slot_end

                ## If been going for more than max_consecutive_slots, take a break
                if consecutive_slots > self.config.max_consecutive_slots:
                    consecutive_slots = 0
                    prev_slot_end = None
                    continue

                # Check daily cap
                day = slot_start.date()
                day_minutes = self._scheduled_minutes_on_day(
                    new_study_blocks, day
                )
                max_day_minutes = int(self.config.max_study_hours_by_week[day.weekday()] * 60)
                if day_minutes >= max_day_minutes:
                    consecutive_slots = 0
                    prev_slot_end = None
                    continue

                # Clamp slot to daily cap
                available = min(
                    self.config.slot_minutes,
                    max_day_minutes - day_minutes,
                )
                if available < self.config.slot_minutes:
                    consecutive_slots = 0
                    prev_slot_end = None
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
        Uses day-by-day round-robin in small contiguous chunks to avoid
        over-fragmenting sessions.
        """
        if not slots:
            return slots

        from collections import defaultdict
        day_buckets: dict[date, list[tuple[datetime, datetime]]] = defaultdict(list)
        for s, e in slots:
            day_buckets[s.date()].append((s, e))

        # Interleave day by day with small per-day batches to keep flow.
        sorted_days = sorted(day_buckets.keys())
        chunk_size = max(1, min(2, self.config.max_consecutive_slots))
        day_offsets: dict[date, int] = {d: 0 for d in sorted_days}
        result: list[tuple[datetime, datetime]] = []
        while True:
            added_any = False
            for d in sorted_days:
                bucket = day_buckets[d]
                offset = day_offsets[d]
                if offset >= len(bucket):
                    continue
                end_offset = min(offset + chunk_size, len(bucket))
                result.extend(bucket[offset:end_offset])
                day_offsets[d] = end_offset
                added_any = True
            if not added_any:
                break
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
    
    def display_timetable(
    self,
    timetable_blocks: list[TimeBlock],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    ) -> str:
        """
        Display the student's full timetable (existing blocks + newly scheduled
        study sessions) in a readable day-by-day format.

        Parameters
        ----------
        timetable_blocks:
            The original fixed blocks passed into the scheduler.
        start_date:
            First day to display. Defaults to the earliest block found.
        end_date:
            Last day to display. Defaults to the latest block found.
        """
        all_blocks = timetable_blocks + self.new_study_blocks

        if not all_blocks:
            return "No blocks to display."

        # --- determine date range ---
        if start_date is None:
            start_date = min(b.start.date() for b in all_blocks)
        if end_date is None:
            end_date = max(b.end.date() for b in all_blocks)

        # --- colour / icon map per block type ---
        ICONS = {
            BlockType.LECTURE:       "📚",
            BlockType.SEMINAR:       "🗣 ",
            BlockType.LAB:           "🔬",
            BlockType.SOCIETY:       "🎭",
            BlockType.SLEEP:         "😴",
            BlockType.SPORT:         "🏃",
            BlockType.WORK:          "💼",
            BlockType.PERSONAL:      "🧍",
            BlockType.STUDY_SESSION: "✏️ ",
            BlockType.OTHER:         "📌",
        }

        BLOCK_WIDTH = 60   # total width of the timetable display

        def separator(char: str = "─") -> str:
            return char * BLOCK_WIDTH

        def centre(text: str) -> str:
            return text.center(BLOCK_WIDTH)

        def format_block(block: TimeBlock) -> str:
            icon = ICONS.get(block.block_type, "📌")
            time_str = (
                f"{block.start.strftime('%H:%M')} – {block.end.strftime('%H:%M')}"
                f"  ({block.duration_minutes}min)"
            )
            title_line = f"  {icon} {block.title}"
            time_line  = f"     {time_str}"

            lines = [title_line, time_line]

            if block.attended is not None:
                attended_str = "Attended: ✓" if block.attended else "Attended: ✗"
                lines.append(f"     {attended_str}")

            if block.notes and block.block_type != BlockType.STUDY_SESSION:
                # Wrap notes at (BLOCK_WIDTH - 6) chars
                words = block.notes.split()
                note_lines: list[str] = []
                current = "     📝 "
                for word in words:
                    if len(current) + len(word) + 1 <= BLOCK_WIDTH - 1:
                        current += word + " "
                    else:
                        note_lines.append(current.rstrip())
                        current = "        " + word + " "
                note_lines.append(current.rstrip())
                lines.extend(note_lines)

            return "\n".join(lines)

        # --- build output ---
        lines: list[str] = []
        lines.append(separator("═"))
        lines.append(centre("  STUDENT TIMETABLE"))
        lines.append(separator("═"))

        current_date = start_date
        while current_date <= end_date:
            # All blocks that START on this day, sorted by start time
            day_blocks = sorted(
                [b for b in all_blocks if b.start.date() == current_date],
                key=lambda b: b.start,
            )

            # Day header
            day_str = current_date.strftime("%A  %d %B %Y")
            lines.append("")
            lines.append(centre(f"┌── {day_str} ──┐"))
            lines.append(separator())

            if not day_blocks:
                lines.append(centre("(no blocks)"))
            else:
                # Detect gaps between blocks so free time is visible
                prev_end: Optional[datetime] = None
                for block in day_blocks:
                    if prev_end is not None and block.start > prev_end:
                        gap_minutes = int((block.start - prev_end).total_seconds() / 60)
                        if gap_minutes >= 30:   # only show gaps worth mentioning
                            gap_str = (
                                f"  · · · free  "
                                f"{prev_end.strftime('%H:%M')} – "
                                f"{block.start.strftime('%H:%M')} "
                                f"({gap_minutes}min)"
                            )
                            lines.append(gap_str)

                    lines.append(format_block(block))
                    lines.append(separator("·"))
                    prev_end = block.end

            # Daily study session summary
            study_blocks = [
                b for b in day_blocks if b.block_type == BlockType.STUDY_SESSION
            ]
            if study_blocks:
                total_study = sum(b.duration_minutes for b in study_blocks)
                lines.append(
                    f"  📊 Study sessions today: {len(study_blocks)} "
                    f"× {self.new_study_blocks[0].duration_minutes}min "
                    f"= {total_study}min total"
                )
                lines.append(separator())

            current_date += timedelta(days=1)

        # --- legend ---
        lines.append("")
        lines.append(separator("═"))
        lines.append(centre("LEGEND"))
        lines.append(separator("─"))
        for block_type, icon in ICONS.items():
            lines.append(f"  {icon}  {block_type.value.replace('_', ' ').title()}")
        lines.append(separator("═"))

        return "\n".join(lines)


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


def _populate_assignments(
    user_id: str,
    assignments: list[dict],
) -> int:
    """
    Persist a batch of assignments to the database for a given user.

    Each dict in *assignments* must have the following keys:
        - ``name``              (str)      – the assignment name
        - ``topic``             (str)      – the topic / module the assignment belongs to
        - ``due_date``          (datetime) – when the assignment is due
        - ``date_released``     (datetime) – when the assignment was released

    Optional keys:
        - ``strength_score``    (float)    – 0.0–1.0, defaults to 0.5
        - ``duration_minutes``  (int)      – estimated duration, defaults to 240 (4 hours)

    An ``assignment_id`` is generated automatically (UUID4) for every entry
    that does not already carry one.

    Returns
    -------
    int
        Number of documents upserted / modified in the database.
    """
    import uuid as _uuid
    from database.timetable_db import add_assignments

    enriched: list[dict] = []
    for a in assignments:
        enriched.append({
            "assignment_id": a.get("assignment_id", str(_uuid.uuid4())),
            "name": a["name"],
            "topic": a["topic"],
            "due_date": a["due_date"],
            "date_released": a["date_released"],
            "strength_score": a.get("strength_score", 0.5),
            "duration_minutes": a.get("duration_minutes", 240),
        })

    return add_assignments(user_id, enriched)


# ---------------------------------------------------------------------------
# Demo / smoke test
# ---------------------------------------------------------------------------

def _display_blocks(blocks: list[TimeBlock], start_date: Optional[date] = None, end_date: Optional[date] = None) -> str:
    """
    Standalone timetable printer that works on any list of TimeBlocks
    (without needing a ScheduleResult).
    """
    if not blocks:
        return "No blocks to display."

    if start_date is None:
        start_date = min(b.start.date() for b in blocks)
    if end_date is None:
        end_date = max(b.end.date() for b in blocks)

    ICONS = {
        BlockType.LECTURE:       "📚",
        BlockType.SEMINAR:       "🗣 ",
        BlockType.LAB:           "🔬",
        BlockType.SOCIETY:       "🎭",
        BlockType.SLEEP:         "😴",
        BlockType.SPORT:         "🏃",
        BlockType.WORK:          "💼",
        BlockType.PERSONAL:      "🧍",
        BlockType.STUDY_SESSION: "✏️ ",
        BlockType.OTHER:         "📌",
    }
    W = 60

    def sep(c: str = "─") -> str:
        return c * W

    def centre(t: str) -> str:
        return t.center(W)

    lines: list[str] = [sep("═"), centre("  STUDENT TIMETABLE"), sep("═")]
    cur = start_date
    while cur <= end_date:
        day_blocks = sorted(
            [b for b in blocks if b.start.date() == cur],
            key=lambda b: b.start,
        )
        lines.append("")
        lines.append(centre(f"┌── {cur.strftime('%A  %d %B %Y')} ──┐"))
        lines.append(sep())
        if not day_blocks:
            lines.append(centre("(no blocks)"))
        else:
            prev_end: Optional[datetime] = None
            for block in day_blocks:
                if prev_end and block.start > prev_end:
                    gap = int((block.start - prev_end).total_seconds() / 60)
                    if gap >= 30:
                        lines.append(
                            f"  · · · free  {prev_end.strftime('%H:%M')} – "
                            f"{block.start.strftime('%H:%M')} ({gap}min)"
                        )
                icon = ICONS.get(block.block_type, "📌")
                lines.append(f"  {icon} {block.title}")
                lines.append(
                    f"     {block.start.strftime('%H:%M')} – "
                    f"{block.end.strftime('%H:%M')}  ({block.duration_minutes}min)"
                )
                lines.append(sep("·"))
                prev_end = block.end
        cur += timedelta(days=1)

    lines += ["", sep("═")]
    return "\n".join(lines)


def _demo() -> None:
    """
    End-to-end demonstration with MongoDB persistence.

    1. Load sample timetable into DB as 'fixed' blocks (clearing old data).
    2. Run the scheduler and store generated blocks.
    3. Re-read everything from DB and print.
    4. User adds a new fixed break → regenerate → store → re-read → print.
    """
    from database.timetable_db import (
        add_blocks as db_add_blocks,
        clear_generated_blocks,
        get_user_schedule,
        ensure_indexes,
        _get_db,
    )

    # ------------------------------------------------------------------
    # Setup: fixed demo user
    # ------------------------------------------------------------------
    DEMO_USER_ID = "demo-student-001"
    ensure_indexes()

    # Wipe this user's previous data entirely
    db = _get_db()
    db["timetable_blocks"].delete_many({"user_id": DEMO_USER_ID})
    db["users"].update_one(
        {"_id": DEMO_USER_ID},
        {"$set": {"name": "Demo Student", "email": "demo@example.com"}},
        upsert=True,
    )
    print("✓ Cleared previous demo data from MongoDB\n")

    # ------------------------------------------------------------------
    # Sample timetable (Mon 2025-06-09 → Fri 2025-06-13)
    # ------------------------------------------------------------------
    monday = datetime(2025, 6, 9)

    def dt(day_offset: int, hour: int, minute: int = 0) -> datetime:
        return monday + timedelta(days=day_offset, hours=hour, minutes=minute)

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
            deadline=dt(3, 23, 59),
            base_estimated_minutes=180,
            strength_score=0.75,
            priority=Priority.HIGH,
            description="3000 word essay on binary tree applications.",
        ),
        make_assignment(
            title="Integration Problem Sheet",
            module_code="MATH201",
            deadline=dt(4, 17, 0),
            base_estimated_minutes=120,
            strength_score=0.40,
            priority=Priority.MEDIUM,
        ),
        make_assignment(
            title="Quantum Mechanics Report",
            module_code="PHYS301",
            deadline=dt(5, 23, 59),
            base_estimated_minutes=240,
            strength_score=0.55,
            priority=Priority.HIGH,
        ),
        make_assignment(
            title="Programming Assignment",
            module_code="CS101",
            deadline=dt(6, 17, 0),
            base_estimated_minutes=300,
            strength_score=0.90,
            priority=Priority.CRITICAL,
        ),
    ]

    config = SchedulerConfig(
        slot_minutes=30,
        min_gap_minutes=10,
        max_study_hours_by_week=[5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0],
        preferred_study_start=time(8, 0),
        preferred_study_end=time(22, 30),
        days_before_deadline_buffer=0,
        strategy=SchedulingStrategy.SPREAD_OUT,
    )
    scheduler = Scheduler(config)

    # ==================================================================
    # STEP 1: Store fixed blocks into MongoDB
    # ==================================================================
    print("=" * 60)
    print("  STEP 1 — Load sample fixed blocks into MongoDB")
    print("=" * 60)
    n = db_add_blocks(DEMO_USER_ID, timetable, source="fixed")
    print(f"  Wrote {n} fixed blocks to DB\n")

    # ==================================================================
    # STEP 2: Run scheduler, store generated blocks
    # ==================================================================
    print("=" * 60)
    print("  STEP 2 — Run scheduler & store generated study sessions")
    print("=" * 60)
    result = scheduler.build(
        timetable_blocks=timetable,
        assignments=assignments,
        scheduling_start=dt(0, 7, 30),
    )
    print(result.summary())

    n = db_add_blocks(DEMO_USER_ID, result.new_study_blocks, source="generated")
    print(f"  Wrote {n} generated blocks to DB\n")

    # ==================================================================
    # STEP 3: Re-read everything from DB and print
    # ==================================================================
    print("=" * 60)
    print("  STEP 3 — Re-read full schedule from MongoDB")
    print("=" * 60)
    all_blocks = get_user_schedule(DEMO_USER_ID)
    print(f"  Total blocks in DB: {len(all_blocks)}")
    fixed_count = len(get_user_schedule(DEMO_USER_ID, source="fixed"))
    gen_count = len(get_user_schedule(DEMO_USER_ID, source="generated"))
    print(f"    Fixed: {fixed_count}  |  Generated: {gen_count}\n")
    print(_display_blocks(all_blocks))

    # ==================================================================
    # STEP 4: User adds a new fixed break → regenerate
    # ==================================================================
    print("\n" + "=" * 60)
    print("  STEP 4 — User adds a fixed break on Wednesday afternoon")
    print("=" * 60)
    new_break = make_block(
        "Doctor's Appointment",
        BlockType.PERSONAL,
        dt(2, 10, 30),
        dt(2, 12, 0),
        notes="GP check-up, cannot reschedule.",
    )
    db_add_blocks(DEMO_USER_ID, [new_break], source="fixed")
    print("  ✓ Added 'Doctor's Appointment' (Wed 10:30–12:00) as fixed\n")

    # Clear old generated blocks
    deleted = clear_generated_blocks(DEMO_USER_ID)
    print(f"  ✓ Cleared {deleted} old generated blocks\n")

    # Re-read fixed blocks from DB (this is now the scheduler's input)
    fixed_blocks = get_user_schedule(DEMO_USER_ID, source="fixed")
    print(f"  Fixed blocks now in DB: {len(fixed_blocks)}")

    # Regenerate
    print("\n  Re-running scheduler with updated fixed blocks …\n")
    result2 = scheduler.build(
        timetable_blocks=fixed_blocks,
        assignments=assignments,
        scheduling_start=dt(0, 7, 30),
    )
    print(result2.summary())

    # Store new generated blocks
    n = db_add_blocks(DEMO_USER_ID, result2.new_study_blocks, source="generated")
    print(f"  Wrote {n} new generated blocks to DB\n")

    # ==================================================================
    # STEP 5: Final re-read from DB and print
    # ==================================================================
    print("=" * 60)
    print("  STEP 5 — Final schedule read from MongoDB")
    print("=" * 60)
    final_blocks = get_user_schedule(DEMO_USER_ID)
    final_fixed = len(get_user_schedule(DEMO_USER_ID, source="fixed"))
    final_gen = len(get_user_schedule(DEMO_USER_ID, source="generated"))
    print(f"  Total blocks in DB: {len(final_blocks)}")
    print(f"    Fixed: {final_fixed}  |  Generated: {final_gen}\n")
    print(_display_blocks(final_blocks))

    print("\n✓ Demo complete — all data persisted in MongoDB.")


def _demo2() -> None:
    """
    Populate the database with a realistic, busy two-week timetable
    (Sun 22 Feb 2026 – Sun 8 Mar 2026) for the demo user.

    Covers: lectures, seminars, labs, societies, sport, part-time work,
    sleep, meals, and a set of assignments.  Also runs the scheduler
    to fill in study sessions.

    Usage:
        python -m backend.scheduling demo2
    """
    from database.timetable_db import (
        add_blocks as db_add_blocks,
        clear_generated_blocks,
        get_user_schedule,
        ensure_indexes,
        _get_db,
    )

    DEMO_USER_ID = "demo-student-001"
    ensure_indexes()

    # Wipe previous data
    db = _get_db()
    db["timetable_blocks"].delete_many({"user_id": DEMO_USER_ID})
    db["users"].update_one(
        {"_id": DEMO_USER_ID},
        {"$set": {"name": "Demo Student", "email": "demo@example.com"}},
        upsert=True,
    )
    print("✓ Cleared previous demo data\n")

    # ------------------------------------------------------------------
    # Base date: Sunday 22 Feb 2026
    # ------------------------------------------------------------------
    sun22 = datetime(2026, 2, 22)

    def dt(day_offset: int, hour: int, minute: int = 0) -> datetime:
        return sun22 + timedelta(days=day_offset, hours=hour, minutes=minute)

    # ------------------------------------------------------------------
    # Helper: repeat a weekly pattern across both weeks
    # ------------------------------------------------------------------
    def weekly(day_of_week: int, h_start: int, m_start: int,
               h_end: int, m_end: int, title: str,
               bt: BlockType, notes: str | None = None,
               attended: bool | None = None) -> list[TimeBlock]:
        """day_of_week: 0=Sun(22 Feb), 1=Mon(23 Feb), … 6=Sat(28 Feb)"""
        blocks: list[TimeBlock] = []
        for week_offset in (0, 7):
            d = day_of_week + week_offset
            blocks.append(make_block(
                title, bt,
                dt(d, h_start, m_start), dt(d, h_end, m_end),
                notes=notes, attended=attended,
            ))
        return blocks

    timetable: list[TimeBlock] = []

    # === SLEEP — every night 23:00 → 07:30 ============================
    for d in range(15):  # day 0 (Sun 22) through day 14 (Sun 8 Mar)
        timetable.append(make_block(
            "Sleep", BlockType.SLEEP,
            dt(d, 23, 0), dt(d + 1, 7, 30),
        ))

    # === MONDAY (days 1, 8) ============================================
    timetable += weekly(1, 9, 0, 10, 0, "CS201 – Algorithms Lecture",
                        BlockType.LECTURE, "Divide and conquer", True)
    timetable += weekly(1, 10, 15, 11, 15, "MATH202 – Linear Algebra Lecture",
                        BlockType.LECTURE, "Eigenvalues this week", True)
    timetable += weekly(1, 11, 30, 12, 30, "CS201 – Algorithms Seminar",
                        BlockType.SEMINAR)
    timetable += weekly(1, 12, 30, 13, 15, "Lunch",
                        BlockType.PERSONAL)
    timetable += weekly(1, 14, 0, 16, 0, "CS201 – Programming Lab",
                        BlockType.LAB)
    timetable += weekly(1, 18, 0, 19, 30, "Football (5-a-side)",
                        BlockType.SPORT)
    timetable += weekly(1, 20, 0, 21, 0, "Dinner",
                        BlockType.PERSONAL)

    # === TUESDAY (days 2, 9) ===========================================
    timetable += weekly(2, 9, 0, 10, 0, "PHYS301 – Quantum Mechanics Lecture",
                        BlockType.LECTURE, "Schrödinger equation", True)
    timetable += weekly(2, 10, 15, 11, 15, "ENG101 – Technical Writing Lecture",
                        BlockType.LECTURE)
    timetable += weekly(2, 11, 30, 12, 30, "ENG101 – Workshop",
                        BlockType.SEMINAR)
    timetable += weekly(2, 12, 30, 13, 15, "Lunch",
                        BlockType.PERSONAL)
    timetable += weekly(2, 13, 30, 14, 30, "MATH202 – Seminar",
                        BlockType.SEMINAR)
    timetable += weekly(2, 16, 0, 20, 0, "Part-time job (café)",
                        BlockType.WORK)

    # === WEDNESDAY (days 3, 10) ========================================
    timetable += weekly(3, 9, 0, 10, 0, "CS201 – Algorithms Lecture",
                        BlockType.LECTURE, "Dynamic programming")
    timetable += weekly(3, 10, 15, 11, 15, "PHYS301 – Problem Class",
                        BlockType.SEMINAR)
    timetable += weekly(3, 12, 0, 13, 30, "Gym session",
                        BlockType.SPORT)
    timetable += weekly(3, 14, 0, 17, 0, "PHYS301 – Lab",
                        BlockType.LAB, "Optics experiment")
    timetable += weekly(3, 18, 0, 19, 30, "Chess Club",
                        BlockType.SOCIETY)
    timetable += weekly(3, 19, 30, 20, 15, "Dinner",
                        BlockType.PERSONAL)

    # === THURSDAY (days 4, 11) =========================================
    timetable += weekly(4, 10, 0, 11, 0, "MATH202 – Lecture",
                        BlockType.LECTURE, "Orthogonal matrices")
    timetable += weekly(4, 11, 15, 12, 15, "CS201 – Guest Lecture",
                        BlockType.LECTURE)
    timetable += weekly(4, 12, 15, 13, 0, "Lunch",
                        BlockType.PERSONAL)
    timetable += weekly(4, 15, 0, 19, 0, "Part-time job (café)",
                        BlockType.WORK)
    timetable += weekly(4, 20, 0, 21, 30, "Debate Society",
                        BlockType.SOCIETY)

    # === FRIDAY (days 5, 12) ===========================================
    timetable += weekly(5, 9, 0, 10, 0, "PHYS301 – Lecture",
                        BlockType.LECTURE, "Angular momentum")
    timetable += weekly(5, 10, 15, 11, 15, "CS201 – Seminar",
                        BlockType.SEMINAR)
    timetable += weekly(5, 11, 30, 12, 30, "ENG101 – Peer Review",
                        BlockType.SEMINAR)
    timetable += weekly(5, 12, 30, 14, 0, "Lunch with friends",
                        BlockType.PERSONAL)
    timetable += weekly(5, 14, 0, 15, 0, "MATH202 – Drop-in",
                        BlockType.SEMINAR)

    # === SATURDAY (days 6, 13) =========================================
    timetable += weekly(6, 10, 0, 11, 30, "Gym / Parkrun",
                        BlockType.SPORT)
    timetable += weekly(6, 14, 0, 18, 0, "Part-time job (café)",
                        BlockType.WORK)

    # === SUNDAY (days 0, 7, 14) ========================================
    # Sundays are mostly free (lie-in + errands)
    for d in (0, 7, 14):
        timetable.append(make_block("Lie-in", BlockType.PERSONAL,
                                    dt(d, 8, 0), dt(d, 10, 0)))
        timetable.append(make_block("Grocery shopping", BlockType.PERSONAL,
                                    dt(d, 11, 0), dt(d, 12, 0)))

    # A few one-off events
    timetable.append(make_block("Doctor's appointment", BlockType.PERSONAL,
                                dt(3, 8, 0), dt(3, 9, 0),
                                notes="GP check-up"))
    timetable.append(make_block("Career Fair", BlockType.OTHER,
                                dt(11, 13, 0), dt(11, 16, 0),
                                notes="Imperial spring careers fair"))
    timetable.append(make_block("Birthday dinner", BlockType.PERSONAL,
                                dt(12, 19, 0), dt(12, 22, 0)))

    print(f"Total fixed blocks: {len(timetable)}")

    # ------------------------------------------------------------------
    # Assignments
    # ------------------------------------------------------------------
    assignments: list[Assignment] = [
        make_assignment(
            title="Algorithms Coursework 2",
            module_code="CS201",
            deadline=dt(10, 23, 59),        # Wed 4 Mar
            base_estimated_minutes=300,
            strength_score=0.70,
            priority=Priority.HIGH,
            description="Implement and benchmark a graph algorithm.",
        ),
        make_assignment(
            title="Linear Algebra Problem Sheet 5",
            module_code="MATH202",
            deadline=dt(8, 17, 0),           # Mon 2 Mar
            base_estimated_minutes=120,
            strength_score=0.45,
            priority=Priority.MEDIUM,
        ),
        make_assignment(
            title="Quantum Mechanics Lab Report",
            module_code="PHYS301",
            deadline=dt(12, 23, 59),         # Fri 6 Mar
            base_estimated_minutes=240,
            strength_score=0.55,
            priority=Priority.HIGH,
        ),
        make_assignment(
            title="Technical Report Draft",
            module_code="ENG101",
            deadline=dt(14, 17, 0),          # Sun 8 Mar
            base_estimated_minutes=180,
            strength_score=0.65,
            priority=Priority.MEDIUM,
            description="2000-word draft on engineering ethics.",
        ),
        make_assignment(
            title="CS201 Programming Challenge",
            module_code="CS201",
            deadline=dt(7, 23, 59),          # Sun 1 Mar
            base_estimated_minutes=150,
            strength_score=0.85,
            priority=Priority.CRITICAL,
        ),
    ]

    # ------------------------------------------------------------------
    # Persist fixed blocks
    # ------------------------------------------------------------------
    n = db_add_blocks(DEMO_USER_ID, timetable, source="fixed")
    print(f"✓ Wrote {n} fixed blocks to MongoDB\n")

    # ------------------------------------------------------------------
    # Run scheduler
    # ------------------------------------------------------------------
    config = SchedulerConfig(
        slot_minutes=30,
        min_gap_minutes=10,
        max_study_hours_by_week=[6.0] * 7,
        preferred_study_start=time(8, 0),
        preferred_study_end=time(22, 30),
        days_before_deadline_buffer=0,
        strategy=SchedulingStrategy.SPREAD_OUT,
    )
    scheduler = Scheduler(config)

    result = scheduler.build(
        timetable_blocks=timetable,
        assignments=assignments,
        scheduling_start=dt(0, 7, 30),
    )
    print(result.summary())

    n = db_add_blocks(DEMO_USER_ID, result.new_study_blocks, source="generated")
    print(f"✓ Wrote {n} study sessions to MongoDB\n")

    # ------------------------------------------------------------------
    # Print final timetable
    # ------------------------------------------------------------------
    all_blocks = get_user_schedule(DEMO_USER_ID)
    fixed_count = len(get_user_schedule(DEMO_USER_ID, source="fixed"))
    gen_count = len(get_user_schedule(DEMO_USER_ID, source="generated"))
    print(f"Total blocks in DB: {len(all_blocks)}  (fixed: {fixed_count}, generated: {gen_count})\n")
    print(_display_blocks(all_blocks))
    print("\n✓ demo2 complete — busy two-week timetable persisted.")


if __name__ == "__main__":
    _demo2()
    # _populate_assignments(
    #     "demo-student-001",
    #     [
    #         {
    #             "name": "Algorithms Coursework 2",
    #             "topic": "CS201",
    #             "due_date": datetime(2026, 3, 4, 23, 59),
    #             "date_released": datetime(2026, 2, 22, 9, 0),
    #             "strength_score": 0.5,
    #             "duration_minutes": 240,
    #         },
    #         {
    #             "name": "Linear Algebra Problem Sheet 5",
    #             "topic": "MATH202",
    #             "due_date": datetime(2026, 3, 2, 17, 0),
    #             "date_released": datetime(2026, 2, 22, 9, 0),
    #             "strength_score": 0.5,
    #             "duration_minutes": 240,
    #         },
    #         {
    #             "name": "Quantum Mechanics Lab Report",
    #             "topic": "PHYS301",
    #             "due_date": datetime(2026, 3, 6, 23, 59),
    #             "date_released": datetime(2026, 2, 23, 10, 0),
    #             "strength_score": 0.5,
    #             "duration_minutes": 300,
    #         },
    #         {
    #             "name": "Technical Report Draft",
    #             "topic": "ENG101",
    #             "due_date": datetime(2026, 3, 8, 17, 0),
    #             "date_released": datetime(2026, 2, 24, 11, 0),
    #             "strength_score": 0.5,
    #             "duration_minutes": 300,
    #         },
    #         {
    #             "name": "CS201 Programming Challenge",
    #             "topic": "CS201",
    #             "due_date": datetime(2026, 3, 1, 23, 59),
    #             "date_released": datetime(2026, 2, 22, 9, 0),
    #             "strength_score": 0.5,
    #             "duration_minutes": 240,
    #         },
    #     ]
    # )