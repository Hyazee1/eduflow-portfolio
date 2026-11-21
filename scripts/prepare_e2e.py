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
    env = {
        "DATABASE_URL": target.set(host="postgres").render_as_string(hide_password=False),
        "REDIS_URL": "redis://redis:6379/8",
        "JWT_SECRET": secret,
    }
    (runtime / "private/e2e.env").write_text(
        "\n".join(key + "=" + value for key, value in env.items()) + "\n", encoding="utf-8"
    )
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
        + "\nREDIS_URL=redis://127.0.0.1:6379/8\nJWT_SECRET="
        + secret
        + "\n",
        encoding="utf-8",
    )
    services = {}
    for name, image, action in (
        ("worker", "eduflow-local:privacy", "worker"),
        ("beat", "eduflow-local:privacy", "beat"),
    ):
        services[name] = {
            "image": image,
            "env_file": [str(runtime / "private/e2e.env")],
            "command": [
                "celery",
                "-A",
                "eduflow.bootstrap.worker:celery_app",
                action,
                "--loglevel=WARNING",
                *(["--concurrency=2"] if action == "worker" else ["--schedule=/tmp/celerybeat-schedule"]),
            ],
            "volumes": [
                {
                    "type": "bind",
                    "source": str(ROOT / "backend/src"),
                    "target": "/app/backend/src",
                    "read_only": True,
                }
            ],
        }
    compose = {
        "name": "eduflow-e2e",
        "services": services,
        "networks": {"default": {"external": True, "name": "eduflow-ai_default"}},
    }
    (runtime / "e2e-compose.json").write_text(json.dumps(compose, indent=2), encoding="utf-8")
    print("Isolated E2E database and private runtime config prepared; week=" + str(week))


if __name__ == "__main__":
    main()
