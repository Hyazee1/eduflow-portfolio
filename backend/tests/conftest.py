from datetime import date, timedelta

import pytest

from eduflow.domain.entities import Course, Demand, Lesson, Room, Snapshot, Student, Teacher
from eduflow.domain.time_rules import DEFAULT_SLOTS, Rules, all_keys, slot_key


@pytest.fixture
def legal_week() -> tuple[Snapshot, tuple[Lesson, ...]]:
    week = date(2026, 11, 2)
    available = all_keys()
    courses = (
        Course("main", "主课", "MAIN", 6, 15, 1, 10, 2, 20, frozenset()),
        Course("qa", "答疑", "QA", 6, 15, 1, 10, 1, 20, frozenset(), duration=35),
    )
    teachers = (
        Teacher("teacher-a", frozenset({"main", "qa"}), available),
        Teacher("teacher-b", frozenset({"main", "qa"}), available),
    )
    rooms = (Room("room-a", 20, frozenset(), available), Room("room-b", 20, frozenset(), available))
    students = tuple(Student(f"student-{i}", 10, 5, available) for i in range(21))
    demands = []
    lessons = []
    for day in range(5):
        for index, code in enumerate(("S1", "S3")):
            group = day * 2 + index
            student_ids = (f"student-{group * 2}", f"student-{group * 2 + 1}")
            request_ids = (f"request-{group * 2}", f"request-{group * 2 + 1}")
            offering = f"offering-{group}"
            slot = next(s for s in DEFAULT_SLOTS if s.code == code)
            for student_id, request_id in zip(student_ids, request_ids, strict=True):
                demands.append(
                    Demand(
                        request_id,
                        student_id,
                        "main",
                        offering,
                        (f"{day}:{code}",),
                        frozenset({"teacher-a", "teacher-b"}),
                        available,
                    )
                )
            lessons.append(
                Lesson(
                    f"lesson-{group}",
                    offering,
                    "main",
                    "teacher-a" if index == 0 else "teacher-b",
                    "room-a",
                    week + timedelta(days=day),
                    code,
                    slot.start,
                    slot.end,
                    student_ids,
                    request_ids,
                )
            )
    demands.append(
        Demand(
            "qa-request",
            "student-20",
            "qa",
            "qa-offering",
            ("0:S8",),
            frozenset({"teacher-a", "teacher-b"}),
            available,
        )
    )
    slot = DEFAULT_SLOTS[-1]
    lessons.append(
        Lesson(
            "qa-lesson",
            "qa-offering",
            "qa",
            "teacher-a",
            "room-a",
            week,
            slot.code,
            slot.start,
            slot.end,
            ("student-20",),
            ("qa-request",),
        )
    )
    snapshot = Snapshot(
        "org",
        week,
        DEFAULT_SLOTS,
        students,
        teachers,
        rooms,
        courses,
        tuple(demands),
        Rules(solve_seconds=3, require_full_day_coverage=False),
    )
    assert slot_key(lessons[0].day, lessons[0].slot_code) == "0:S1"
    return snapshot, tuple(lessons)
