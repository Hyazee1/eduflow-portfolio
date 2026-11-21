import os
import subprocess
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from eduflow.adapters.initialization import initialize_blank
from eduflow.adapters.models import AccountToken, AuthSession, Organization, StudentProfile, User
from eduflow.bootstrap.app import create_app
from eduflow.bootstrap.settings import Settings

pytestmark = pytest.mark.integration
PASSWORD = "Integration-Test-Password!"
NEW_PASSWORD = "Integration-Replaced-Password!"


def configured(pg, portal, **values):
    return Settings(
        database_url=pg.settings.database_url,
        jwt_secret="test-only-delivery-master-signing-value",
        portal=portal,
        **values,
    )


def test_public_spa_refuses_management_paths_and_keeps_personal_routes(pg, tmp_path):
    distribution = tmp_path / "public-build"
    distribution.mkdir()
    (distribution / "index.html").write_text("<main>public-portal-fixture</main>")
    with TestClient(
        create_app(configured(pg, "PUBLIC", frontend_dir=distribution), pg.clock),
        base_url="http://127.0.0.1:8100",
    ) as client:
        for route in ("/login", "/courses", "/reviews", "/schedule", "/account", "/accept"):
            assert client.get(route).status_code == 200
        for route in (
            "/students",
            "/teachers",
            "/catalog",
            "/rooms",
            "/demand",
            "/offering-reviews",
            "/audit",
            "/scheduling",
            "/approvals",
            "/analytics",
            "/rules",
        ):
            response = client.get(route)
            assert response.status_code == 404
            assert "public-portal-fixture" not in response.text


def login(client, username, role, password=PASSWORD):
    return client.post(
        "/api/v1/auth/login",
        headers={"X-EduFlow-Request": "1"},
        json={"username": username, "role": role, "password": password},
    )


def headers(response):
    assert response.status_code == 200, response.text
    return {
        "Authorization": "Bearer " + response.json()["access_token"],
        "Idempotency-Key": str(uuid4()),
        "X-EduFlow-Request": "1",
    }


def test_real_portal_routes_tokens_cookies_and_shared_identity(pg):
    with (
        TestClient(create_app(configured(pg, "ADMIN"), pg.clock), base_url="http://127.0.0.1:8000") as admin,
        TestClient(
            create_app(configured(pg, "PUBLIC"), pg.clock), base_url="http://127.0.0.1:8100"
        ) as public,
    ):
        assert login(public, "admin", "ADMIN").status_code == 401
        assert login(admin, "student-0", "STUDENT").status_code == 401
        ah = headers(login(admin, "admin", "ADMIN"))
        ph = headers(login(public, "student-0", "STUDENT"))
        assert admin.cookies.get("eduflow_admin_refresh")
        assert public.cookies.get("eduflow_public_refresh")
        assert admin.get("/api/v1/auth/me", headers=ph).status_code == 401
        assert public.get("/api/v1/auth/me", headers=ah).status_code == 401
        assert public.get("/api/v1/admin/users", headers=ph).status_code == 404
        assert (
            public.post(
                "/api/v1/admin/schedule/solve", headers=ah, json={"week": str(pg.snapshot.week)}
            ).status_code
            == 404
        )
        schema = public.get("/openapi.json").json()
        assert all(not path.startswith("/api/v1/admin") for path in schema["paths"])
        assert public.get("/api/v1/students/student-1/schedule", headers=ph).status_code == 403
        assert "password_hash" not in public.get("/api/v1/profile", headers=ph).text


