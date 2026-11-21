from collections import defaultdict
from uuid import uuid4

from eduflow.application.scheduling import new_version
from eduflow.domain.entities import Principal


def execute(pg, name, values, actor=None, key=None):
    return pg.platform.commands[name].execute(actor or pg.admin, values, key or str(uuid4()))


def candidate(pg, lessons=None, base=None):
    with pg.platform.uow(pg.admin.organization_id) as tx:
        store = tx.records
        version = new_version(
            store, pg.admin, store.snapshot(pg.snapshot.week), lessons or pg.lessons, "FEASIBLE", {}, base
        )
        tx.commit()
    return execute(pg, "ValidateVersion", {"version_id": version["id"], "version": version["version"]})


def publish(pg, version):
    return execute(pg, "PublishVersion", {"version_id": version["id"], "version": version["version"]})


def confirm_all(pg, version_id):
    with pg.platform.uow(pg.admin.organization_id) as tx:
        store = tx.records
        rows = {
            row["id"]: row["logical_id"] for row in store.list("course_sessions", {"version_id": version_id})
        }
        confirmations = store.list(
            "schedule_confirmations", {"session_id": list(rows), "decision": "PENDING"}
        )
        users = {u["id"]: u for u in store.list("users")}
    by_user = defaultdict(list)
    for confirmation in confirmations:
        by_user[confirmation["user_id"]].append(rows[confirmation["session_id"]])
    for user_id, logical_ids in by_user.items():
        actor = Principal(user_id, pg.admin.organization_id, users[user_id]["role"])
        execute(
            pg,
            "ConfirmSchedule",
            {"version_id": version_id, "session_ids": logical_ids, "decision": "ACCEPTED"},
            actor,
        )
    return pg.platform.queries.version(pg.admin, version_id)


def active(pg):
    version = publish(pg, candidate(pg))
    return confirm_all(pg, version["id"])
