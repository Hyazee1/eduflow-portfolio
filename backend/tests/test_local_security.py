import hashlib
import json
import os
import runpy
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from eduflow.bootstrap.settings import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_launcher_accepts_tags_or_a_complete_set_of_verified_digests():
    refs = runpy.run_path(str(ROOT / "run.py"))["verified_image_refs"]
    services = {name: {"image": name + ":test"} for name in ("api", "postgres", "redis")}
    assert refs({"services": services}) == {}
    services["api"]["image"] = "app@sha256:" + "a" * 64
    with pytest.raises(RuntimeError):
        refs({"services": services})
    for name in services:
        services[name]["image"] = name + "@sha256:" + "b" * 64
    assert refs({"services": services}) == {name: svc["image"] for name, svc in services.items()}
    services["redis"]["image"] = "redis@sha256:123"
    with pytest.raises(RuntimeError):
        refs({"services": services})


def test_queue_port_and_credentials_are_scoped_to_their_consumers():
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    assert not services["redis"].get("ports")
    assert set(services["redis"]["secrets"]) == {"redis_acl", "redis_health"}
    for name in ("admin-api", "public-api", "worker", "beat"):
        assert "redis_url" in services[name]["secrets"]
        assert {"redis_acl", "redis_health"}.isdisjoint(services[name]["secrets"])
    assert {"redis_url", "redis_acl", "redis_health"}.isdisjoint(services["maintenance"]["secrets"])
    developer = yaml.safe_load((ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8"))
    assert all(port.startswith("127.0.0.1:") for port in developer["services"]["redis"]["ports"])


def test_redis_secret_file_takes_precedence_and_is_hidden(tmp_path):
    target = tmp_path / "redis.url"
    url = "redis://eduflow:fixture-only@redis:6379/0"
    target.write_text(url + "\n", encoding="utf-8")
    settings = Settings(_env_file=None, portal="WORKER", redis_url_file=target)
    assert settings.redis_url == url
    assert "fixture-only" not in repr(settings)


def test_unavailable_redis_secret_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        Settings(_env_file=None, portal="WORKER", redis_url_file=tmp_path / "absent")


@pytest.mark.skipif(os.name != "nt", reason="Windows NTFS access control")
@pytest.mark.parametrize("existing", [True, False])
def test_windows_setup_restricts_acl_without_rotating_credentials(tmp_path, existing):
    shell = shutil.which("pwsh") or "powershell"
    env_file = tmp_path / ".env"
    shutil.copyfile(ROOT / ".env.example", tmp_path / ".env.example")
    if existing:
        env_file.write_bytes(b"JWT_SECRET=test-fixture-existing-value\r\n")
        original = hashlib.sha256(env_file.read_bytes()).digest()
    protect = [
        shell,
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(ROOT / "scripts/protect_config.ps1"),
        "-ProjectRoot",
        str(tmp_path),
        "-CreateEnvironment",
    ]
    subprocess.run(protect, check=True, capture_output=True)
    digest = hashlib.sha256(env_file.read_bytes()).digest()
    if existing:
        assert digest == original
    subprocess.run(protect, check=True, capture_output=True)
    assert hashlib.sha256(env_file.read_bytes()).digest() == digest
    probe = """
    $acl = Get-Acl -LiteralPath $env:EDUFLOW_ACL_FIXTURE
    $sid = [Security.Principal.SecurityIdentifier]
    $who = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $rules = @($acl.GetAccessRules($true, $true, $sid))
    [pscustomobject]@{
        protected = $acl.AreAccessRulesProtected
        allowed = @($rules | Where-Object { $_.AccessControlType -eq 'Allow' } |
                    ForEach-Object { $_.IdentityReference.Value } | Sort-Object -Unique)
        owner = $who
    } | ConvertTo-Json -Compress
    """
    policy = json.loads(
        subprocess.check_output(
            [shell, "-NoProfile", "-NonInteractive", "-Command", probe],
            env={**os.environ, "EDUFLOW_ACL_FIXTURE": str(env_file)},
        )
    )
    assert policy["protected"]
    assert set(policy["allowed"]) == {policy["owner"], "S-1-5-18"}
