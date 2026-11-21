from dataclasses import replace
from datetime import timedelta

import pytest

from eduflow.application.scheduling import (
    CancelVersion,
    ConfirmSchedule,
    PublishVersion,
    activate,
    new_version,
)
from eduflow.domain.entities import Principal
from eduflow.domain.errors import Conflict
from support.fakes import FixedClock, MemoryDatabase, MemoryRecords


@pytest.fixture
def pending(legal_week):
    snapshot, lessons = legal_week
    store = MemoryRecords(snapshot)
    clock = FixedClock()
    admin = Principal("admin", snapshot.organization_id, "ADMIN")
    version = new_version(store, admin, snapshot, lessons, "FEASIBLE", {})
    version = store.update("schedule_versions", version["id"], {"status": "VALIDATED"}, version["version"])
    database = MemoryDatabase(store)
    PublishVersion(database, clock).execute(
        admin, {"version_id": version["id"], "version": version["version"]}, "publish-first"
    )
    return database, clock, admin, version["id"], lessons


def accept_all(database, version_id):
    rows = database.records.list("course_sessions", {"version_id": version_id})
    for confirmation in database.records.list(
        "schedule_confirmations", {"session_id": [r["id"] for r in rows]}
    ):
        database.records.update(
            "schedule_confirmations", confirmation["id"], {"decision": "ACCEPTED"}, confirmation["version"]
        )


def test_missing_confirmation_cannot_activate(pending):
    database, clock, admin, version_id, _ = pending
    accept_all(database, version_id)
    database.records.rows["schedule_confirmations"].pop(
        next(iter(database.records.rows["schedule_confirmations"]))
    )
    with pytest.raises(Conflict, match="必要确认"):
        with database(admin.organization_id) as tx:
            activate(tx.records, admin, tx.records.get("schedule_versions", version_id), clock.now())
            tx.commit()
    assert database.records.reservations is None
    assert database.records.get("schedule_versions", version_id)["status"] == "PUBLISHED_PENDING_CONFIRMATION"


def test_T27_failed_activation_rolls_back_confirmation(pending):
    database, clock, admin, version_id, lessons = pending
    accept_all(database, version_id)
    row = next(
        c for c in database.records.list("schedule_confirmations") if c["user_id"] == lessons[0].teacher_id
    )
    database.records.update("schedule_confirmations", row["id"], {"decision": "PENDING"}, row["version"])
    database.records.fail_reservations = True
    actor = Principal(row["user_id"], admin.organization_id, "TEACHER")
    with pytest.raises(Conflict, match="资源"):
        ConfirmSchedule(database, clock).execute(
            actor,
            {"version_id": version_id, "session_ids": [lessons[0].id], "decision": "ACCEPTED"},
            "confirm-failure",
        )
    assert database.records.get("schedule_confirmations", row["id"])["decision"] == "PENDING"
    assert database.records.reservations is None


def test_activation_rechecks_class_start_time(pending):
    database, clock, admin, version_id, lessons = pending
    accept_all(database, version_id)
    clock.current += timedelta(days=4)
    version = database.records.get("schedule_versions", version_id)
    database.records.update(
        "schedule_versions",
        version_id,
        {"confirmation_deadline": clock.now() + timedelta(hours=24)},
        version["version"],
    )
    with pytest.raises(Conflict, match="已开始"):
        with database(admin.organization_id) as tx:
            activate(tx.records, admin, tx.records.get("schedule_versions", version_id), clock.now())


def test_T35_replay_does_not_create_duplicate_records(pending):
    database, clock, admin, version_id, _ = pending
    original = database.records.get("schedule_versions", version_id)
    command = CancelVersion(database, clock)
    values = {"version_id": version_id, "version": original["version"]}
    first = command.execute(admin, values, "cancel-stable")
    count = len(database.records.list("audit_logs"))
    assert command.execute(admin, values, "cancel-stable") == first
    assert len(database.records.list("audit_logs")) == count
    with pytest.raises(Conflict, match="不同"):
        command.execute(admin, {**values, "version": original["version"] + 1}, "cancel-stable")


def test_changed_members_require_both_old_and_new_participants(legal_week):
    from eduflow.application.scheduling import confirmation_requirements

    _, lessons = legal_week
    changed = replace(lessons[0], teacher_id="teacher-new", students=("student-new",), request_ids=("new",))
    assert confirmation_requirements((changed,), (lessons[0],))[changed.id] == {
        "teacher-new",
        lessons[0].teacher_id,
        "student-new",
        *lessons[0].students,
    }
