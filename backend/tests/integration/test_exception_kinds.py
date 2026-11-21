from dataclasses import replace

import pytest

from eduflow.domain.entities import Principal
from eduflow.domain.validator import validate_schedule

from .helpers import active, confirm_all, execute

pytestmark = pytest.mark.integration


def approve_and_confirm(pg, change, actor):
    simulated = pg.platform.simulate.execute(actor, change["id"])
    assert simulated["proposal"]["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assert simulated["proposal"]["violations"] == []
    decided = execute(
        pg,
        "DecideChange",
        {
            "change_id": change["id"],
            "version": simulated["version"],
            "approved": True,
        },
    )
    assert decided["status"] == "WAITING_CONFIRMATION"
    with pg.platform.uow(pg.admin.organization_id) as transaction:
        assert transaction.records.get("schedule_versions", change["base_version_id"])["status"] == "ACTIVE"
        assert transaction.records.list("availability_exceptions") == []
        proposed = transaction.records.list("schedule_versions", {"change_request_id": change["id"]})[0]
    confirmed = confirm_all(pg, proposed["id"])
    assert confirmed["status"] == "ACTIVE"
    detail = pg.platform.queries.changes(actor, change["id"])
    assert detail["status"] == "APPLIED"
    assert detail["events"][-1]["to_state"] == "APPLIED"
    with pg.platform.uow(pg.admin.organization_id) as transaction:
        snapshot = transaction.records.snapshot(pg.snapshot.week)
        assert validate_schedule(snapshot, transaction.records.lessons(confirmed["id"])) == ()
        assert (
            transaction.records.get("schedule_versions", change["base_version_id"])["status"] == "SUPERSEDED"
        )
    return confirmed


@pytest.mark.parametrize(
    "kind", ["TEACHER_LEAVE", "ROOM_FAULT", "EQUIPMENT_FAULT", "SUBSTITUTE", "RESCHEDULE"]
)
def test_resource_exceptions_apply_only_after_confirmation_and_lock_unrelated_sessions(pg, kind):
    original = active(pg)
    lesson = pg.lessons[0]
    actor = Principal(lesson.teacher_id, pg.admin.organization_id, "TEACHER")
    payload = {"session_ids": [lesson.id]}
    if kind in {"TEACHER_LEAVE", "ROOM_FAULT", "EQUIPMENT_FAULT"}:
        slot = next(slot for slot in pg.snapshot.slots if slot.code == lesson.slot_code)
        starts_at, ends_at = slot.at(lesson.day)
        payload.update(
            starts_at=starts_at.isoformat(),
            ends_at=ends_at.isoformat(),
            owner_id=lesson.teacher_id if kind == "TEACHER_LEAVE" else lesson.room_id,
        )
    elif kind == "RESCHEDULE":
        payload["room_id"] = "room-b"
    change = execute(
        pg,
        "CreateChangeRequest",
        {
            "version_id": original["id"],
            "kind": kind,
            "reason": "验证异常闭环及事务一致性",
            "payload": payload,
        },
        actor,
    )
    confirmed = approve_and_confirm(pg, change, actor)
    changed = next(row for row in confirmed["lessons"] if row["id"] == lesson.id)
    if kind in {"TEACHER_LEAVE", "SUBSTITUTE"}:
        assert changed["teacher_id"] != lesson.teacher_id
        assert (changed["day"], changed["slot_code"]) == (str(lesson.day), lesson.slot_code)
    else:
        assert changed["room_id"] != lesson.room_id
    before = {row["id"]: row for row in original["lessons"]}
    assert all(row == before[row["id"]] for row in confirmed["lessons"] if row["id"] != lesson.id)
    with pg.platform.uow(pg.admin.organization_id) as transaction:
        exceptions = transaction.records.list("availability_exceptions", {"change_request_id": change["id"]})
        assert len(exceptions) == (1 if kind in {"TEACHER_LEAVE", "ROOM_FAULT", "EQUIPMENT_FAULT"} else 0)


@pytest.mark.parametrize("kind,status", [("STUDENT_LEAVE", "EXCUSED"), ("ENROLLMENT_CHANGE", "CANCELLED")])
def test_student_removal_keeps_classmates_and_updates_demand_only_at_activation(pg, kind, status):
    # A classmate keeps the QA class above its minimum size after one student leaves.
    with pg.platform.uow(pg.admin.organization_id) as transaction:
        transaction.records.add(
            "enrollment_requests",
            {
                "id": "classmate-qa-request",
                "student_id": "student-19",
                "course_id": "qa",
                "offering_id": "qa-offering",
                "week": pg.snapshot.week,
                "status": "APPROVED",
            },
        )
        transaction.commit()
    pg.lessons = tuple(
        replace(
            lesson,
            students=(*lesson.students, "student-19"),
            request_ids=(*lesson.request_ids, "classmate-qa-request"),
        )
        if lesson.id == "qa-lesson"
        else lesson
        for lesson in pg.lessons
    )
    original = active(pg)
    lesson = pg.lessons[-1]
    actor = Principal("student-20", pg.admin.organization_id, "STUDENT")
    payload = {"session_ids": [lesson.id]}
    if kind == "STUDENT_LEAVE":
        starts_at, ends_at = pg.snapshot.slots[-1].at(lesson.day)
        payload.update(starts_at=starts_at.isoformat(), ends_at=ends_at.isoformat(), owner_id=actor.id)
    change = execute(
        pg,
        "CreateChangeRequest",
        {
            "version_id": original["id"],
            "kind": kind,
            "reason": "本人请假或调整选课",
            "payload": payload,
        },
        actor,
    )
    confirmed = approve_and_confirm(pg, change, actor)
    updated = next(row for row in confirmed["lessons"] if row["id"] == lesson.id)
    assert tuple(updated["students"]) == ("student-19",)
    with pg.platform.uow(pg.admin.organization_id) as transaction:
        assert transaction.records.get("enrollment_requests", "qa-request")["status"] == status
        assert transaction.records.get("enrollment_requests", "classmate-qa-request")["status"] == "APPROVED"


def test_cancel_session_releases_only_cancelled_class_and_retains_history(pg):
    original = active(pg)
    change = execute(
        pg,
        "CreateChangeRequest",
        {
            "version_id": original["id"],
            "kind": "CANCEL_SESSION",
            "reason": "本次答疑取消并通知参与者",
            "payload": {"session_ids": ["qa-lesson"]},
        },
    )
    confirmed = approve_and_confirm(pg, change, pg.admin)
    cancelled = next(row for row in confirmed["lessons"] if row["id"] == "qa-lesson")
    assert cancelled["status"] == "CANCELLED"
    with pg.platform.uow(pg.admin.organization_id) as transaction:
        assert transaction.records.get("enrollment_requests", "qa-request")["status"] == "CANCELLED"
        rows = transaction.records.list(
            "course_sessions", {"version_id": confirmed["id"], "logical_id": "qa-lesson"}
        )
        assert (
            transaction.records.list(
                "resource_reservations", {"session_id": rows[0]["id"], "is_active": True}
            )
            == []
        )
        assert (
            next(row for row in transaction.records.lessons(original["id"]) if row.id == "qa-lesson").status
            == "SCHEDULED"
        )

    teacher = Principal("teacher-a", pg.admin.organization_id, "TEACHER")
    review = next(
        row for row in pg.platform.queries.reviews(teacher) if row["offering"]["id"] == "qa-offering"
    )
    execute(
        pg,
        "ReviewOffering",
        {"decision_id": review["id"], "version": review["version"], "decision": "ACCEPTED"},
        teacher,
    )
    with pg.platform.uow(pg.admin.organization_id) as transaction:
        assert transaction.records.get("enrollment_requests", "qa-request")["status"] == "CANCELLED"
    student = Principal("student-20", pg.admin.organization_id, "STUDENT")
    reapplied = execute(
        pg,
        "EnrollStudent",
        {
            "course_id": "qa",
            "week": str(pg.snapshot.week),
            "preferences": [{"day_index": 1, "slot_code": "S8"}],
        },
        student,
    )
    assert reapplied["id"] == "qa-request"
    assert reapplied["status"] == "PENDING_REVIEW"


def test_add_session_uses_a_real_reviewed_unassigned_demand_and_reconfirms(pg):
    original = active(pg)
    with pg.platform.uow(pg.admin.organization_id) as transaction:
        transaction.records.add(
            "enrollment_requests",
            {
                "id": "added-qa-request",
                "student_id": "student-0",
                "course_id": "qa",
                "offering_id": "qa-offering",
                "week": pg.snapshot.week,
                "status": "APPROVED",
            },
        )
        transaction.commit()
    change = execute(
        pg,
        "CreateChangeRequest",
        {
            "version_id": original["id"],
            "kind": "ADD_SESSION",
            "reason": "安排本周新增的已审核需求",
            "payload": {"session_ids": [], "request_ids": ["added-qa-request"]},
        },
    )
    confirmed = approve_and_confirm(pg, change, pg.admin)
    assert any(
        "added-qa-request" in row["request_ids"] and "student-0" in row["students"]
        for row in confirmed["lessons"]
    )
    before = {row["id"]: row for row in original["lessons"]}
    assert all(row == before[row["id"]] for row in confirmed["lessons"] if row["id"] in before)
