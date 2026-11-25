"""Local portfolio launcher. Never opens an Internet listener or tunnel."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def docker_path() -> str:
    candidates = [
        shutil.which("docker"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/DockerDesktop/resources/bin/docker.exe"),
        str(Path(os.environ.get("ProgramFiles", "")) / "Docker/Docker/resources/bin/docker.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("请先安装并打开 Docker Desktop，使用 Linux 容器。")


def http_json(url: str) -> dict:
    with urlopen(url, timeout=5) as response:
        return json.load(response)


def ready(portal: str, port: int) -> bool:
    try:
        value = http_json(f"http://127.0.0.1:{port}/health/ready")
        return value.get("status") == "ready" and value.get("portal") == portal
    except (URLError, ValueError, OSError):
        return False


def verified_image_refs(config: dict) -> dict[str, str]:
    refs = {name: service["image"] for name, service in config["services"].items() if "image" in service}
    if not any("@" in ref for ref in refs.values()):
        return {}
    if not all(re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", ref) for ref in refs.values()):
        raise RuntimeError("按摘要启动时，应用、PostgreSQL 和 Redis 都须指定完整的 SHA-256 镜像引用。")
    return refs


def execute(command: list[str], env: dict[str, str], title: str) -> None:
    print(title, flush=True)
    logs = ROOT / ".runtime/private/logs"
    logs.mkdir(parents=True, exist_ok=True)
    target = logs / "startup.log"
    handler = RotatingFileHandler(target, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8")
    logger = logging.Logger("eduflow.launcher")
    logger.addHandler(handler)
    try:
        with subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        ) as process:
            assert process.stdout is not None
            for line in process.stdout:
                logger.warning(line.rstrip("\r\n"))
            returncode = process.wait()
    finally:
        handler.close()
    if returncode:
        raise RuntimeError(
            "步骤失败，详细诊断保存在本机 .runtime/private/logs/startup.log（请勿公开原始日志）。"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="EduFlow 本地双入口启动器，公网默认关闭")
    parser.add_argument("action", nargs="?", default="up", choices=["up", "doctor", "backup", "restore"])
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--rebuild", action="store_true", help="代码更新后备份、迁移并重建本地应用")
    parser.add_argument("--port", type=int, help="Check the configured admin port")
    parser.add_argument("--archive")
    parser.add_argument("--target")
    args = parser.parse_args()
    if not PYTHON.is_file() or not (ROOT / ".env").is_file():
        raise RuntimeError("项目环境或私有配置缺失，请先执行 setup.ps1。")
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
    env = dict(os.environ, PYTHONPATH=str(ROOT / "backend/src"), PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    execute(
        [str(PYTHON), "-c", "from eduflow.bootstrap.settings import Settings; Settings()"],
        env,
        "校验本地运行配置…",
    )
    docker = docker_path()
    probe = subprocess.run(
        [docker, "info", "--format", "{{.OSType}}"], capture_output=True, text=True, check=False, timeout=30
    )
    if probe.returncode or probe.stdout.strip() != "linux":
        raise RuntimeError("Docker Linux 引擎尚未就绪，请打开 Docker Desktop，等待 Engine running。")
    compose = [docker, "compose", "-f", str(ROOT / "docker-compose.yml")]
    config = json.loads(subprocess.check_output([*compose, "config", "--format", "json"], cwd=ROOT, env=env))
    endpoints = []
    for portal, service in (("ADMIN", "admin-api"), ("PUBLIC", "public-api")):
        bindings = config["services"][service]["ports"]
        if len(bindings) != 1 or bindings[0]["host_ip"] != "127.0.0.1":
            raise RuntimeError("The portfolio requires loopback-only HTTP listeners.")
        endpoints.append((portal, int(bindings[0]["published"])))
    if args.port is not None and args.port != endpoints[0][1]:
        raise RuntimeError("Requested admin port differs from the Compose configuration.")
    if args.action == "doctor":
        for portal, port in endpoints:
            print(portal + ": " + ("ready" if ready(portal, port) else "not ready"))
        print("公网关闭；无需域名或隧道。")
        return 0
    refs = verified_image_refs(config)
    identities = {}
    for ref in set(refs.values()):
        image = subprocess.run(
            [docker, "image", "inspect", "--format", "{{.Id}}", ref], env=env, text=True, capture_output=True
        )
        if image.returncode or not image.stdout.strip():
            raise RuntimeError("已验证的镜像摘要在本机缺失，请先恢复或加载对应镜像。")
        identities[ref] = image.stdout.strip()
    if refs and args.rebuild:
        raise RuntimeError("按已验证的摘要启动时，请使用 run.py --no-browser。重新构建请另用测试标签。")
    if args.action in {"backup", "restore"}:
        action = [
            *compose,
            "--profile",
            "tools",
            "run",
            "--no-deps",
            "--pull",
            "never" if refs else "missing",
            "--rm",
            "maintenance",
            "python",
            "-m",
            "eduflow.bootstrap.cli",
            args.action,
        ]
        if args.action == "restore":
            if not args.archive or not args.target:
                raise RuntimeError(
                    "恢复需要 --archive 备份文件名及全新的 --target *_test 或 *_restored 数据库。"
                )
            archive = Path(args.archive)
            if archive.name != args.archive:
                raise RuntimeError("备份文件必须位于本机私有 backups 目录，请仅传文件名。")
            action += ["--archive", "/private/backups/" + archive.name, "--target", args.target]
        execute(action, env, "执行本机加密备份/恢复，保留现有数据库…")
        return 0
    for portal, port in endpoints:
        with socket.socket() as sock:
            occupied = sock.connect_ex(("127.0.0.1", port)) == 0
        if occupied and not ready(portal, port):
            raise RuntimeError(
                f"本机端口 {port} 已被其他服务或旧版应用占用；请先检查，启动器不会终止其他进程。"
            )
    already_ready = all(ready(portal, port) for portal, port in endpoints)
    if already_ready and refs:
        for name in ("postgres", "redis", "admin-api", "public-api", "worker", "beat"):
            cid = subprocess.check_output([*compose, "ps", "-q", name], cwd=ROOT, env=env, text=True).strip()
            if (
                not cid
                or subprocess.check_output(
                    [docker, "inspect", "--format", "{{.Image}}", cid], env=env, text=True
                ).strip()
                != identities[refs[name]]
            ):
                raise RuntimeError("运行中的服务与指定镜像不一致。请先在维护窗口停写、备份并准备副本卷。")
    if already_ready and not args.prepare_only and not args.rebuild:
        print("EduFlow 双入口已就绪。")
    else:
        for name in ("admin", "public"):
            if not (ROOT / "frontend/dist" / name / "index.html").is_file():
                raise RuntimeError("双入口前端尚未构建，请执行 setup.ps1 或 npm run build。")
        execute([*compose, "config", "--quiet"], env, "验证默认关闭的网络配置…")
        existing = subprocess.check_output(
            [*compose, "ps", "--all", "--quiet", "postgres"], cwd=ROOT, env=env
        ).strip()
        marker = ROOT / ".runtime/private/postgres-image-id"
        pg_image = config["services"]["postgres"]["image"]
        if existing:
            storage = subprocess.check_output(
                [
                    docker,
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "org.eduflow.postgres-storage"}}',
                    existing,
                ],
                env=env,
                text=True,
            ).strip()
            if storage != "pg16-alpine-icu":
                raise RuntimeError(
                    "发现旧 PostgreSQL 容器。请使用原环境备份，并在新的 Compose 项目和卷内逻辑恢复。"
                )
        execute([str(PYTHON), "scripts/runtime_config.py", "--files-only"], env, "检查本机私有文件权限…")
        if existing:
            execute([*compose, "start", "--wait", "postgres"], env, "等待原 PostgreSQL 实例就绪…")
            execute(
                [str(PYTHON), "scripts/backup_before_migrate.py", docker, *compose[2:]],
                env,
                "替换数据库容器前，使用旧版 pg_dump 创建加密恢复点…",
            )
        else:
            volume = config["volumes"]["postgres_data"]["name"]
            volumes = subprocess.check_output([docker, "volume", "ls", "-q"], env=env, text=True).splitlines()
            if volume in volumes:
                image = subprocess.run(
                    [docker, "image", "inspect", "--format", "{{.Id}}", pg_image],
                    env=env,
                    text=True,
                    capture_output=True,
                )
                if (
                    image.returncode
                    or not marker.is_file()
                    or marker.read_text().strip() != image.stdout.strip()
                ):
                    raise RuntimeError("发现脱离容器的原数据卷，请先使用原镜像启动并验证备份，再升级。")
                execute(
                    [*compose, "up", "--no-build", "--pull", "never", "-d", "--wait", "postgres"],
                    env,
                    "使用记录的原镜像加载现有卷…",
                )
                execute(
                    [str(PYTHON), "scripts/backup_before_migrate.py", docker, *compose[2:]],
                    env,
                    "备份现有数据库…",
                )
        execute(
            [
                *compose,
                "up",
                *(["--no-build", "--pull", "never"] if refs else ["--build"]),
                "-d",
                "--wait",
                "postgres",
                "redis",
            ],
            env,
            "启动本机数据库与队列，保留原有数据卷…",
        )
        container_id = subprocess.check_output(
            [*compose, "ps", "-q", "postgres"], env=env, cwd=ROOT, text=True
        ).strip()
        marker.write_bytes(
            subprocess.check_output(
                [docker, "inspect", "--format", "{{.Image}}", container_id], env=env
            ).strip()
        )
        execute([str(PYTHON), "-m", "eduflow.bootstrap.cli", "migrate"], env, "执行增量迁移…")
        execute(
            [str(PYTHON), "-m", "eduflow.bootstrap.cli", "initialize"],
            env,
            "按本地配置幂等初始化，保留已有业务…",
        )
        execute([str(PYTHON), "-m", "eduflow.bootstrap.cli", "check"], env, "校验统一教学时段…")
        execute([str(PYTHON), "scripts/runtime_config.py"], env, "配置非超级用户数据库角色和独立密钥…")
        if args.prepare_only:
            print("准备完成；尚未启动双入口 API。")
            return 0
        if not refs:
            execute([*compose, "build", "worker"], env, "构建共享应用镜像，复用已缓存依赖…")
        execute(
            [
                *compose,
                "up",
                *(["--no-build", "--pull", "never"] if refs else []),
                "-d",
                "--wait",
                "worker",
                "beat",
                "admin-api",
                "public-api",
            ],
            env,
            "启动隔离应用并等待健康检查…",
        )
        deadline = time.monotonic() + 120
        while not all(ready(portal, port) for portal, port in endpoints):
            if time.monotonic() >= deadline:
                raise RuntimeError("双入口健康检查超时，请查看私有启动日志。")
            time.sleep(1)
    login_url = f"http://127.0.0.1:{endpoints[0][1]}/login"
    print("管理员：" + login_url)
    print(f"教师/学生：http://127.0.0.1:{endpoints[1][1]}/login")
    print("公网关闭。关闭此终端后容器继续运行；停止应用可执行 docker compose stop。")
    if not args.no_browser:
        webbrowser.open(login_url)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
