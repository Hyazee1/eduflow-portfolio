from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, inspect, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from eduflow.adapters.background import relay_outbox, schedule_reminders
from eduflow.adapters.models import (
    Base,
    Confirmation,
    CourseFeature,
    CourseSession,
    Enrollment,
    Notification,
    Organization,
    OutboxEvent,
    ResourceReservation,
    RoomRow,
    ScheduleVersion,
    User,
)
from eduflow.application.scheduling import activate
from eduflow.bootstrap.app import create_app
from eduflow.domain.entities import Principal
from eduflow.domain.errors import BusinessError, Conflict, Forbidden, StaleVersion
from eduflow.domain.validator import validate_schedule

from .helpers import active, candidate, confirm_all, execute, publish

pytestmark = pytest.mark.integration


def test_real_migration_matches_models_and_round_trip(pg):
    with pg.engine.connect() as connection:
        assert (
            compare_metadata(
                MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata
            )
            == []
        )
        assert len(inspect(connection).get_table_names()) == 34
        assert "account_tokens" in inspect(connection).get_table_names()
    from pathlib import Path

    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["database_url"] = pg.settings.database_url
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    with pg.engine.connect() as connection:
        assert len(inspect(connection).get_table_names()) == 34


def test_T26_enrollment_review_solve_publish_confirm_real_database(pg):
    student = Principal("student-20", pg.admin.organization_id, "STUDENT")
    request = execute(
        pg,
        "EnrollStudent",
        {
            "week": str(pg.snapshot.week),
            "course_id": "main",
            "preferences": [{"day_index": 2, "slot_code": "S3"}],
        },
        student,
    )
    with pg.platform.uow(pg.admin.organization_id) as tx:
        review = tx.records.list(
            "teacher_decisions", {"offering_id": request["offering_id"], "teacher_id": "teacher-a"}
        )[0]
    teacher = Principal("teacher-a", pg.admin.organization_id, "TEACHER")
    execute(
        pg,
        "ReviewOffering",
        {"decision_id": review["id"], "version": review["version"], "decision": "ACCEPTED"},
        teacher,
    )
    job = execute(pg, "SolveWeek", {"week": str(pg.snapshot.week)})
    result = pg.platform.runner.execute(pg.admin.organization_id, job["id"])
    assert result["status"] == "COMPLETED"
    assert result["result"]["solver_status"] in {"FEASIBLE", "OPTIMAL"}
    version = pg.platform.queries.version(pg.admin, result["version_id"])
    validated = execute(pg, "ValidateVersion", {"version_id": version["id"], "version": version["version"]})
    pending = publish(pg, validated)
    assert pending["status"] == "PUBLISHED_PENDING_CONFIRMATION"
    assert confirm_all(pg, version["id"])["status"] == "ACTIVE"
    own = pg.platform.queries.schedule(student, pg.snapshot.week)
    assert all(s["students"] == [student.id] for s in own["sessions"])
    with pg.platform.uow(pg.admin.organization_id) as tx:
        assert (
            validate_schedule(tx.records.snapshot(pg.snapshot.week), tx.records.lessons(version["id"])) == ()
        )


def test_T31_concurrent_publication_has_one_pending_version(pg):
    versions = [candidate(pg), candidate(pg)]

    def attempt(version):
        try:
            publish(pg, version)
            return "published"
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(attempt, versions)) == ["conflict", "published"]
    with Session(pg.engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ScheduleVersion)
                .where(ScheduleVersion.status == "PUBLISHED_PENDING_CONFIRMATION")
            )
            == 1
        )
        assert session.scalar(select(func.count()).select_from(ResourceReservation)) == 0


def test_T35_concurrent_idempotency_replays_one_request(pg):
    student = Principal("student-20", pg.admin.organization_id, "STUDENT")
    values = {
        "week": str(pg.snapshot.week),
        "course_id": "main",
        "preferences": [{"day_index": 2, "slot_code": "S3"}],
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        outputs = list(
            executor.map(
                lambda _: execute(pg, "EnrollStudent", values, student, "same-enrollment-key"), range(2)
            )
        )
    assert outputs[0] == outputs[1]
    with Session(pg.engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Enrollment)
                .where(Enrollment.student_id == student.id, Enrollment.course_id == "main")
            )
            == 1
        )


