from datetime import date, timedelta

from eduflow.domain.entities import Course, Demand, Lesson, Room, Snapshot, Student, Teacher
from eduflow.domain.time_rules import DEFAULT_SLOTS, Rules, all_keys


def full_week():
    week = date(2026, 11, 2)
    courses = (
        Course("main", "主课", "MAIN", 6, 15, 1, 10, 3, 20, frozenset()),
        Course("qa", "答疑", "QA", 6, 15, 1, 10, 3, 20, frozenset(), duration=35),
    )
    teachers = tuple(Teacher(f"teacher-{i}", frozenset({"main", "qa"}), all_keys()) for i in range(2))
    students, demands, lessons = [], [], []
    for day in range(5):
        for index, slot in enumerate(DEFAULT_SLOTS):
            course = courses[int(slot.kind == "QA")]
            offering = f"offering-{day}-{index}"
            roster, requests = [], []
            for member in range(3):
                student = Student(f"student-{day}-{index}-{member}", 10, 5, all_keys())
                students.append(student)
                key = f"{day}:{slot.code}"
                demand = Demand(
                    f"request-{student.id}",
                    student.id,
                    course.id,
                    offering,
                    (key,),
                    frozenset(t.id for t in teachers),
                    frozenset({key}),
                )
                demands.append(demand)
                roster.append(student.id)
                requests.append(demand.id)
            lessons.append(
                Lesson(
                    f"lesson-{day}-{index}",
                    offering,
                    course.id,
                    teachers[index % 2].id,
                    "room",
                    week + timedelta(days=day),
                    slot.code,
                    slot.start,
                    slot.end,
                    tuple(roster),
                    tuple(requests),
                )
            )
    return Snapshot(
        "org",
        week,
        DEFAULT_SLOTS,
        tuple(students),
        teachers,
        (Room("room", 20, frozenset(), all_keys()),),
        courses,
        tuple(demands),
        Rules(solve_seconds=5),
    ), tuple(lessons)
