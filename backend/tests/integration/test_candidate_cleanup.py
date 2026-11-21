from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from eduflow.adapters.demo_week import create_full_day_demo
from eduflow.bootstrap.app import create_app
from eduflow.domain.entities import Principal
from eduflow.domain.errors import Conflict, Forbidden, NotFound, StaleVersion

from .helpers import candidate, execute, publish

pytestmark = pytest.mark.integration


def test_delete_candidate_detaches_job_keeps_enrollment_and_replays(pg):
    version = candidate(pg)
    with pg.platform.uow(pg.admin.organization_id) as tx:
        job = tx.records.add(
            "solve_jobs",
            {
                "requested_by": pg.admin.id,
                "week": pg.snapshot.week,
                "status": "COMPLETED",
                "snapshot": pg.snapshot.to_dict(),
                "version_id": version["id"],
                "result": {"solver_status": "FEASIBLE", "wall_seconds": 1, "unmet_requests": []},
            },
        )
        before = tx.records.list("enrollment_requests")
        tx.commit()
    data = {"version_id": version["id"], "version": version["version"]}
    for actor in (Principal("teacher-a", "org", "TEACHER"), Principal("student-0", "org", "STUDENT")):
        with pytest.raises(Forbidden):
            execute(pg, "DeleteDraftVersion", data, actor)
    with pytest.raises(NotFound):
        execute(pg, "DeleteDraftVersion", data, Principal("other", "other-org", "ADMIN"))
    with pytest.raises(StaleVersion):
        execute(pg, "DeleteDraftVersion", {**data, "version": 999})
    key = str(uuid4())
    result = execute(pg, "DeleteDraftVersion", data, key=key)
    assert result == {"id": version["id"], "deleted": True}
    assert execute(pg, "DeleteDraftVersion", data, key=key) == result
    with pg.platform.uow(pg.admin.organization_id) as tx:
        assert tx.records.list("enrollment_requests") == before
        assert not tx.records.list("course_sessions", {"version_id": version["id"]})
        assert not tx.records.list("session_enrollments")
        kept = tx.records.get("solve_jobs", job["id"])
        assert kept["version_id"] is None and kept["snapshot"] == {}
        assert kept["result"]["candidate_deleted"]
        assert len(tx.records.list("audit_logs", {"action": "schedule.delete_draft"})) == 1


def test_published_and_referenced_candidates_cannot_be_deleted(pg):
    version = candidate(pg)
    with pg.platform.uow(pg.admin.organization_id) as tx:
        dependent = tx.records.add(
            "schedule_versions",
            {
                "week": pg.snapshot.week,
                "status": "DRAFT",
                "solver_status": "FEASIBLE",
                "base_version_id": version["id"],
                "snapshot": pg.snapshot.to_dict(),
                "report": {},
            },
        )
        tx.commit()
    assert not pg.platform.queries.version(pg.admin, version["id"])["can_delete"]
    with pytest.raises(Conflict, match="引用"):
        execute(pg, "DeleteDraftVersion", {"version_id": version["id"], "version": version["version"]})
    execute(pg, "DeleteDraftVersion", {"version_id": dependent["id"], "version": 1})
    published = publish(pg, version)
    assert not pg.platform.queries.version(pg.admin, published["id"])["can_delete"]
    cancelled = execute(pg, "CancelVersion", {"version_id": published["id"], "version": published["version"]})
    with pytest.raises(Conflict):
        execute(pg, "DeleteDraftVersion", {"version_id": cancelled["id"], "version": cancelled["version"]})


def test_delete_is_atomic_when_audit_fails(pg, monkeypatch):
    version = candidate(pg)

    def fail(*args, **kwargs):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr("eduflow.application.candidate_cleanup.audit", fail)
    with pytest.raises(RuntimeError):
        execute(pg, "DeleteDraftVersion", {"version_id": version["id"], "version": version["version"]})
    assert pg.platform.queries.version(pg.admin, version["id"])["lessons"]


def test_never_published_cancelled_candidate_can_be_deleted(pg):
    version = candidate(pg)
    cancelled = execute(pg, "CancelVersion", {"version_id": version["id"], "version": version["version"]})
    assert pg.platform.queries.version(pg.admin, cancelled["id"])["can_delete"]
    assert execute(
        pg, "DeleteDraftVersion", {"version_id": cancelled["id"], "version": cancelled["version"]}
    )["deleted"]


def test_delete_and_publish_share_transaction_lock(pg):
    version = candidate(pg)
    barrier = Barrier(2)

    def run(name):
        barrier.wait(timeout=10)
        try:
            return execute(pg, name, {"version_id": version["id"], "version": version["version"]})
        except (Conflict, NotFound) as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("DeleteDraftVersion", "PublishVersion")))
    assert sum(isinstance(r, dict) for r in results) == 1
    with pg.platform.uow(pg.admin.organization_id) as tx:
        versions = tx.records.list("schedule_versions", {"id": version["id"]})
        assert not versions or versions[0]["status"] == "PUBLISHED_PENDING_CONFIRMATION"


def test_delete_http_and_old_rule_revalidation(pg):
    version = candidate(pg)
    with TestClient(create_app(pg.settings, pg.clock)) as client:
        token = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "role": "ADMIN", "password": "Integration-Test-Password!"},
        ).json()["access_token"]
        response = client.post(
            f"/api/v1/admin/schedule/versions/{version['id']}/delete",
            json={"version": version["version"]},
            headers={"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 200 and response.json()["deleted"]
    old = candidate(pg)
    with pg.platform.uow(pg.admin.organization_id) as tx:
        tx.records.set_rules({**pg.snapshot.to_dict()["rules"], "require_full_day_coverage": True})
        tx.commit()
    with pytest.raises(StaleVersion, match="旧覆盖规则"):
        execute(pg, "ValidateVersion", {"version_id": old["id"], "version": old["version"]})


def test_independent_demo_week_uses_persisted_types_and_preserves_source(pg):
    with pg.platform.factory.begin() as session:
        from eduflow.adapters.repository import SqlRecords

        store = SqlRecords(session, "org")
        before = store.list("enrollment_requests")
        old_slots = store.list("offering_slots")
        first = store.list("course_offerings")[0]
        store.remove_links("offering_slots", {"offering_id": first["id"]})
        store.add("offering_slots", {"offering_id": first["id"], "day_index": 0, "slot_code": "S8"})
        bad_before = store.list("offering_slots")
        result = create_full_day_demo(session, "org")
        assert result["created"] and result["request_count"] == len(before)
        assert not create_full_day_demo(session, "org")["created"]
        assert store.list("enrollment_requests", {"week": pg.snapshot.week}) == before
        assert (
            store.list(
                "offering_slots",
                {
                    "offering_id": [
                        x["id"] for x in store.list("course_offerings", {"week": pg.snapshot.week})
                    ]
                },
            )
            == bad_before
        )
        assert old_slots
        from datetime import date

        new = store.snapshot(date.fromisoformat(result["week"]))
        for d in new.demands:
            c = next(c for c in new.courses if c.id == d.course_id)
            assert all((key.endswith("S8")) == (c.group == "QA") for key in d.allowed)
