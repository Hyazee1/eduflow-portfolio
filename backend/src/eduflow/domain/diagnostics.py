"""Explain input inconsistencies without dropping approved requests from the denominator."""

from collections import Counter

from eduflow.domain.entities import Snapshot
from eduflow.domain.time_rules import slot_key


def demand_diagnostics(snapshot: Snapshot) -> dict[str, str]:
    courses = {c.id: c for c in snapshot.courses}
    students = {s.id: s for s in snapshot.students}
    sizes = Counter(d.offering_id for d in snapshot.demands)
    result = {}
    for demand in snapshot.demands:
        course = courses.get(demand.course_id)
        student = students.get(demand.student_id)
        reason = ""
        if not course or course.status != "OPEN":
            reason = "课程不存在或已关闭"
        elif not student or not course.accepts(student):
            reason = "学生未启用或年龄年级不符合课程要求"
        else:
            legal = {
                f"{day}:{slot.code}"
                for day in range(5)
                for slot in snapshot.slots
                if (course.group == "QA") == (slot.kind == "QA") and course.duration == slot.minutes
            }
            teachers = [
                t
                for t in snapshot.teachers
                if t.id in demand.approved_teachers and course.id in t.qualifications
            ]
            if not legal & demand.allowed:
                reason = "开课时段与课程类型不一致：答疑仅限S8，常规课仅限S1—S7"
            elif not teachers:
                reason = "缺少具备资质且已同意开课的教师"
            elif not any(legal & demand.allowed & student.available & t.available for t in teachers):
                reason = "学生、已审核教师与开课计划没有共同可用时段"
            elif sizes[demand.offering_id] < course.min_size:
                reason = "已审核人数不足最低开班人数"
            elif not any(
                r.status == "OPEN"
                and r.capacity >= course.min_size
                and course.features <= r.features
                and (not demand.allowed_rooms or r.id in demand.allowed_rooms)
                and any(
                    legal & demand.allowed & student.available & t.available & r.available for t in teachers
                )
                for r in snapshot.rooms
            ):
                reason = "缺少共同可用、容量和安全设备合规的教室"
        if reason:
            result[demand.id] = reason
    return result


def uncovered_input_slots(snapshot: Snapshot) -> tuple[str, ...]:
    """Necessary supply check only; the solver still decides joint class membership."""
    available = {slot_key(s.day, s.slot_code) for s in snapshot.locked if s.status == "SCHEDULED"}
    courses = {c.id: c for c in snapshot.courses}
    students = {s.id: s for s in snapshot.students}
    for day in range(5):
        for slot in snapshot.slots:
            key = f"{day}:{slot.code}"
            if key in available:
                continue
            supply: Counter[tuple[str, str, str]] = Counter()
            for d in snapshot.demands:
                c, s = courses.get(d.course_id), students.get(d.student_id)
                if not c or not s or d.status != "APPROVED" or c.status != "OPEN" or not c.accepts(s):
                    continue
                if (
                    key not in d.allowed & s.available
                    or (c.group == "QA") != (slot.kind == "QA")
                    or c.duration != slot.minutes
                ):
                    continue
                for teacher in snapshot.teachers:
                    if (
                        key not in teacher.available
                        or teacher.id not in d.approved_teachers
                        or c.id not in teacher.qualifications
                    ):
                        continue
                    for room in snapshot.rooms:
                        if (
                            room.status == "OPEN"
                            and key in room.available
                            and c.features <= room.features
                            and room.capacity >= c.min_size
                            and (not d.allowed_rooms or room.id in d.allowed_rooms)
                        ):
                            supply[d.offering_id, teacher.id, room.id] += 1
                if any(
                    count >= c.min_size
                    for (offering, _, _), count in supply.items()
                    if offering == d.offering_id
                ):
                    available.add(key)
                    break
    return tuple(
        f"{d}:{s.code}" for d in range(5) for s in snapshot.slots if f"{d}:{s.code}" not in available
    )