def test_origin_host_csrf_forwarded_headers_and_refresh_replay(pg):
    with TestClient(
        create_app(configured(pg, "PUBLIC"), pg.clock), base_url="http://127.0.0.1:8100"
    ) as client:
        assert (
            client.get(
                "/health/live", headers={"Host": "attacker.invalid", "X-Forwarded-Host": "localhost"}
            ).status_code
            == 400
        )
        assert login(client, "student-0", "STUDENT").status_code == 200
        assert client.post("/api/v1/auth/refresh").status_code == 403
        assert (
            client.post(
                "/api/v1/auth/refresh",
                headers={"Origin": "http://127.0.0.1:8000", "X-Forwarded-For": "127.0.0.1"},
            ).status_code
            == 403
        )
        old = client.cookies.get("eduflow_public_refresh")
        assert client.post("/api/v1/auth/refresh", headers={"X-EduFlow-Request": "1"}).status_code == 200
        client.cookies.set("eduflow_public_refresh", old, domain="127.0.0.1", path="/api/v1/auth")
        assert client.post("/api/v1/auth/refresh", headers={"X-EduFlow-Request": "1"}).status_code == 401


def test_https_cookie_and_rate_limits(pg):
    with TestClient(
        create_app(configured(pg, "PUBLIC", cookie_secure=True, login_limit=2), pg.clock),
        base_url="https://127.0.0.1:8100",
    ) as client:
        response = login(client, "student-0", "STUDENT")
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert all(value in cookie for value in ["Secure", "HttpOnly", "SameSite=strict"])
        assert login(client, "student-0", "STUDENT", "incorrect").status_code == 401
        assert login(client, "student-0", "STUDENT", "incorrect").status_code == 429


def test_invitation_is_idempotent_private_and_single_use(pg):
    with (
        TestClient(create_app(configured(pg, "ADMIN"), pg.clock), base_url="http://127.0.0.1:8000") as admin,
        TestClient(
            create_app(configured(pg, "PUBLIC"), pg.clock), base_url="http://127.0.0.1:8100"
        ) as public,
    ):
        ah = headers(login(admin, "admin", "ADMIN"))
        payload = {
            "username": "invited.student",
            "name": "Test Student",
            "role": "STUDENT",
            "age": 9,
            "grade": 3,
        }
        first = admin.post("/api/v1/admin/invitations", headers=ah, json=payload)
        assert first.status_code == 201, first.text
        second = admin.post("/api/v1/admin/invitations", headers=ah, json=payload)
        assert second.json() == first.json()
        assert (
            admin.post(
                "/api/v1/admin/invitations", headers=ah, json={**payload, "name": "changed"}
            ).status_code
            == 409
        )
        credential = first.json()
        body = {"token": credential["token"], "purpose": "INVITE", "password": NEW_PASSWORD}
        assert (
            admin.post("/api/v1/auth/accept", headers={"X-EduFlow-Request": "1"}, json=body).status_code
            == 422
        )
        assert login(public, "invited.student", "STUDENT", NEW_PASSWORD).status_code == 401
        with Session(pg.engine) as session:
            token = session.scalar(select(AccountToken).where(AccountToken.user_id == credential["user_id"]))
            assert token.token_hash != credential["token"]
            assert len(token.token_hash) == 64
        assert (
            public.post("/api/v1/auth/accept", headers={"X-EduFlow-Request": "1"}, json=body).status_code
            == 200
        )
        assert (
            public.post("/api/v1/auth/accept", headers={"X-EduFlow-Request": "1"}, json=body).status_code
            == 422
        )
        ph = headers(login(public, "invited.student", "STUDENT", NEW_PASSWORD))
        assert public.get("/api/v1/profile", headers=ph).json()["profile"]["grade"] == 3