def test_database_exclusion_constraint_rejects_duplicate_resource(pg):
    active(pg)
    with Session(pg.engine) as session:
        occupied = session.scalar(select(ResourceReservation).where(ResourceReservation.is_active.is_(True)))
        values = {
            column.key: getattr(occupied, column.key)
            for column in occupied.__table__.columns
            if column.key not in {"id", "created_at", "updated_at"}
        }
    with pytest.raises(IntegrityError, match="ex_resource_time"):
        with Session(pg.engine) as session, session.begin():
            session.add(ResourceReservation(**values))
            session.flush()


def test_missing_confirmation_blocks_activation_in_postgres(pg):
    version = publish(pg, candidate(pg))
    with Session(pg.engine) as session, session.begin():
        rows = select(CourseSession.id).where(CourseSession.version_id == version["id"])
        session.execute(
            update(Confirmation).where(Confirmation.session_id.in_(rows)).values(decision="ACCEPTED")
        )
        victim = session.scalar(select(Confirmation.id))
        session.execute(delete(Confirmation).where(Confirmation.id == victim))
    with pytest.raises(Conflict, match="必要确认"):
        with pg.platform.uow(pg.admin.organization_id) as tx:
            activate(tx.records, pg.admin, tx.records.get("schedule_versions", version["id"]), pg.clock.now())
            tx.commit()
    with Session(pg.engine) as session:
        assert session.scalar(select(func.count()).select_from(ResourceReservation)) == 0


def test_T27_final_resource_failure_preserves_old_active_and_last_confirmation(pg):
    original = active(pg)
    changed = (replace(pg.lessons[0], room_id="room-b"), *pg.lessons[1:])
    pending = publish(pg, candidate(pg, changed, original["id"]))
    with Session(pg.engine) as session, session.begin():
        session.execute(update(RoomRow).where(RoomRow.id == "room-b").values(status="CLOSED"))
    with pytest.raises(BusinessError, match="资源复检"):
        confirm_all(pg, pending["id"])
    with Session(pg.engine) as session:
        assert session.get(ScheduleVersion, original["id"]).status == "ACTIVE"
        assert session.get(ScheduleVersion, pending["id"]).status == "PUBLISHED_PENDING_CONFIRMATION"
        assert (
            session.scalar(
                select(func.count())
                .select_from(ResourceReservation)
                .where(ResourceReservation.is_active.is_(True))
            )
            > 0
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Confirmation).where(Confirmation.decision == "PENDING")
            )
            == 1
        )


def test_emergency_pause_invalidates_old_candidate_and_records_timeline(pg):
    original = active(pg)
    pending = candidate(pg, (replace(pg.lessons[0], room_id="room-b"), *pg.lessons[1:]), original["id"])
    with Session(pg.engine) as session:
        row_id = session.scalar(
            select(CourseSession.id).where(
                CourseSession.version_id == original["id"], CourseSession.logical_id == pg.lessons[0].id
            )
        )
    execute(pg, "EmergencyPause", {"session_id": row_id, "reason": "设备紧急故障"})
    with pytest.raises(StaleVersion):
        publish(pg, pending)
    updated = pg.platform.queries.version(pg.admin, original["id"])
    assert updated["version"] > original["version"]
    assert updated["report"]["hard_conflicts"] > 0
    changes = pg.platform.queries.changes(pg.admin)
    assert changes[0]["status"] == "APPLIED"
    assert [e["to_state"] for e in changes[0]["events"]] == [
        "SUBMITTED",
        "VALIDATING",
        "PENDING_ADMIN",
        "APPROVED",
        "APPLIED",
    ]


