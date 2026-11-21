from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from eduflow.bootstrap.app import create_app
from eduflow.domain.entities import Principal
from eduflow.domain.errors import Conflict

from .helpers import active, execute

pytestmark = pytest.mark.integration


def test_future_week_offering_does_not_move_default_schedule_and_catalog(pg):
    expected = str(pg.snapshot.week)
    assert pg.platform.queries.bootstrap(pg.admin)["default_week"] == expected
    with pg.platform.uow(pg.admin.organization_id) as transaction:
        transaction.records.add(
            "course_offerings",
            {
                "course_id": "main",
                "week": pg.snapshot.week + timedelta(days=14),
                "name": "以后周的开课计划",
                "status": "PENDING_REVIEW",
                "min_age": 6,
                "max_age": 15,
            },
        )
        transaction.commit()
    assert pg.platform.queries.bootstrap(pg.admin)["default_week"] == expected
    assert {course["week"] for course in pg.platform.queries.catalog(pg.admin)} == {expected}


def test_teacher_cannot_withdraw_review_behind_an_active_class(pg):
    version = active(pg)
    teacher = Principal("teacher-a", pg.admin.organization_id, "TEACHER")
    before = next(
        review
        for review in pg.platform.queries.reviews(teacher)
        if review["offering"]["id"] == pg.lessons[0].offering_id
    )
    assert before["active_teaching"] is True
    with pytest.raises(Conflict, match="已生效课表"):
        execute(
            pg,
            "ReviewOffering",
            {
                "decision_id": before["id"],
                "version": before["version"],
                "decision": "REJECTED",
                "reason": "改为请假处理",
            },
            teacher,
        )
    after = next(review for review in pg.platform.queries.reviews(teacher) if review["id"] == before["id"])
    assert after["decision"] == "ACCEPTED"
    assert after["version"] == before["version"]
    current = pg.platform.queries.version(pg.admin, version["id"])
    assert current["status"] == "ACTIVE"
    assert current["lessons"] == version["lessons"]


@pytest.mark.parametrize("week", ["invalid-date", "2026-11-03"])
def test_agent_rejects_invalid_week_input_as_validation_error(pg, week):
    with TestClient(create_app(pg.settings, pg.clock)) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Integration-Test-Password!", "role": "ADMIN"},
        )
        response = client.post(
            "/api/v1/agent/chat",
            json={"message": "统计本周课程", "context": {"week": week}},
            headers={
                "Authorization": "Bearer " + login.json()["access_token"],
                "Idempotency-Key": "invalid-week-" + week,
            },
        )
        assert response.status_code == 422
        assert response.json()["request_id"]


def test_schedule_uses_saved_version_time_axis_instead_of_a_new_template(pg):
    version = active(pg)
    # Simulate a staged configuration migration; historical display must keep its saved axis.
    with pg.platform.uow(pg.admin.organization_id) as transaction:
        slot = transaction.records.list("time_slot_templates", {"code": "S1"})[0]
        transaction.records.update("time_slot_templates", slot["id"], {"start": "08:05"}, slot["version"])
        transaction.commit()
    schedule = pg.platform.queries.schedule(pg.admin, pg.snapshot.week, version_id=version["id"])
    assert next(slot for slot in schedule["slots"] if slot["code"] == "S1")["start"] == "08:00"
    assert (
        next(slot for slot in schedule["version_slots"][version["id"]] if slot["code"] == "S1")[
            "duration_minutes"
        ]
        == 45
    )