def test_reset_change_and_revoke_invalidate_existing_sessions(pg):
    with (
        TestClient(create_app(configured(pg, "ADMIN"), pg.clock), base_url="http://127.0.0.1:8000") as admin,
        TestClient(
            create_app(configured(pg, "PUBLIC"), pg.clock), base_url="http://127.0.0.1:8100"
        ) as public,
    ):
        ah = headers(login(admin, "admin", "ADMIN"))
        ph = headers(login(public, "student-0", "STUDENT"))
        response = admin.post(
            "/api/v1/admin/users/student-0/account-token", headers=ah, json={"purpose": "RESET"}
        )
        assert response.status_code == 200
        token = response.json()["token"]
        assert (
            public.post(
                "/api/v1/auth/accept",
                headers={"X-EduFlow-Request": "1"},
                json={"token": token, "purpose": "RESET", "password": NEW_PASSWORD},
            ).status_code
            == 200
        )
        assert public.get("/api/v1/auth/me", headers=ph).status_code == 401
        ph = headers(login(public, "student-0", "STUDENT", NEW_PASSWORD))
        assert (
            public.post(
                "/api/v1/auth/password",
                headers=ph,
                json={"current_password": NEW_PASSWORD, "new_password": PASSWORD},
            ).status_code
            == 200
        )
        assert public.get("/api/v1/auth/me", headers=ph).status_code == 401
        ph = headers(login(public, "student-0", "STUDENT"))
        with Session(pg.engine) as session:
            other = session.scalar(select(AuthSession).where(AuthSession.user_id == "admin"))
            other_id = other.id
        assert public.post("/api/v1/auth/sessions/" + other_id + "/revoke", headers=ph).status_code == 404
        assert (
            admin.post(
                "/api/v1/admin/users/student-0/revoke-sessions",
                headers={**ah, "Idempotency-Key": str(uuid4())},
            ).status_code
            == 200
        )
        assert public.get("/api/v1/auth/me", headers=ph).status_code == 401


def test_expired_invitation_and_blank_initialization(pg):
    auth = pg.platform.auth
    credential = auth.invite_user(
        pg.admin, {"username": "expired.invite", "name": "Fixture", "role": "TEACHER"}
    )
    pg.clock.current += timedelta(hours=25)
    with pytest.raises(Exception, match="过期"):
        auth.accept_account_token(credential["token"], PASSWORD, "INVITE")
    with Session(pg.engine) as session, session.begin():
        assert initialize_blank(session, "blank-fixture", "Blank School", "first.admin", PASSWORD)
        assert not initialize_blank(session, "blank-fixture", "Unchanged", "another.admin", PASSWORD)
    with Session(pg.engine) as session:
        org = session.scalar(select(Organization).where(Organization.slug == "blank-fixture"))
        users = list(session.scalars(select(User).where(User.organization_id == org.id)))
        assert len(users) == 1 and users[0].role == "ADMIN" and not org.seeded


def test_database_pagination_count_and_search(pg):
    with pg.platform.uow(pg.admin.organization_id) as transaction:
        result = transaction.records.page("student_profiles", {}, 2, 5)
        assert result["total"] == 21 and len(result["items"]) == 5
        found = transaction.records.page("student_profiles", {}, 1, 20, "student-20")
        assert found["total"] == 1 and found["items"][0]["user_id"] == "student-20"


@pytest.mark.parametrize(
    "path",
    [
        "/.env",
        "/backend/src/config.py",
        "/.runtime/private/key",
        "/..%2fsecret",
        "/%2e%2e%5csecret",
        "/docs/RESUME.md",
    ],
)
def test_private_paths_are_never_delivered(pg, tmp_path, path):
    distribution = tmp_path / "frontend"
    (distribution / "assets").mkdir(parents=True)
    (distribution / "index.html").write_text("<html>local frontend</html>")
    (tmp_path / "secret").write_text("private-sentinel-value")
    with TestClient(
        create_app(configured(pg, "PUBLIC", frontend_dir=distribution), pg.clock),
        base_url="http://127.0.0.1:8100",
    ) as client:
        response = client.get(path)
        assert response.status_code == 404
        assert "private-sentinel-value" not in response.text
        assert client.get("/login").status_code == 200


def test_public_exposure_and_mixed_application_are_fail_closed():
    with pytest.raises(ValueError, match="Public exposure"):
        Settings(
            _env_file=None, jwt_secret="test-only-long-enough-private-secret", public_access_enabled=True
        )
    with pytest.raises(ValueError, match="test-only"):
        Settings(
            _env_file=None,
            jwt_secret="test-only-long-enough-private-secret",
            portal="TEST",
            database_url="postgresql+psycopg://test:test@localhost/application",
        )