def test_T28_change_approval_scopes_and_cancelled_version_updates_workorder(pg):
    original = active(pg)
    teacher = Principal("teacher-a", pg.admin.organization_id, "TEACHER")
    change = execute(
        pg,
        "CreateChangeRequest",
        {
            "version_id": original["id"],
            "kind": "RESCHEDULE",
            "reason": "教学安排调整",
            "payload": {"session_ids": [pg.lessons[0].id], "room_id": "room-b"},
        },
        teacher,
    )
    simulated = pg.platform.simulate.execute(teacher, change["id"])
    assert simulated["proposal"]["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    decided = execute(
        pg, "DecideChange", {"change_id": change["id"], "version": simulated["version"], "approved": True}
    )
    assert decided["status"] == "WAITING_CONFIRMATION"
    assert pg.platform.queries.changes(teacher, change["id"])["status"] == decided["status"]
    with pg.platform.uow(pg.admin.organization_id) as tx:
        version = tx.records.list("schedule_versions", {"change_request_id": change["id"]})[0]
    execute(pg, "CancelVersion", {"version_id": version["id"], "version": version["version"]})
    assert pg.platform.queries.changes(teacher, change["id"])["status"] == "CANCELLED"
    assert pg.platform.queries.version(pg.admin, original["id"])["status"] == "ACTIVE"


def test_stale_simulation_rejected_after_resource_change(pg):
    original = active(pg)
    change = execute(
        pg,
        "CreateChangeRequest",
        {
            "version_id": original["id"],
            "kind": "RESCHEDULE",
            "reason": "课程调整申请",
            "payload": {"session_ids": [pg.lessons[0].id], "room_id": "room-b"},
        },
    )
    simulated = pg.platform.simulate.execute(pg.admin, change["id"])
    with Session(pg.engine) as session, session.begin():
        session.execute(update(RoomRow).where(RoomRow.id == "room-b").values(capacity=19))
    with pytest.raises(StaleVersion):
        execute(
            pg, "DecideChange", {"change_id": change["id"], "version": simulated["version"], "approved": True}
        )


def test_reenroll_excused_request_reuses_unique_record_and_requires_review(pg):
    with Session(pg.engine) as session, session.begin():
        row = session.get(Enrollment, "request-0")
        row.status = "EXCUSED"
    student = Principal("student-0", pg.admin.organization_id, "STUDENT")
    new = execute(
        pg,
        "EnrollStudent",
        {
            "week": str(pg.snapshot.week),
            "course_id": "main",
            "preferences": [{"day_index": 2, "slot_code": "S3"}],
        },
        student,
    )
    assert new["id"] == "request-0"
    assert new["status"] == "PENDING_REVIEW"


def test_safety_equipment_cannot_be_removed(pg):
    with Session(pg.engine) as session, session.begin():
        session.add(
            CourseFeature(
                organization_id=pg.admin.organization_id,
                course_id="main",
                feature="safety",
                safety_required=True,
            )
        )
    with pytest.raises(BusinessError, match="不能移除"):
        execute(
            pg,
            "SaveResource",
            {"entity": "courses", "id": "main", "version": 1, "values": {}, "features": []},
        )
    with Session(pg.engine) as session:
        assert session.get(User, "admin")
        assert session.scalar(select(CourseFeature.feature)) == "safety"


def test_institution_foreign_key_rejects_cross_tenant_reference(pg):
    with Session(pg.engine) as session, session.begin():
        session.add(Organization(id="other-org", name="Other", slug="other", rules={}))
    with pytest.raises(IntegrityError):
        with Session(pg.engine) as session, session.begin():
            session.add(CourseFeature(organization_id="other-org", course_id="main", feature="private"))
            session.flush()


