"""Prepare per-service local secrets without exposing values in command lines."""
# ruff: noqa: E402 -- standalone script bootstraps the local source path.

import base64
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from eduflow.bootstrap.settings import Settings


def prepare_runtime(files_only: bool = False) -> None:
    settings = Settings()
    private = ROOT / ".runtime/private"
    directory = private / "secrets"
    directory.mkdir(parents=True, exist_ok=True)
    (private / "backups").mkdir(exist_ok=True)
    if os.name == "nt":
        result = subprocess.run(
            [
                "icacls",
                str(private),
                "/inheritance:r",
                "/grant:r",
                os.environ["USERNAME"] + ":(OI)(CI)F",
                "SYSTEM:(OI)(CI)F",
            ],
            capture_output=True,
        )
        if result.returncode:
            raise RuntimeError("Unable to apply private-directory permissions")
    else:
        private.chmod(0o700)
        directory.chmod(0o700)

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