def test_chunked_body_is_bounded_and_errors_have_stable_identifiers(pg):
    with TestClient(
        create_app(configured(pg, "PUBLIC"), pg.clock), base_url="http://127.0.0.1:8100"
    ) as client:
        response = client.post(
            "/api/v1/auth/login",
            content=iter([b"x" * 600_000, b"y" * 600_000]),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["code"] == "BODY_TOO_LARGE"
        assert response.json()["request_id"]
        rejected = client.get("/not-a-business-route")
        assert rejected.status_code == 404
        assert rejected.json()["code"] == "NOT_FOUND" and rejected.json()["request_id"]


def test_assets_cannot_escape_through_a_directory_link(pg, tmp_path):
    distribution = tmp_path / "frontend"
    distribution.mkdir()
    (distribution / "index.html").write_text("<html>frontend</html>")
    private = tmp_path / "private"
    private.mkdir()
    (private / "secret-ABCDEF.js").write_text("private-link-sentinel")
    assets = distribution / "assets"
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(assets), str(private)], capture_output=True, check=False
        )
        assert result.returncode == 0, "Test directory junction could not be created"
    else:
        assets.symlink_to(private, target_is_directory=True)
    with TestClient(
        create_app(configured(pg, "PUBLIC", frontend_dir=distribution), pg.clock),
        base_url="http://127.0.0.1:8100",
    ) as client:
        response = client.get("/assets/secret-ABCDEF.js")
        assert response.status_code == 404
        assert "private-link-sentinel" not in response.text
        assert client.get("/login").status_code == 200


def test_blank_login_configuration_and_account_deactivation(pg):
    with (
        TestClient(
            create_app(configured(pg, "ADMIN", initialization_mode="blank"), pg.clock),
            base_url="http://127.0.0.1:8000",
        ) as admin,
        TestClient(
            create_app(configured(pg, "PUBLIC", initialization_mode="blank"), pg.clock),
            base_url="http://127.0.0.1:8100",
        ) as public,
    ):
        assert public.get("/api/v1/auth/config").json() == {
            "portal": "PUBLIC",
            "demo_mode": False,
            "public_access_enabled": False,
        }
        ph = headers(login(public, "student-0", "STUDENT"))
        ah = headers(login(admin, "admin", "ADMIN"))
        with Session(pg.engine) as session:
            user = session.get(User, "student-0")
            version = user.version
        response = admin.post(
            "/api/v1/admin/users",
            headers=ah,
            json={
                "id": "student-0",
                "version": version,
                "username": "student-0",
                "name": "student-0",
                "role": "STUDENT",
                "active": False,
            },
        )
        assert response.status_code == 200
        assert public.get("/api/v1/auth/me", headers=ph).status_code == 401
        assert login(public, "student-0", "STUDENT").status_code == 401


def test_personal_response_does_not_expose_internal_crm_notes(pg):
    with Session(pg.engine) as session, session.begin():
        profile = session.scalar(select(StudentProfile).where(StudentProfile.user_id == "student-0"))
        profile.notes = "Internal CRM fixture note"
    with TestClient(
        create_app(configured(pg, "PUBLIC"), pg.clock), base_url="http://127.0.0.1:8100"
    ) as client:
        ph = headers(login(client, "student-0", "STUDENT"))
        response = client.get("/api/v1/profile", headers=ph)
        assert response.status_code == 200
        assert "Internal CRM fixture note" not in response.text
        assert set(response.json()["user"]) <= {
            "id",
            "organization_id",
            "version",
            "username",
            "name",
            "role",
            "phone",
            "active",
        }
        assert "password_hash" not in response.text
        assert set(response.json()["profile"]) <= {
            "id",
            "user_id",
            "version",
            "student_code",
            "gender",
            "age",
            "grade",
        }
