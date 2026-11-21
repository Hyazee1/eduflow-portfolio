import os
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from sqlalchemy import create_engine, insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from eduflow.adapters.models import ENTITY_MODELS, Base, Organization
from eduflow.bootstrap.composition import build_platform
from eduflow.bootstrap.settings import Settings
from eduflow.domain.entities import Principal
from support.fakes import FixedClock

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def pg_engine():
    if os.environ.get("EDUFLOW_RUN_INTEGRATION") != "1":
        pytest.skip("Set EDUFLOW_RUN_INTEGRATION=1 after preparing the dedicated PostgreSQL test database.")
    application = make_url(Settings().database_url)
    target = make_url(
        os.environ.get("EDUFLOW_TEST_DATABASE_URL")
        or application.set(database="eduflow_ai_test").render_as_string(hide_password=False)
    )
    if (
        not target.database
        or not target.database.endswith("_test")
        or target.database == application.database
    ):
        raise RuntimeError("Refusing to reset a database that is not an isolated *_test database")
    engine = create_engine(target)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT current_database()")) == target.database
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["database_url"] = target.render_as_string(hide_password=False)
    command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def pg(pg_engine, legal_week):
    snapshot, lessons = legal_week
    with pg_engine.begin() as connection:
        actual = connection.scalar(text("SELECT current_database()"))
        if not actual.endswith("_test") or actual == make_url(Settings().database_url).database:
            raise RuntimeError("Refusing destructive test cleanup outside *_test database")
        tables = ", ".join(
            connection.dialect.identifier_preparer.quote(name) for name in Base.metadata.tables
        )
        connection.exec_driver_sql("TRUNCATE " + tables + " RESTART IDENTITY CASCADE")
    rows = {name: [] for name in ENTITY_MODELS}

    def add(entity, **values):
        rows[entity].append({"organization_id": snapshot.organization_id, **values})

    password = PasswordHasher().hash("Integration-Test-Password!")
    people = [(Principal("admin", snapshot.organization_id, "ADMIN"), None)]
    people += [(Principal(t.id, snapshot.organization_id, "TEACHER"), t) for t in snapshot.teachers]
    people += [(Principal(s.id, snapshot.organization_id, "STUDENT"), s) for s in snapshot.students]
    for person, source in people:
        add(
            "users",
            id=person.id,
            username=person.id,
            name=person.id,
            role=person.role,
            password_hash=password,
        )
        if person.role == "TEACHER":
            add("teacher_profiles", user_id=person.id, teacher_code=person.id)
            for course in source.qualifications:
                add("teacher_qualifications", teacher_id=person.id, course_id=course)
        elif person.role == "STUDENT":
            add(
                "student_profiles",
                user_id=person.id,
                student_code=person.id,
                age=source.age,
                grade=source.grade,
            )
    for course in snapshot.courses:
        add(
            "courses",
            id=course.id,
            name=course.name,
            course_group=course.group,
            min_age=course.min_age,
            max_age=course.max_age,
            min_grade=course.min_grade,
            max_grade=course.max_grade,
            min_size=course.min_size,
            max_size=course.max_size,
            duration=course.duration,
        )
    for room in snapshot.rooms:
        add("rooms", id=room.id, name=room.id, kind=room.kind, capacity=room.capacity)
    for source, kind in [
        *((s, "STUDENT") for s in snapshot.students),
        *((t, "TEACHER") for t in snapshot.teachers),
        *((r, "ROOM") for r in snapshot.rooms),
    ]:
        for key in sorted(source.available):
            day, code = key.split(":")
            add("availability_rules", owner_id=source.id, owner_kind=kind, day_index=int(day), slot_code=code)
    for slot in snapshot.slots:
        add("time_slot_templates", **asdict(slot), duration_minutes=slot.minutes)
    offerings = {}
    for demand in snapshot.demands:
        offerings.setdefault(demand.offering_id, demand)
        add(
            "enrollment_requests",
            id=demand.id,
            student_id=demand.student_id,
            course_id=demand.course_id,
            offering_id=demand.offering_id,
            week=snapshot.week,
            status="APPROVED",
            priority=demand.priority,
        )
        for rank, key in enumerate(demand.preferred):
            day, code = key.split(":")
            add(
                "enrollment_time_preferences",
                request_id=demand.id,
                day_index=int(day),
                slot_code=code,
                rank=rank,
            )
    for offering_id, demand in offerings.items():
        add(
            "course_offerings",
            id=offering_id,
            course_id=demand.course_id,
            week=snapshot.week,
            name=offering_id,
            status="APPROVED",
            min_age=6,
            max_age=15,
        )
        for teacher in sorted(demand.approved_teachers):
            add("teacher_decisions", teacher_id=teacher, offering_id=offering_id, decision="ACCEPTED")
        for key in sorted(demand.allowed):
            day, code = key.split(":")
            add("offering_slots", offering_id=offering_id, day_index=int(day), slot_code=code)
    with Session(pg_engine) as session, session.begin():
        session.add(
            Organization(
                id=snapshot.organization_id,
                name="Integration School",
                slug="demo",
                rules=asdict(snapshot.rules),
                seeded=True,
            )
        )
        session.flush()
        for table in Base.metadata.sorted_tables:
            name = table.name
            if name in rows and rows[name]:
                session.execute(insert(ENTITY_MODELS[name]), rows[name])
    clock = FixedClock()
    settings = Settings(
        portal="TEST",
        database_url=pg_engine.url.render_as_string(hide_password=False),
        jwt_secret="integration-secret-for-testing-only-2026",
    )
    platform = build_platform(settings, clock)
    yield SimpleNamespace(
        platform=platform,
        clock=clock,
        snapshot=snapshot,
        lessons=lessons,
        admin=people[0][0],
        settings=settings,
        engine=pg_engine,
    )
    platform.engine.dispose()
