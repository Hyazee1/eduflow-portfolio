"""Prepare per-service local secrets without exposing values in command lines."""
# ruff: noqa: E402 -- standalone script bootstraps the local source path.

import base64
import hashlib
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from dotenv import dotenv_values
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from eduflow.bootstrap.settings import Settings


def protect_local_files() -> None:
    private = ROOT / ".runtime/private"
    if os.name == "nt":
        subprocess.run(
            [
                shutil.which("pwsh") or "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(ROOT / "scripts/protect_config.ps1"),
            ],
            check=True,
        )
    else:
        private.mkdir(parents=True, exist_ok=True, mode=0o700)
        private.chmod(0o700)
        if (ROOT / ".env").is_file():
            (ROOT / ".env").chmod(0o600)


def prepare_runtime(files_only: bool = False) -> None:
    protect_local_files()
    settings = Settings()
    private = ROOT / ".runtime/private"
    directory = private / "secrets"
    directory.mkdir(exist_ok=True, mode=0o700)
    (private / "backups").mkdir(exist_ok=True, mode=0o700)

    def keep(name: str, value: str) -> str:
        target = directory / name
        try:
            with target.open("x", encoding="utf-8") as stream:
                stream.write(value)
            target.chmod(0o600)
        except FileExistsError:
            pass
        return target.read_text(encoding="utf-8").strip()

    keep("admin.key", secrets.token_hex(48))
    keep("public.key", secrets.token_hex(48))
    keep("backup.key", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    application_password = keep("application.password", secrets.token_urlsafe(36))
    password = keep("redis.password", secrets.token_hex(32))
    health_password = keep("redis-health.password", secrets.token_hex(32))
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    health_hash = hashlib.sha256(health_password.encode()).hexdigest()
    port = int(
        os.getenv("EDUFLOW_REDIS_PORT") or dotenv_values(ROOT / ".env").get("EDUFLOW_REDIS_PORT") or 6379
    )
    if not 1 <= port <= 65535:
        raise ValueError("EDUFLOW_REDIS_PORT must be between 1 and 65535")
    # Celery uses lists, sorted sets, pub/sub and Lua locks in its dedicated Redis instance.
    acl = (
        "user default off resetpass -@all\n"
        f"user health on #{health_hash} -@all +ping\n"
        f"user eduflow on #{password_hash} ~* &* -@all +@read +@write +@transaction +@connection "
        "+@pubsub +@scripting -@dangerous +info +client|setname +client|setinfo\n"
    )
    for name, value in {
        "redis.acl": acl,
        "redis.url": f"redis://eduflow:{password}@redis:6379/0",
        "redis.host.url": f"redis://eduflow:{password}@127.0.0.1:{port}/0",
    }.items():
        target = directory / name
        if not target.exists() or target.read_text(encoding="utf-8") != value:
            target.write_bytes(value.encode())
            target.chmod(0o600)
    source = make_url(settings.database_url)
    keep("postgres.password", source.password or "")
    if files_only:
        print("Private files prepared.")
        return
    app_role = "eduflow_local_app"
    engine = create_engine(source)
    try:
        with engine.begin() as connection:
            exists = connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname=:name"), {"name": app_role})
            query = (
                sql.SQL("ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD {}")
                if exists
                else sql.SQL(
                    "CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD {}"
                )
            )
            connection.exec_driver_sql(
                query.format(sql.Identifier(app_role), sql.Literal(application_password)).as_string()
            )
            connection.exec_driver_sql(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}")
                .format(
                    sql.Identifier(source.database or "eduflow_ai"),
                    sql.Identifier(app_role),
                )
                .as_string()
            )
            connection.exec_driver_sql(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(app_role)).as_string()
            )
            connection.exec_driver_sql(
                sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO {}")
                .format(sql.Identifier(app_role))
                .as_string()
            )
            connection.exec_driver_sql(
                sql.SQL("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}")
                .format(sql.Identifier(app_role))
                .as_string()
            )
    except Exception:
        raise RuntimeError(
            "Local application role preparation failed; private credentials suppressed"
        ) from None
    finally:
        engine.dispose()
    container = source.set(host="postgres", port=5432)
    for name, url in [
        (
            "application.url",
            container.set(username=app_role, password=application_password),
        ),
        ("maintenance.url", container),
    ]:
        target = directory / name
        target.write_text(url.render_as_string(hide_password=False), encoding="utf-8")
        target.chmod(0o600)
    print("Per-service private configuration prepared; public exposure remains disabled.")


if __name__ == "__main__":
    prepare_runtime("--files-only" in sys.argv)
