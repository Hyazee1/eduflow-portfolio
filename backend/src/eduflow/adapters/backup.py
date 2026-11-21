"""Local authenticated encryption around the version-matched PostgreSQL tools."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def _environment(url: str) -> tuple[list[str], dict[str, str]]:
    target = make_url(url)
    env = dict(os.environ)
    env["PGPASSWORD"] = target.password or ""
    env["LD_LIBRARY_PATH"] = "/opt/pg-libs"
    return [
        "-h",
        target.host or "127.0.0.1",
        "-p",
        str(target.port or 5432),
        "-U",
        target.username or "eduflow",
        "-d",
        target.database or "eduflow_ai",
    ], env


def _run(binary: str, args: list[str], env: dict[str, str], **kwargs: Any) -> bytes:
    executable = shutil.which(binary)
    if not executable:
        raise RuntimeError("PostgreSQL tools are supplied in the application container")
    result = subprocess.run([executable, *args], env=env, capture_output=True, timeout=300, **kwargs)
    if result.returncode:
        raise RuntimeError("PostgreSQL maintenance command failed; credential details suppressed")
    return result.stdout


def backup_database(
    url: str, directory: Path, key_file: Path, *, purpose: str = "manual"
) -> dict[str, str | int]:
    args, env = _environment(url)
    dump = _run("pg_dump", [*args, "--format=custom", "--no-owner", "--no-acl"], env)
    if not dump.startswith(b"PGDMP"):
        raise RuntimeError("PostgreSQL backup format verification failed")
    return encrypt_dump(dump, directory, key_file, purpose=purpose)


def encrypt_dump(
    dump: bytes, directory: Path, key_file: Path, *, purpose: str = "manual"
) -> dict[str, str | int]:
    directory.mkdir(parents=True, exist_ok=True)
    cipher = Fernet(key_file.read_bytes().strip())
    name = "eduflow-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".dump.enc"
    target = directory / name
    data = cipher.encrypt(dump)
    with target.open("xb") as stream:
        stream.write(data)
    target.chmod(0o600)
    result: dict[str, str | int] = {
        "file": name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "created_at": datetime.now(UTC).isoformat(),
        "purpose": purpose,
    }
    target.with_suffix(".json").write_text(json.dumps(result) + "\n", encoding="utf-8")
    if Fernet(key_file.read_bytes().strip()).decrypt(target.read_bytes()) != dump:
        raise RuntimeError("Encrypted backup verification failed")
    if purpose == "daily":
        prune_daily_backups(directory, key_file, target)
    return result


def verify_archive(archive: Path, key_file: Path) -> dict[str, Any]:
    metadata = json.loads(archive.with_suffix(".json").read_text(encoding="utf-8"))
    data = archive.read_bytes()
    if metadata["file"] != archive.name or metadata["sha256"] != hashlib.sha256(data).hexdigest():
        raise RuntimeError("Backup checksum mismatch")
    Fernet(key_file.read_bytes().strip()).decrypt(data)
    return dict(metadata)


def backup_due(directory: Path, key_file: Path, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    for archive in sorted(directory.glob("eduflow-*.dump.enc"), reverse=True):
        try:
            verify_archive(archive, key_file)
            return now.timestamp() - archive.stat().st_mtime >= 86400
        except (OSError, ValueError, KeyError, RuntimeError, InvalidToken):
            continue
    return True


def prune_daily_backups(directory: Path, key_file: Path, newest: Path, keep: int = 7) -> list[str]:
    # Verify the new recovery point before removing any older daily archive.
    verify_archive(newest, key_file)
    root = directory.resolve()
    protected_file = directory / "protected.json"
    protected = (
        set(json.loads(protected_file.read_text(encoding="utf-8")).values())
        if protected_file.exists()
        else set()
    )
    daily = []
    for archive in sorted(directory.glob("eduflow-*.dump.enc"), reverse=True):
        if archive.is_symlink() or not archive.resolve().is_relative_to(root):
            raise RuntimeError("Backup retention refuses linked or external files")
        metadata = json.loads(archive.with_suffix(".json").read_text(encoding="utf-8"))
        if metadata.get("purpose") == "daily" and archive.name not in protected:
            daily.append(archive)
    removed = []
    for archive in daily[max(1, keep) :]:
        archive.unlink()
        archive.with_suffix(".json").unlink()
        removed.append(archive.name)
    return removed


def restore_database(url: str, archive: Path, key_file: Path, target_name: str) -> dict[str, str]:
    source = make_url(url)
    if (
        not re.fullmatch(r"[a-z][a-z0-9_]{1,55}(?:_test|_restored)", target_name)
        or target_name == source.database
    ):
        raise RuntimeError(
            "Restore requires a NEW *_test or *_restored database, never the application database"
        )
    plaintext = Fernet(key_file.read_bytes().strip()).decrypt(archive.read_bytes())
    engine = create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            if connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": target_name}
            ):
                raise RuntimeError("Restore refuses to overwrite an existing database")
            name = connection.dialect.identifier_preparer.quote_identifier(target_name)
            connection.exec_driver_sql("CREATE DATABASE " + name)
    finally:
        engine.dispose()
    args, env = _environment(source.set(database=target_name).render_as_string(hide_password=False))
    with tempfile.TemporaryDirectory(prefix="eduflow-restore-") as temporary:
        dump = Path(temporary) / "restore.dump"
        dump.write_bytes(plaintext)
        dump.chmod(0o600)
        _run(
            "pg_restore",
            [*args, "--no-owner", "--no-acl", "--exit-on-error", "--single-transaction", str(dump)],
            env,
        )
    return {"database": target_name, "status": "RESTORED"}
