import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

from eduflow.adapters import backup
from eduflow.adapters.security import public_details


def scanner():
    target = Path(__file__).resolve().parents[2] / "scripts/privacy_scan.py"
    spec = importlib.util.spec_from_file_location("privacy_scan", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scan_reports_locations_without_recording_secret_values(tmp_path):
    source = tmp_path / "example.py"
    secret = "fixture-only-sensitive-marker"
    source.write_text('value = "' + secret + '"', encoding="utf-8")
    result = scanner().scan(tmp_path, [source], [secret])
    assert result == [{"file": "example.py", "line": 1, "rule": "LOCAL_SECRET"}]
    assert secret not in str(result)


def test_diagnostic_details_remove_paths_and_secret_fields():
    value = {
        "password": "fixture-password",
        "input": "fixture-data",
        "message": "C:/Users/Fixture/private.txt",  # privacy: fixture
    }  # privacy: fixture
    safe = public_details(value)
    assert set(safe) == {"message"}
    assert safe["message"] == "[private-path]"


def test_scan_detects_private_portal_key_in_a_candidate_build(tmp_path):
    key = tmp_path / ".runtime/private/secrets/admin.key"
    key.parent.mkdir(parents=True)
    key.write_text("fixture-only-portal-signing-key")
    artifact = tmp_path / "bundle.js"
    artifact.write_text('const accidental = "fixture-only-portal-signing-key";')
    module = scanner()
    findings = module.scan(tmp_path, [artifact], module.private_values(tmp_path))
    assert findings == [{"file": "bundle.js", "line": 1, "rule": "LOCAL_SECRET"}]
    assert key.read_text() not in str(findings)


def test_backup_is_authenticated_and_key_stays_separate(tmp_path):
    key = tmp_path / "keys" / "backup.key"
    key.parent.mkdir()
    key.write_bytes(Fernet.generate_key())
    plain = b"fixture-postgresql-custom-dump"
    result = backup.encrypt_dump(plain, tmp_path / "archives", key)
    archive = tmp_path / "archives" / result["file"]
    assert plain not in archive.read_bytes()
    assert Fernet(key.read_bytes()).decrypt(archive.read_bytes()) == plain
    assert not list(archive.parent.glob("*.key"))
    with pytest.raises(InvalidToken):
        Fernet(Fernet.generate_key()).decrypt(archive.read_bytes())
    corrupted = bytearray(archive.read_bytes())
    corrupted[-8] ^= 1
    with pytest.raises(InvalidToken):
        Fernet(key.read_bytes()).decrypt(bytes(corrupted))


def test_daily_retention_keeps_seven_and_pinned_recovery_points(tmp_path):
    key = tmp_path / "backup.key"
    key.write_bytes(Fernet.generate_key())
    directory = tmp_path / "archives"
    pinned = backup.encrypt_dump(b"baseline", directory, key, purpose="daily")
    (directory / "protected.json").write_text(json.dumps({"before": pinned["file"]}))
    for _ in range(9):
        backup.encrypt_dump(b"daily", directory, key, purpose="daily")
    assert (directory / pinned["file"]).exists()
    assert len(list(directory.glob("*.dump.enc"))) == 8
    assert not backup.backup_due(directory, key)


def test_retention_refuses_pruning_when_new_backup_is_corrupt(tmp_path):
    key = tmp_path / "backup.key"
    key.write_bytes(Fernet.generate_key())
    directory = tmp_path / "archives"
    older = backup.encrypt_dump(b"old", directory, key)
    newest = backup.encrypt_dump(b"new", directory, key)
    target = directory / newest["file"]
    target.write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="checksum"):
        backup.prune_daily_backups(directory, key, target, keep=1)
    assert (directory / older["file"]).exists()


@pytest.mark.parametrize("target", ["eduflow_ai", "postgres", "existing", "bad;DROP DATABASE anything_test"])
def test_restore_rejects_unsafe_targets_before_running_database_commands(tmp_path, target):
    with pytest.raises(RuntimeError, match="NEW"):
        backup.restore_database(
            "postgresql+psycopg://fixture@localhost/eduflow_ai",
            tmp_path / "missing",
            tmp_path / "missing-key",
            target,
        )
