from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from eduflow.bootstrap.app import create_app
from eduflow.domain.errors import Conflict

from .helpers import active, execute

pytestmark = pytest.mark.integration


def test_room_hours_http_requires_admin_and_rejects_stale_writes_and_foreign_rooms(pg):
    path = "/api/v1/admin/rooms/room-a/availability"
    with TestClient(create_app(pg.settings, pg.clock)) as client:

        def login(username, role):
            response = client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "Integration-Test-Password!", "role": role},
            )
            assert response.status_code == 200
            return {"Authorization": "Bearer " + response.json()["access_token"]}

        for username, role in [("student-0", "STUDENT"), ("teacher-a", "TEACHER")]:
            denied = login(username, role)
            assert client.get(path, headers=denied).status_code == 403
            assert (
                client.post(
                    path,
                    json={"version": 1, "slots": []},
                    headers={**denied, "Idempotency-Key": str(uuid4())},
                ).status_code
                == 403
            )

        headers = login("admin", "ADMIN")
        before = client.get(path, headers=headers).json()
        slots = [
            {"day_index": row["day_index"], "slot_code": row["slot_code"]} for row in before["availability"]
        ]
        body = {"version": before["room"]["version"], "slots": slots[1:]}
        key = str(uuid4())
        result = client.post(path, json=body, headers={**headers, "Idempotency-Key": key})
        assert result.status_code == 200
        assert result.json()["version"] == before["room"]["version"] + 1
        assert (
            client.post(path, json=body, headers={**headers, "Idempotency-Key": key}).json() == result.json()
        )
        stale = client.post(path, json=body, headers={**headers, "Idempotency-Key": str(uuid4())})
        assert stale.status_code == 409
        assert (
            client.get(
                "/api/v1/admin/rooms/another-organization-room/availability", headers=headers
            ).status_code
            == 404
        )
        assert (
            client.post(
                path, json={"slots": []}, headers={**headers, "Idempotency-Key": str(uuid4())}
            ).status_code
            == 422
        )
        with pg.platform.uow(pg.admin.organization_id) as transaction:
            room = next(
                room for room in transaction.records.snapshot(pg.snapshot.week).rooms if room.id == "room-a"
            )
        removed = str(slots[0]["day_index"]) + ":" + slots[0]["slot_code"]
        assert removed not in room.available


def test_room_hours_preserve_active_lessons_and_roll_back_room_revision(pg):
    version = active(pg)
    lesson = pg.lessons[0]
    before = pg.platform.queries.room_availability(pg.admin, lesson.room_id)
    slots = [
        {"day_index": row["day_index"], "slot_code": row["slot_code"]}
        for row in before["availability"]
        if (row["day_index"], row["slot_code"]) != (lesson.day.weekday(), lesson.slot_code)
    ]
    with pytest.raises(Conflict, match="已生效课表"):
        execute(
            pg,
            "SaveAvailability",
            {
                "owner_kind": "ROOM",
                "owner_id": lesson.room_id,
                "version": before["room"]["version"],
                "slots": slots,
            },
        )
    after = pg.platform.queries.room_availability(pg.admin, lesson.room_id)
    assert after == before
    current = pg.platform.queries.version(pg.admin, version["id"])
    assert current["status"] == "ACTIVE"
    assert current["lessons"] == version["lessons"]


@pytest.mark.parametrize(
    "role,field,value", [("STUDENT", "notes", "新生试听后跟进"), ("TEACHER", "bio", "科学教学与实践经验")]
)
def test_admin_creates_complete_inactive_crm_profile(pg, role, field, value):
    with TestClient(create_app(pg.settings, pg.clock)) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Integration-Test-Password!", "role": "ADMIN"},
        )
        headers = {"Authorization": "Bearer " + login.json()["access_token"], "Idempotency-Key": str(uuid4())}
        body = {
            "role": role,
            "username": "new-" + role.lower(),
            "name": "测试新档案",
            "password": "New-Profile-Password!",
            "active": False,
            "age": 10,
            "grade": 5,
            field: value,
        }
        response = client.post("/api/v1/admin/users", json=body, headers=headers)
        assert response.status_code == 200
        created = response.json()
        assert created["active"] is False
        with pg.platform.uow(pg.admin.organization_id) as transaction:
            entity = "student_profiles" if role == "STUDENT" else "teacher_profiles"
            profile = transaction.records.list(entity, {"user_id": created["id"]})[0]
        assert profile[field] == value
        denied = client.post(
            "/api/v1/auth/login",
            json={"username": body["username"], "password": body["password"], "role": role},
        )
        assert denied.status_code == 401
