"""Inspect candidate project files locally; reports never contain matched secret values."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".cjs",
    ".css",
    ".md",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".txt",
    ".bat",
    ".ps1",
    ".html",
    ".svg",
    ".xml",
    ".in",
    ".example",
    ".conf",
    ".sql",
    ".csv",
    ".dax",
    ".tmdl",
    ".pbip",
    ".pbir",
    ".pbism",
    ".platform",
}


def is_text_candidate(path: Path) -> bool:
    return path.is_file() and (
        path.suffix.lower() in TEXT or path.name in {"Dockerfile", ".gitignore", ".dockerignore", ".platform"}
    )


def private_values(root: Path) -> list[str]:
    env = root / ".env"
    values = []
    if env.is_file():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            key, _, value = line.partition("=")
            if key in {"JWT_SECRET", "POSTGRES_PASSWORD", "LLM_API_KEY"} and len(value.strip()) >= 12:
                values.append(value.strip().strip("\"'"))
    for name in (
        "admin.key",
        "public.key",
        "backup.key",
        "application.password",
        "redis.password",
        "redis-health.password",
    ):
        path = root / ".runtime/private/secrets" / name
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if len(value) >= 12:
                values.append(value)
    return values


def scan(root: Path, paths: list[Path], values: list[str]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in paths:
        if not is_text_candidate(path):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            findings.append({"file": path.relative_to(root).as_posix(), "rule": "OUTSIDE_PROJECT"})
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
            rules = []
            if any(value and value in line for value in values):
                rules.append("LOCAL_SECRET")
            personal = [os.environ.get("USERNAME", ""), os.environ.get("COMPUTERNAME", "")]
            if (
                any(
                    len(value) >= 3
                    and re.search(r"(?<![\w-])" + re.escape(value) + r"(?![\w-])", line, re.IGNORECASE)
                    for value in personal
                )
                and "privacy: fixture" not in line
            ):
                rules.append("PERSONAL_IDENTIFIER")
            if (
                re.search(r"[A-Za-z]:[\\/](?:Users|Projects|Codex)[\\/]", line)
                and "privacy: fixture" not in line
            ):
                rules.append("MACHINE_PATH")
            if (
                re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", line)
                and "privacy: fixture" not in line
            ):
                rules.append("PRIVATE_KEY")
            findings.extend(
                {"file": path.relative_to(root).as_posix(), "line": number, "rule": rule} for rule in rules
            )
    return findings


def candidates(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        check=True,
    )
    paths = {root / name for name in result.stdout.decode("utf-8").split("\0") if name}
    for directory in (root / "frontend/dist/admin", root / "frontend/dist/public"):
        if directory.exists():
            paths.update(path for path in directory.rglob("*") if path.is_file())
    return sorted(paths)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=ROOT / ".runtime/private/privacy-scan.json")
    args = parser.parse_args()
    paths = candidates(ROOT)
    checked_paths = [path for path in paths if is_text_candidate(path)]
    findings = scan(ROOT, checked_paths, private_values(ROOT))
    result = {
        "candidate_files": len(paths),
        "files_checked": len(checked_paths),
        "excluded_non_text": len(paths) - len(checked_paths),
        "findings": findings,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**result, "findings": len(findings)}))
    raise SystemExit(bool(findings))


if __name__ == "__main__":
    main()
