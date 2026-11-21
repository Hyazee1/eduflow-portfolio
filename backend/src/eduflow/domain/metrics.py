from collections import Counter
from collections.abc import Sequence
from typing import Any

from eduflow.domain.entities import Lesson, Snapshot
from eduflow.domain.time_rules import slot_key, week_days


def schedule_metrics(snapshot: Snapshot, lessons: Sequence[Lesson]) -> dict[str, Any]:
    valid = [s for s in lessons if s.status not in {"CANCELLED", "SUSPENDED"}]
    fulfilled = {r for s in valid for r in s.request_ids}
    teacher_load = Counter(s.teacher_id for s in valid)
    teacher_minutes: Counter[str] = Counter()
    room_load = Counter(s.room_id for s in valid)
    day_main: Counter[str] = Counter()
    courses = {c.id: c for c in snapshot.courses}
    slot_minutes = {s.code: s.minutes for s in snapshot.slots}
    room_capacity = {r.id: r.capacity for r in snapshot.rooms}
    preferences = {d.id: d.preferred for d in snapshot.demands}
    preferred_met = 0
    first_preferred_met = 0
    walking = 0
    for lesson in valid:
        teacher_minutes[lesson.teacher_id] += slot_minutes[lesson.slot_code]
        if courses[lesson.course_id].group == "MAIN":
            day_main[str(lesson.day)] += 1
        preferred_met += sum(
            slot_key(lesson.day, lesson.slot_code) in preferences.get(r, ()) for r in lesson.request_ids
        )
        first_preferred_met += sum(
            bool(preferences.get(r)) and slot_key(lesson.day, lesson.slot_code) == preferences[r][0]
            for r in lesson.request_ids
        )
    for student in snapshot.students:
        for day in {s.day for s in valid}:
            sessions = sorted(
                (s for s in valid if s.day == day and student.id in s.students), key=lambda s: s.slot_code
            )
            walking += sum(a.room_id != b.room_id for a, b in zip(sessions, sessions[1:]))
    total_capacity = sum(room_capacity.get(s.room_id, 0) for s in valid)
    days = week_days(snapshot.week)
    covered = {slot_key(s.day, s.slot_code) for s in valid if s.day in days}
    daily_load = Counter((s.teacher_id, s.day) for s in valid)
    rest = [
        {
            "teacher_id": teacher.id,
            "day": str(day),
            "lessons": daily_load[teacher.id, day],
            "rest_slots": len(snapshot.slots) - daily_load[teacher.id, day],
        }
        for teacher in snapshot.teachers
        for day in days
    ]
    return {
        "session_count": len(valid),
        "student_count": len({u for s in valid for u in s.students}),
        "request_count": len(snapshot.demands),
        "fulfilled_count": len(fulfilled),
        "satisfaction_rate": round(len(fulfilled) / max(1, len(snapshot.demands)), 4),
        "preferred_count": preferred_met,
        "first_preferred_count": first_preferred_met,
        "teacher_load": dict(teacher_load),
        "teacher_minutes": dict(teacher_minutes),
        "room_load": dict(room_load),
        "main_by_day": dict(day_main),
        "seat_utilization": round(sum(len(s.students) for s in valid) / max(1, total_capacity), 4),
        "student_room_changes": walking,
        "afternoon_regular_sessions": sum(s.slot_code in {"S5", "S6", "S7"} for s in valid),
        "covered_slots": len(covered),
        "required_slots": len(days) * len(snapshot.slots),
        "uncovered_slots": [
            f"{d}:{s.code}" for d in range(5) for s in snapshot.slots if f"{d}:{s.code}" not in covered
        ],
        "full_day_coverage_required": snapshot.rules.require_full_day_coverage,
        "teacher_daily_rest": rest,
        "active_teacher_count": len(teacher_load),
        "teacher_count": len(snapshot.teachers),
        "minimum_adjacent_day_rest": min(
            (
                16 - daily_load[t.id, days[d]] - daily_load[t.id, days[d + 1]]
                for t in snapshot.teachers
                for d in range(4)
            ),
            default=16,
        ),
    }
