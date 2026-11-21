"""Deterministic, explicitly fictional demonstration records and solver fixtures."""

import random
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import date, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from faker import Faker
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from eduflow.adapters.models import ENTITY_MODELS, Organization
from eduflow.adapters.repository import SqlRecords
from eduflow.domain.entities import Course, Demand, Room, Snapshot, Student, Teacher
from eduflow.domain.time_rules import DEFAULT_SLOTS, Rules, all_keys

ORG_ID = str(uuid5(NAMESPACE_URL, "eduflow/demo/organization"))


def demo_id(kind: str, value: object) -> str:
    return str(uuid5(NAMESPACE_URL, f"eduflow/demo/{kind}/{value}"))


CATALOG = (
    ("艺术", "INTEREST", "ART", ("art",)),
    ("绘画", "INTEREST", "ART", ("art",)),
    ("书法", "INTEREST", "CALLIGRAPHY", ("calligraphy",)),
    ("无人机", "INTEREST", "DRONE", ("drone", "safety")),
    ("编程与实验", "MAIN", "COMPUTER", ("computer",)),
    ("逻辑思维", "MAIN", "GENERAL", ()),
    ("科学知识科普", "MAIN", "GENERAL", ()),
    ("机械原理", "MAIN", "MULTIPURPOSE", ("mechanics",)),
    ("科学小实验", "EXTENSION", "LAB", ("lab", "safety")),
    ("综合课程答疑", "QA", "GENERAL", ()),
    ("编程答疑", "QA", "GENERAL", ()),
    ("科学实验答疑", "QA", "GENERAL", ()),
    ("逻辑思维答疑", "QA", "GENERAL", ()),
)
ROOMS = (
    ("计算机教室 A", "COMPUTER", 24, ("computer",)),
    ("计算机教室 B", "COMPUTER", 24, ("computer",)),
    ("科学实验室 A", "LAB", 20, ("lab", "safety", "mechanics")),
    ("科学实验室 B", "LAB", 20, ("lab", "safety", "mechanics")),
    ("无人机与机器人实验室", "DRONE", 20, ("drone", "safety", "mechanics")),
    ("艺术绘画教室", "ART", 24, ("art",)),
    ("书法教室", "CALLIGRAPHY", 24, ("calligraphy",)),
    ("普通教学教室 A", "GENERAL", 24, ()),
    ("普通教学教室 B", "GENERAL", 24, ()),
    ("多功能教室", "MULTIPURPOSE", 28, ("mechanics", "art")),
)


def next_week(today: date) -> date:
    return today + timedelta(days=7 - today.weekday())


