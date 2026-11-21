"""Host-side pre-migration backup, keeping Docker control out of application containers."""

# ruff: noqa: E402
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from sqlalchemy.engine import make_url

from eduflow.adapters.backup import encrypt_dump
from eduflow.bootstrap.settings import Settings


def main() -> None:
    settings = Settings()
    source = make_url(settings.database_url)
    command = [
        sys.argv[1],
        "compose",
        "exec",
        "-T",
        "postgres",
        "pg_dump",
        "-U",
        source.username or "eduflow",
        "-d",
        source.database or "eduflow_ai",
        "--format=custom",
        "--no-owner",
        "--no-acl",
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=180, check=False)
    if result.returncode:
        raise RuntimeError("Pre-migration backup failed; database was not modified")
    private = ROOT / ".runtime/private"
    metadata = encrypt_dump(result.stdout, private / "backups", private / "secrets/backup.key")
    print("Encrypted pre-migration backup verified: " + str(metadata["file"]))


if __name__ == "__main__":
    main()
