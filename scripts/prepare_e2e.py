"""Prepare a separately guarded 220-student E2E database and private runtime config."""
# ruff: noqa: E402 -- project imports intentionally follow the standalone path bootstrap.

from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from eduflow.adapters.demo import ORG_ID, next_week, seed_demo
from eduflow.adapters.demo_week import create_full_day_demo
from eduflow.adapters.models import Base
from eduflow.bootstrap.settings import Settings
from eduflow.domain.time_rules import SHANGHAI
from prepare_test_database import main as create_test_database


def main() -> None:
    if "--reset" not in sys.argv:
        raise RuntimeError("Use --reset to explicitly initialize the isolated eduflow_ai_e2e_test database.")
    settings = Settings()
    target = make_url(settings.database_url).set(database="eduflow_ai_e2e_test")
    if target.database == make_url(settings.database_url).database:
        raise RuntimeError("Refusing to reset application database")
    os.environ["EDUFLOW_TEST_DATABASE_URL"] = target.render_as_string(hide_password=False)
    create_test_database()
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.attributes["database_url"] = target.render_as_string(hide_password=False)
    command.upgrade(config, "head")
    engine = create_engine(target)
    today = datetime.now(SHANGHAI).date()
    week = next_week(today)
    with engine.begin() as connection:
        tables = ", ".join(
            connection.dialect.identifier_preparer.quote(name) for name in Base.metadata.tables
        )
        connection.exec_driver_sql("TRUNCATE " + tables + " RESTART IDENTITY CASCADE")
    with Session(engine) as session, session.begin():
        seed_demo(session, week, PasswordHasher().hash(settings.demo_password))
        full_day = create_full_day_demo(session, ORG_ID, source_week=week)
    engine.dispose()
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    existing_api_config = runtime / "private/e2e-api.env"
    secret = (
        Settings(_env_file=existing_api_config).jwt_secret
        if existing_api_config.is_file()
        else secrets.token_urlsafe(48)
    )
    initial_clock = datetime.combine(today, datetime.min.time(), SHANGHAI).isoformat()
    (runtime / "e2e-clock.json").write_text(json.dumps({"now": initial_clock}), encoding="utf-8")
    private = runtime / "private"
    deployment = {**dotenv_values(ROOT / ".env"), **os.environ}
    redis = make_url((private / "secrets/redis.url").read_text(encoding="utf-8").strip()).set(database="8")
    host_redis = make_url((private / "secrets/redis.host.url").read_text(encoding="utf-8").strip()).set(
        database="8"
    )
    secret_files = {
        "e2e_database": target.set(host="postgres", port=5432).render_as_string(hide_password=False),
        "e2e_redis": redis.render_as_string(hide_password=False),
    }
    for name, value in secret_files.items():
        (private / name).write_text(value, encoding="utf-8")
        (private / name).chmod(0o600)
    info = {
        "week": str(week),
        "date": str(today),
        "clock": initial_clock,
        "database": target.database,
        "model_mode": "offline",
    }
    (runtime / "e2e-info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    (runtime / "full-day-e2e-info.json").write_text(json.dumps(full_day, indent=2), encoding="utf-8")
    (runtime / "private/e2e-api.env").write_text(
        "DATABASE_URL="
        + target.render_as_string(hide_password=False)
        + "\nREDIS_URL="
        + host_redis.render_as_string(hide_password=False)
        + "\nJWT_SECRET="
        + secret
        + "\n",
        encoding="utf-8",
    )
    services = {}
    for name in ("worker", "beat"):
        services[name] = {
            "image": deployment.get("EDUFLOW_APP_IMAGE") or "eduflow-local:privacy",
            "environment": {
                "PORTAL": "WORKER",
                "DATABASE_URL_FILE": "/run/secrets/e2e_database",
                "REDIS_URL_FILE": "/run/secrets/e2e_redis",
            },
            "secrets": list(secret_files),
            "command": [
                "celery",
                "-A",
                "eduflow.bootstrap.worker:celery_app",
                name,
                "--loglevel=WARNING",
                *(["--concurrency=2"] if name == "worker" else ["--schedule=/tmp/celerybeat-schedule"]),
            ],
            "read_only": True,
            "tmpfs": ["/tmp"],
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
        }
    compose = {
        "name": deployment.get("EDUFLOW_E2E_PROJECT") or "eduflow-e2e",
        "services": services,
        "secrets": {k: {"file": str(private / k)} for k in secret_files},
        "networks": {
            "default": {
                "external": True,
                "name": (deployment.get("COMPOSE_PROJECT_NAME") or "eduflow-ai") + "_default",
            }
        },
    }
    (runtime / "e2e-compose.json").write_text(json.dumps(compose, indent=2), encoding="utf-8")
    print("Isolated E2E database and private runtime config prepared; week=" + str(week))


if __name__ == "__main__":
    main()