def test_teacher_agent_only_reads_own_related_requests(pg):
    active(pg)
    student = Principal("student-0", pg.admin.organization_id, "STUDENT")
    execute(
        pg,
        "EnrollStudent",
        {
            "week": str(pg.snapshot.week),
            "course_id": "qa",
            "preferences": [{"day_index": 2, "slot_code": "S8"}],
        },
        student,
    )
    teacher = Principal("teacher-a", pg.admin.organization_id, "TEACHER")
    preferences = pg.platform.agent.tools.execute(
        teacher, "get_student_preferences", {"student_id": student.id}, str(uuid4())
    )
    assert {item["course_id"] for item in preferences} == {"main"}
    with pytest.raises(Forbidden):
        pg.platform.queries.schedule(student, pg.snapshot.week, owner_id="student-1")


def test_T23_T24_T25_real_http_permissions_and_refresh_rotation(pg):
    with TestClient(create_app(pg.settings, pg.clock)) as client:
        assert client.get("/api/v1/courses").status_code == 401
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "student-0", "password": "Integration-Test-Password!", "role": "STUDENT"},
        )
        assert response.status_code == 200
        data = response.json()
        headers = {"Authorization": "Bearer " + data["access_token"]}
        assert client.get("/api/v1/admin/users", headers=headers).status_code == 403
        assert client.get("/api/v1/students/student-1/schedule", headers=headers).status_code == 403
        old_refresh = client.cookies.get("eduflow_refresh")
        assert client.post("/api/v1/auth/refresh").status_code == 200
        client.cookies.set("eduflow_refresh", old_refresh)
        assert client.post("/api/v1/auth/refresh").status_code == 401
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_outbox_failure_preserves_business_then_retries_and_deduplicates(pg):
    student = Principal("student-20", pg.admin.organization_id, "STUDENT")
    request = execute(
        pg,
        "EnrollStudent",
        {
            "week": str(pg.snapshot.week),
            "course_id": "main",
            "preferences": [{"day_index": 2, "slot_code": "S3"}],
        },
        student,
    )
    job = execute(pg, "SolveWeek", {"week": str(pg.snapshot.week)})

    class FailingPublisher:
        def send_task(self, *args, **kwargs):
            raise ConnectionError("temporary test outage")

    relay_outbox(pg.platform.factory, FailingPublisher())
    with Session(pg.engine) as session:
        assert session.get(Enrollment, request["id"])
        event = session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "SOLVE"))
        assert event.delivered_at is None and event.attempts == 1
        count = session.scalar(select(func.count()).select_from(Notification))

    class Publisher:
        def send_task(self, *args, **kwargs):
            assert kwargs["args"][1] == job["id"]

    with Session(pg.engine) as session, session.begin():
        session.execute(update(OutboxEvent).values(next_attempt_at=None, delivered_at=None))
    relay_outbox(pg.platform.factory, Publisher())
    with Session(pg.engine) as session:
        assert session.scalar(select(func.count()).select_from(Notification)) == count
        assert (
            session.scalar(select(OutboxEvent.delivered_at).where(OutboxEvent.event_type == "SOLVE"))
            is not None
        )


def test_confirmation_12h_reminder_and_24h_escalation_deduplicate(pg):
    version = publish(pg, candidate(pg))
    from datetime import UTC, datetime

    with Session(pg.engine) as session, session.begin():
        session.execute(
            update(ScheduleVersion)
            .where(ScheduleVersion.id == version["id"])
            .values(
                published_at=datetime.now(UTC) - timedelta(hours=25),
                confirmation_deadline=datetime.now(UTC) - timedelta(hours=1),
            )
        )
    schedule_reminders(pg.platform.factory)
    schedule_reminders(pg.platform.factory)
    with Session(pg.engine) as session:
        reminders = session.scalars(select(OutboxEvent).where(OutboxEvent.dedup_key.is_not(None))).all()
        assert {e.dedup_key for e in reminders} == {
            f"confirm:{version['id']}:12h",
            f"confirm:{version['id']}:overdue",
        }


def test_failed_and_unknown_jobs_never_create_publishable_versions(pg):
    from eduflow.application.scheduling import RunSolveJob
    from eduflow.domain.entities import SolveResult

    for status in ("UNKNOWN", "MODEL_INVALID", "INFEASIBLE"):

        class Scheduler:
            def solve(self, snapshot):
                return SolveResult(status, (), 0.1, None, None, ("test status",), ())

        job = execute(pg, "SolveWeek", {"week": str(pg.snapshot.week)})
        result = RunSolveJob(pg.platform.uow, pg.clock, Scheduler()).execute(
            pg.admin.organization_id, job["id"]
        )
        assert result["result"]["solver_status"] == status
        assert result["version_id"] is None