def demo_records(week: date, password_hash: str) -> dict[str, list[dict[str, Any]]]:
    rng = random.Random(42)
    faker = Faker("zh_CN")
    faker.seed_instance(42)
    records: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def add(entity: str, key: object, **values: Any) -> str:
        record_id = demo_id(entity, key)
        records[entity].append({"id": record_id, "organization_id": ORG_ID, **values})
        return record_id

    def availability(owner_id: str, kind: str) -> None:
        for key in sorted(all_keys()):
            day, code = key.split(":")
            add(
                "availability_rules",
                f"{owner_id}/{key}",
                owner_id=owner_id,
                owner_kind=kind,
                day_index=int(day),
                slot_code=code,
            )

    for slot in DEFAULT_SLOTS:
        add("time_slot_templates", slot.code, **asdict(slot), duration_minutes=slot.minutes)
    course_ids = []
    for index, (name, group, preferred, features) in enumerate(CATALOG):
        course_id = add(
            "courses",
            index,
            name=name,
            course_group=group,
            description=f"{name}课程，注重实践、兴趣与能力培养。",
            min_age=6,
            max_age=16,
            min_grade=1,
            max_grade=10,
            duration=35 if group == "QA" else 45,
            min_size=3,
            max_size=20,
            preferred_room=preferred,
        )
        course_ids.append(course_id)
        for feature in features:
            add(
                "course_features",
                f"{index}/{feature}",
                course_id=course_id,
                feature=feature,
                safety_required=preferred in {"DRONE", "LAB"},
            )
    for index, (name, kind, capacity, features) in enumerate(ROOMS):
        room_id = add("rooms", index, name=name, kind=kind, capacity=capacity)
        for feature in features:
            add("room_features", f"{index}/{feature}", room_id=room_id, feature=feature)
        availability(room_id, "ROOM")
    for index in range(2):
        add(
            "users",
            f"admin/{index}",
            username="admin" if index == 0 else "admin2",
            name=f"演示管理员{index + 1}",
            role="ADMIN",
            password_hash=password_hash,
            phone=f"TEST-ADMIN-{index + 1:02d}",
        )
    qualified: dict[str, list[str]] = defaultdict(list)
    for index in range(16):
        teacher_id = add(
            "users",
            f"teacher/{index}",
            username=f"teacher{index + 1:02d}",
            name=faker.name(),
            role="TEACHER",
            password_hash=password_hash,
            phone=f"TEST-TEACHER-{index + 1:02d}",
        )
        add(
            "teacher_profiles",
            index,
            user_id=teacher_id,
            teacher_code=f"T{index + 1:03d}",
            bio="演示教师资料，姓名由固定随机种子生成。",
        )
        availability(teacher_id, "TEACHER")
        primary = index if index < 13 else index - 9
        indices = {primary}
        if index < 4:
            indices.add(4 + index)
        for course_index in sorted(indices):
            course_id = course_ids[course_index]
            add(
                "teacher_qualifications",
                f"{index}/{course_index}",
                teacher_id=teacher_id,
                course_id=course_id,
            )
            qualified[course_id].append(teacher_id)
    definitions = {c["id"]: c for c in records["courses"]}
    offerings = []
    for index, course_id in enumerate(course_ids):
        definition = definitions[course_id]
        name, group = definition["name"], definition["course_group"]
        offering_id = add(
            "course_offerings",
            f"{week}/{index}",
            course_id=course_ids[index],
            week=week,
            name=f"{name} · {week}",
            status="APPROVED",
        )
        offerings.append(offering_id)
        for teacher_id in qualified[course_ids[index]]:
            add(
                "teacher_decisions",
                f"{offering_id}/{teacher_id}",
                teacher_id=teacher_id,
                offering_id=offering_id,
                decision="ACCEPTED",
                reason="模拟数据中的已完成开课审核",
            )
        for key in sorted(all_keys()):
            day, code = key.split(":")
            slot = next(s for s in DEFAULT_SLOTS if s.code == code)
            if (group == "QA") == (slot.kind == "QA"):
                add(
                    "offering_slots",
                    f"{offering_id}/{key}",
                    offering_id=offering_id,
                    day_index=int(day),
                    slot_code=code,
                )
    for index in range(220):
        student_id = add(
            "users",
            f"student/{index}",
            username=f"student{index + 1:03d}",
            name=faker.name(),
            role="STUDENT",
            password_hash=password_hash,
            phone=f"TEST-STUDENT-{index + 1:03d}",
        )
        age = rng.randint(6, 15)
        add(
            "student_profiles",
            index,
            user_id=student_id,
            student_code=f"S{index + 1:04d}",
            gender=rng.choice(["男", "女"]),
            age=age,
            grade=age - 5,
            notes="纯模拟数据；TEST 联系方式不可用于实际通信。",
        )
        availability(student_id, "STUDENT")
        selection = rng.sample(range(13), rng.randint(2, 4))
        if index == 0:
            selection = [5, 8, 12]  # Reserve programming for the end-to-end fresh enrollment demonstration.
        for course_index in selection:
            request_id = add(
                "enrollment_requests",
                f"{student_id}/{week}/{course_index}",
                student_id=student_id,
                course_id=course_ids[course_index],
                offering_id=offerings[course_index],
                week=week,
                status="APPROVED",
            )
            definition = definitions[course_ids[course_index]]
            codes = ["S8"] if definition["course_group"] == "QA" else [f"S{i}" for i in range(1, 8)]
            preferences = rng.sample([f"{day}:{code}" for day in range(5) for code in codes], 3)
            for rank, key in enumerate(preferences):
                day, code = key.split(":")
                add(
                    "enrollment_time_preferences",
                    f"{request_id}/{key}",
                    request_id=request_id,
                    day_index=int(day),
                    slot_code=code,
                    rank=rank,
                )
    return dict(records)


