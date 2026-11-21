"""Optimize and finalize only the explicitly marked local demonstration institution via normal HTTP APIs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

import httpx  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from eduflow.bootstrap.settings import Settings  # noqa: E402


def save(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows indexers can briefly hold a read handle without FILE_SHARE_DELETE.
    for attempt in range(8):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(min(1, 0.1 * 2**attempt))


def request(client: httpx.Client, method: str, route: str, **kwargs: Any) -> dict[str, Any]:
    for attempt in range(4):
        try:
            response = client.request(method, route, **kwargs)
            if response.status_code in {429, 502, 503, 504} and attempt < 3:
                time.sleep(min(30, 2**attempt))
                continue
            if response.is_error:
                error = response.json()
                raise RuntimeError(
                    f"HTTP {response.status_code}: {error.get('message', error.get('detail', '业务请求失败'))}"
                )
            return dict(response.json())
        except httpx.TransportError:
            if attempt == 3:
                raise RuntimeError("Local API transport failed; rerun with the same run id") from None
            time.sleep(2**attempt)
    raise RuntimeError("Retry limit reached")


def login(client: httpx.Client, username: str, role: str, password: str) -> None:
    data = request(
        client, "POST", "/api/v1/auth/login", json={"username": username, "role": role, "password": password}
    )
    client.headers["Authorization"] = "Bearer " + data["access_token"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-version", required=True)
    parser.add_argument("--run-id", default="release-20261108")
    parser.add_argument("--admin-port", type=int, choices=[8000, 8001], default=8000)
    parser.add_argument("--public-port", type=int, choices=[8100, 8101], default=8100)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.run_id.replace("-", "").isalnum():
        raise RuntimeError("Run id must be an alphanumeric label")
    settings = Settings()
    engine = create_engine(
        settings.database_url, connect_args={"options": "-c default_transaction_read_only=on"}
    )
    try:
        with engine.connect() as connection:
            scope = (
                connection.execute(
                    text("""
                SELECT o.slug,o.seeded,v.week,current_database() AS database,
                  MIN((s.day+s.start_time::time) AT TIME ZONE 'Asia/Shanghai')>now() AS all_future
                FROM schedule_versions v JOIN organizations o ON o.id=v.organization_id
                JOIN course_sessions s ON s.version_id=v.id
                WHERE v.id=:id GROUP BY o.slug,o.seeded,v.week
            """),
                    {"id": args.reference_version},
                )
                .mappings()
                .one()
            )
            if scope["slug"] != "demo" or not scope["seeded"] or not scope["all_future"]:
                raise RuntimeError("Requires a seeded demo institution and future reference lessons")
            if scope["database"] != "eduflow_ai" and not scope["database"].endswith("_test"):
                raise RuntimeError("Database is outside the explicit local demonstration allowlist")
            connection.rollback()
    finally:
        engine.dispose()
    if not args.apply:
        print(json.dumps({"dry_run": True, "week": str(scope["week"]), "database": scope["database"]}))
        return
    directory = ROOT / ".runtime/private/release-20261108"
    directory.mkdir(exist_ok=True)
    state_path = directory / (args.run_id + ".json")
    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists()
        else {
            "run_id": args.run_id,
            "reference_version_id": args.reference_version,
            "database": scope["database"],
            "started_at": datetime.now(UTC).isoformat(),
            "simulation": True,
            "confirmed_accounts": [],
        }
    )
    if state["reference_version_id"] != args.reference_version or state["database"] != scope["database"]:
        raise RuntimeError("Run manifest does not match the requested scope")

    def key(operation: str) -> dict[str, str]:
        return {"Idempotency-Key": hashlib.sha256((args.run_id + ":" + operation).encode()).hexdigest()}

    common = {"X-EduFlow-Request": "1"}
    with httpx.Client(base_url=f"http://127.0.0.1:{args.admin_port}", headers=common, timeout=90) as admin:
        if not request(admin, "GET", "/api/v1/auth/config")["demo_mode"]:
            raise RuntimeError("Admin endpoint is not in demonstration mode")
        login(admin, "admin", "ADMIN", settings.demo_password)
        reference = request(admin, "GET", "/api/v1/admin/schedule/versions/" + args.reference_version)
        if not state.get("job_id"):
            job = request(
                admin,
                "POST",
                "/api/v1/admin/schedule/solve",
                json={
                    "week": reference["week"],
                    "optimization_profile": "preference_first",
                    "reference_version_id": args.reference_version,
                },
                headers=key("solve"),
            )
            state["job_id"] = job["id"]
            save(state_path, state)
        stop = time.monotonic() + 480
        last_status = ""
        while True:
            job = request(admin, "GET", "/api/v1/admin/schedule/jobs/" + state["job_id"])
            if job["status"] != last_status:
                print(json.dumps({"stage": "solve", "status": job["status"]}), flush=True)
                last_status = job["status"]
            if job["status"] not in {"QUEUED", "RUNNING"}:
                break
            if time.monotonic() >= stop:
                raise RuntimeError("Solver still running; resume this run later")
            time.sleep(2)
        if job["status"] != "COMPLETED" or not job.get("version_id"):
            raise RuntimeError("Solver did not produce a current feasible candidate")
        version_id = state["version_id"] = job["version_id"]
        save(state_path, state)
        endpoint = "/api/v1/admin/schedule/versions/" + version_id
        version = request(admin, "GET", endpoint)
        if version["status"] == "DRAFT":
            request(
                admin,
                "POST",
                endpoint + "/validate",
                json={"version": version["version"]},
                headers=key("validate"),
            )
            version = request(admin, "GET", endpoint)
        metrics = version["report"]["metrics"]
        if (
            version["report"]["hard_conflicts"]
            or metrics["fulfilled_count"] != 668
            or metrics["request_count"] != 668
            or metrics["covered_slots"] != 40
        ):
            raise RuntimeError("Candidate failed the explicit 668/668 and 40/40 quality gate")
        if metrics["first_preferred_count"] < 106 or metrics["preferred_count"] < 209:
            raise RuntimeError("Preference metrics regressed")
        if version["status"] == "VALIDATED":
            previous = request(admin, "GET", "/api/v1/admin/schedule/versions/" + args.reference_version)
            payload = {"version": version["version"]}
            if previous["status"] == "PUBLISHED_PENDING_CONFIRMATION":
                payload.update(
                    replace_expired_version_id=previous["id"],
                    replace_expired_version_revision=previous["version"],
                )
            request(admin, "POST", endpoint + "/publish", json=payload, headers=key("publish"))
            version = request(admin, "GET", endpoint)
            state["published_at"] = datetime.now(UTC).isoformat()
            save(state_path, state)
        users = {u["id"]: u for u in request(admin, "GET", "/api/v1/admin/users?page_size=500")["items"]}
        pending: dict[str, list[str]] = defaultdict(list)
        for confirmation in version["confirmations"]:
            if confirmation["decision"] == "PENDING":
                pending[confirmation["user_id"]].append(confirmation["logical_id"])
        for index, (user_id, logical_ids) in enumerate(pending.items(), 1):
            user = users[user_id]
            expected = (
                "student" if user["role"] == "STUDENT" else "teacher" if user["role"] == "TEACHER" else ""
            )
            if (
                not expected
                or not user["username"].startswith(expected)
                or not user["username"][len(expected) :].isdigit()
            ):
                raise RuntimeError("Refusing to simulate a non-seeded account")
            with httpx.Client(
                base_url=f"http://127.0.0.1:{args.public_port}", headers=common, timeout=90
            ) as person:
                login(person, user["username"], user["role"], settings.demo_password)
                try:
                    request(
                        person,
                        "POST",
                        "/api/v1/schedule-confirmations",
                        json={
                            "version_id": version_id,
                            "session_ids": logical_ids,
                            "decision": "ACCEPTED",
                            "reason": "DEMO_SIMULATION:" + args.run_id,
                        },
                        headers=key("confirm:" + user_id),
                    )
                finally:
                    request(person, "POST", "/api/v1/auth/logout")
            if user_id not in state["confirmed_accounts"]:
                state["confirmed_accounts"].append(user_id)
            save(state_path, state)
            if index % 25 == 0:
                print(
                    json.dumps(
                        {
                            "stage": "simulate_confirmations",
                            "accounts": index,
                            "pending_accounts_at_start": len(pending),
                        }
                    ),
                    flush=True,
                )
        final = request(admin, "GET", endpoint)
        if (
            final["status"] != "ACTIVE"
            or not final["confirmations"]
            or any(c["decision"] != "ACCEPTED" for c in final["confirmations"])
        ):
            raise RuntimeError("Final version is not ACTIVE with complete confirmations")
        state.update(
            status="ACTIVE",
            completed_at=datetime.now(UTC).isoformat(),
            metrics=final["report"]["metrics"],
            optimization=final["report"].get("optimization"),
            confirmation_tasks=len(final["confirmations"]),
        )
        save(state_path, state)
        request(admin, "POST", "/api/v1/auth/logout")
        print(
            json.dumps(
                {
                    "status": "ACTIVE",
                    "version_id": version_id,
                    "metrics": state["metrics"],
                    "confirmation_tasks": state["confirmation_tasks"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