def test_T34_notifications_use_persisted_session_time_and_templates(pg):
    original = active(pg)
    from datetime import datetime

    from eduflow.domain.time_rules import SHANGHAI

    schedule_reminders(pg.platform.factory, datetime.combine(pg.snapshot.week, datetime.max.time(), SHANGHAI))
    with Session(pg.engine) as session:
        messages = session.scalars(select(OutboxEvent).where(OutboxEvent.dedup_key.like("daily:%"))).all()
        assert any("S3 10:00—10:45" in message.payload["content"] for message in messages)
        assert any("S8 16:55—17:30" in message.payload["content"] for message in messages)
    assert pg.platform.queries.schedule(pg.admin, pg.snapshot.week)["slots"][2]["start"] == "10:00"
    assert original["time_templates"][6]["start"] == "16:00"


def test_job_retry_and_duplicate_completion_do_not_create_extra_versions(pg):
    from eduflow.adapters.background import prepare_job_retry
    from eduflow.adapters.models import SolveJob

    job = execute(pg, "SolveWeek", {"week": str(pg.snapshot.week)})
    with Session(pg.engine) as session, session.begin():
        session.execute(
            update(SolveJob)
            .where(SolveJob.id == job["id"])
            .values(status="RUNNING", started_at=pg.clock.now())
        )
    prepare_job_retry(pg.platform.factory, pg.admin.organization_id, job["id"])
    first = pg.platform.runner.execute(pg.admin.organization_id, job["id"])
    second = pg.platform.runner.execute(pg.admin.organization_id, job["id"])
    assert first["version_id"] == second["version_id"]
    with Session(pg.engine) as session:
        assert session.scalar(select(func.count()).select_from(ScheduleVersion)) == 1


def test_attendance_and_feedback_require_finished_owned_lesson(pg):
    original = active(pg)
    with Session(pg.engine) as session:
        lesson = session.scalar(
            select(CourseSession).where(
                CourseSession.version_id == original["id"], CourseSession.logical_id == pg.lessons[0].id
            )
        )
        record_id = lesson.id
    teacher = Principal("teacher-a", pg.admin.organization_id, "TEACHER")
    with pytest.raises(BusinessError, match="尚未结束"):
        execute(
            pg,
            "RecordAttendance",
            {"session_id": record_id, "student_id": "student-0", "status": "PRESENT"},
            teacher,
        )
    pg.clock.current += timedelta(days=4)
    result = execute(
        pg,
        "RecordAttendance",
        {"session_id": record_id, "student_id": "student-0", "status": "PRESENT"},
        teacher,
    )
    assert result["status"] == "PRESENT"
    feedback = execute(
        pg,
        "RecordFeedback",
        {"session_id": record_id, "student_id": "student-0", "content": "完成课程任务", "rating": 5},
        teacher,
    )
    assert feedback["content"] == "完成课程任务"


def test_offline_agent_parses_owned_explicit_reschedule(pg):
    active(pg)
    teacher = Principal("teacher-a", pg.admin.organization_id, "TEACHER")
    result = pg.platform.agent.execute(
        teacher, "周一 S1 调到周一 S5", {"week": str(pg.snapshot.week)}, str(uuid4())
    )
    assert result["mode"] == "offline"
    assert [c["name"] for c in result["tool_calls"]] == [
        "get_schedule_context",
        "create_change_request",
        "simulate_reschedule",
    ]
    change = result["tool_calls"][-1]["result"]
    assert change["proposal"]["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assert (
        next(lesson for lesson in change["proposal"]["lessons"] if lesson["id"] == pg.lessons[0].id)[
            "slot_code"
        ]
        == "S5"
    )
