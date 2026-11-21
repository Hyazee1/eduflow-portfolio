"""Independent scan-based validator. No dependency on the optimization model."""

from collections import Counter, defaultdict
from collections.abc import Sequence

from eduflow.domain.entities import Lesson, Snapshot, Violation
from eduflow.domain.errors import BusinessError
from eduflow.domain.time_rules import slot_key, to_minutes, validate_templates, week_days


def validate_schedule(snapshot: Snapshot, lessons: Sequence[Lesson]) -> tuple[Violation, ...]:
    errors: list[Violation] = []
    slots = {s.code: s for s in snapshot.slots}
    teachers = {t.id: t for t in snapshot.teachers}
    students = {s.id: s for s in snapshot.students}
    rooms = {r.id: r for r in snapshot.rooms}
    courses = {c.id: c for c in snapshot.courses}
    demands = {d.id: d for d in snapshot.demands}
    days = week_days(snapshot.week)
    teacher_occupied: dict[tuple[str, object], list[Lesson]] = defaultdict(list)
    resource_occupied: dict[tuple[str, str, object], list[tuple[int, int, str]]] = defaultdict(list)
    main_counts: Counter[object] = Counter()
    covered: set[tuple[object, str]] = set()
    fulfilled: set[tuple[str, str]] = set()

    def fail(code: str, message: str, lesson: Lesson | None = None, resource: str = "") -> None:
        errors.append(Violation(code, message, (lesson.id,) if lesson else (), resource))

    try:
        validate_templates(snapshot.slots)
    except BusinessError as error:
        fail("H12", str(error))

    for lesson in lessons:
        if lesson.status in {"CANCELLED", "SUSPENDED"}:
            continue
        course = courses.get(lesson.course_id)
        teacher = teachers.get(lesson.teacher_id)
        room = rooms.get(lesson.room_id)
        slot = slots.get(lesson.slot_code)
        if not all((course, teacher, room, slot)):
            fail("H21", "课次引用不存在的课程、教师、教室或时段", lesson)
            continue
        assert course and teacher and room and slot
        covered.add((lesson.day, slot.code))
        if course.status != "OPEN":
            fail("H21", "课程已关闭，不允许新增有效安排", lesson)
        if lesson.day not in days:
            fail("H07", "课次必须位于本周工作日", lesson)
        if lesson.start != slot.start or lesson.end != slot.end:
            fail("H12", "课次时间与统一时段配置不一致", lesson)
        if (course.group == "QA") != (slot.kind == "QA"):
            fail("H10" if course.group == "QA" else "H11", "答疑与常规时段不匹配", lesson)
        if course.duration != slot.minutes:
            fail("H09", "课程时长与时段不匹配", lesson)
        key = slot_key(lesson.day, slot.code)
        if key not in teacher.available or key not in room.available or room.status != "OPEN":
            fail("H08", "教师请假、资源关闭或不在可用时段", lesson)
        if course.id not in teacher.qualifications:
            fail("H05", "教师缺少授课资质", lesson)
        if not course.features <= room.features:
            fail("H06", "教室设备不满足课程要求", lesson)
        if not course.min_size <= len(lesson.students) <= min(course.max_size, room.capacity):
            fail("H04", "班次人数超出课程或教室容量限制", lesson)
        if len(set(lesson.students)) != len(lesson.students):
            fail("H04", "班次学生重复", lesson)
        if len(lesson.students) != len(lesson.request_ids):
            fail("H21", "班次成员与选课需求不一致", lesson)
        teacher_occupied[(teacher.id, lesson.day)].append(lesson)
        intervals = [("teacher", teacher.id), ("room", room.id)]
        for student_id in lesson.students:
            student = students.get(student_id)
            if not student or not course.accepts(student):
                fail("H22", "学生年龄或年级不符合课程要求", lesson, student_id)
                continue
            if key not in student.available:
                fail("H07", "学生不在可用时段", lesson, student_id)
            matching = [
                demands[r]
                for r in lesson.request_ids
                if r in demands
                and demands[r].student_id == student_id
                and demands[r].course_id == course.id
                and demands[r].offering_id == lesson.offering_id
                and demands[r].status == "APPROVED"
            ]
            if len(matching) != 1:
                fail("H21", "缺少真实且已审核的选课需求", lesson, student_id)
            elif teacher.id not in matching[0].approved_teachers or key not in matching[0].allowed:
                fail("H07", "教师未同意开课或不在允许授课范围", lesson)
            if matching and matching[0].allowed_rooms and room.id not in matching[0].allowed_rooms:
                fail("H06", "教室不在本次调整的指定资源范围", lesson)
            request_key = (student_id, course.id)
            if request_key in fulfilled:
                fail("H21", "同一学生当周课程需求重复满足", lesson, student_id)
            fulfilled.add(request_key)
            intervals.append(("student", student_id))
        for kind, resource_id in intervals:
            start, end = to_minutes(lesson.start), to_minutes(lesson.end)
            previous = resource_occupied[(kind, resource_id, lesson.day)]
            for old_start, old_end, old_id in previous:
                if start < old_end and old_start < end:
                    errors.append(
                        Violation(
                            {"teacher": "H01", "room": "H02", "student": "H03"}[kind],
                            "资源发生时间冲突",
                            (old_id, lesson.id),
                            resource_id,
                        )
                    )
            previous.append((start, end, lesson.id))
        if course.group == "MAIN":
            main_counts[lesson.day] += 1

    weekly: Counter[str] = Counter()
    for (teacher_id, _day), assignments in teacher_occupied.items():
        weekly[teacher_id] += len(assignments)
        if len(assignments) > snapshot.rules.max_daily:
            fail("H14", "教师每日授课超过上限", resource=teacher_id)
        ordinals = {slots[s.slot_code].ordinal for s in assignments}
        limit = snapshot.rules.max_consecutive
        if any(all(i + j in ordinals for j in range(limit + 1)) for i in range(1, 9 - limit)):
            fail("H16", "教师连续授课超过上限", resource=teacher_id)
        for period in ("AM", "PM"):
            period_slots = {s.ordinal for s in snapshot.slots if s.period == period}
            if period_slots <= ordinals:
                fail("H17", "教师每半天必须保留一个未授课时段", resource=teacher_id)
    for teacher_id, count in weekly.items():
        if count > snapshot.rules.max_weekly:
            fail("H15", "教师每周授课超过上限", resource=teacher_id)
    for day in days:
        if main_counts[day] < snapshot.rules.min_main_daily:
            fail("H18", f"{day} 主课不足 {snapshot.rules.min_main_daily} 节")
        if snapshot.rules.require_full_day_coverage:
            for slot in snapshot.slots:
                if (day, slot.code) not in covered:
                    fail("H23", f"{day} {slot.code} 缺少课程，全天覆盖要求未满足")
    by_id = {lesson.id: lesson for lesson in lessons}
    for locked in snapshot.locked:
        if locked.id not in by_id or by_id[locked.id].fingerprint != locked.fingerprint:
            fail("H19", "无关生效课次被修改", locked)
    return tuple(errors)