def demo_snapshot(week: date = date(2026, 11, 2), *, seconds: float = 10) -> Snapshot:
    """Pure fixture with the same business data as database seeding."""
    rows = demo_records(week, "not-a-credential")
    available = all_keys()
    qualifications: dict[str, set[str]] = defaultdict(set)
    for row in rows["teacher_qualifications"]:
        qualifications[row["teacher_id"]].add(row["course_id"])
    course_features: dict[str, set[str]] = defaultdict(set)
    for row in rows["course_features"]:
        course_features[row["course_id"]].add(row["feature"])
    room_features: dict[str, set[str]] = defaultdict(set)
    for row in rows["room_features"]:
        room_features[row["room_id"]].add(row["feature"])
    accepted: dict[str, set[str]] = defaultdict(set)
    for row in rows["teacher_decisions"]:
        accepted[row["offering_id"]].add(row["teacher_id"])
    preferences: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for row in rows["enrollment_time_preferences"]:
        preferences[row["request_id"]].append((row["rank"], f"{row['day_index']}:{row['slot_code']}"))
    allowed: dict[str, set[str]] = defaultdict(set)
    for row in rows["offering_slots"]:
        allowed[row["offering_id"]].add(f"{row['day_index']}:{row['slot_code']}")
    return Snapshot(
        ORG_ID,
        week,
        DEFAULT_SLOTS,
        tuple(Student(r["user_id"], r["age"], r["grade"], available) for r in rows["student_profiles"]),
        tuple(
            Teacher(r["user_id"], frozenset(qualifications[r["user_id"]]), available)
            for r in rows["teacher_profiles"]
        ),
        tuple(
            Room(r["id"], r["capacity"], frozenset(room_features[r["id"]]), available, kind=r["kind"])
            for r in rows["rooms"]
        ),
        tuple(
            Course(
                r["id"],
                r["name"],
                r["course_group"],
                r["min_age"],
                r["max_age"],
                r["min_grade"],
                r["max_grade"],
                r["min_size"],
                r["max_size"],
                frozenset(course_features[r["id"]]),
                (r["preferred_room"],),
                r["duration"],
            )
            for r in rows["courses"]
        ),
        tuple(
            Demand(
                r["id"],
                r["student_id"],
                r["course_id"],
                r["offering_id"],
                tuple(key for _, key in sorted(preferences[r["id"]])),
                frozenset(accepted[r["offering_id"]]),
                frozenset(allowed[r["offering_id"]]),
            )
            for r in rows["enrollment_requests"]
        ),
        Rules(solve_seconds=seconds),
    )


def scenario_snapshot(kind: str, week: date = date(2026, 11, 2), *, seconds: float = 60) -> Snapshot:
    snapshot = demo_snapshot(week, seconds=seconds)
    if kind == "feasible":
        return snapshot
    if kind == "conflict":
        return replace(
            snapshot, teachers=tuple(replace(t, available=frozenset({"0:S1"})) for t in snapshot.teachers)
        )
    if kind == "resource_shortage":
        return replace(snapshot, rooms=())
    raise ValueError("Unknown scenario; use feasible, conflict or resource_shortage")


def seed_demo(session: Session, week: date, password_hash: str) -> bool:
    session.execute(select(Organization).where(Organization.id == ORG_ID).with_for_update())
    org = session.get(Organization, ORG_ID)
    if org and org.seeded:
        return False
    if not org:
        org = Organization(id=ORG_ID, name="EduFlow 示范教培中心", slug="demo", rules=asdict(Rules()))
        session.add(org)
        session.flush()
    rows = demo_records(week, password_hash)
    order = [
        "time_slot_templates",
        "courses",
        "course_features",
        "rooms",
        "room_features",
        "users",
        "student_profiles",
        "teacher_profiles",
        "teacher_qualifications",
        "availability_rules",
        "course_offerings",
        "offering_slots",
        "teacher_decisions",
        "enrollment_requests",
        "enrollment_time_preferences",
    ]
    for entity in order:
        if entity in rows:
            session.execute(insert(ENTITY_MODELS[entity]), rows[entity])
    org.seeded = True
    session.flush()
    # Read the exact persisted configuration before declaring initialization successful.
    from eduflow.domain.time_rules import validate_templates

    validate_templates(SqlRecords(session, ORG_ID).snapshot(week).slots)
    return True
