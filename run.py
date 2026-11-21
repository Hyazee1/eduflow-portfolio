"""Local portfolio launcher. Never opens an Internet listener or tunnel."""

from __future__ import annotations

import argparse
import json
import logging
import os
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
ENDPOINTS = [("ADMIN", 8000), ("PUBLIC", 8100)]


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
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--archive")
    parser.add_argument("--target")
    args = parser.parse_args()
    if args.port != 8000:
        raise RuntimeError("双入口版本使用已验证的本机 8000/8100 端口；不自动修改网络暴露配置。")
    if not PYTHON.is_file() or not (ROOT / ".env").is_file():
        raise RuntimeError("项目环境或私有配置缺失，请先执行 setup.ps1。")
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
    if args.action == "doctor":
        for portal, port in ENDPOINTS:
            print(portal + ": " + ("ready" if ready(portal, port) else "not ready"))
        print("公网关闭；无需域名或隧道。")
        return 0
    compose = [docker, "compose", "-f", str(ROOT / "docker-compose.yml")]
    if args.action in {"backup", "restore"}:
        action = [
            *compose,
            "--profile",
            "tools",
            "run",
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
    for portal, port in ENDPOINTS:
        with socket.socket() as sock:
            occupied = sock.connect_ex(("127.0.0.1", port)) == 0
        if occupied and not ready(portal, port):
            raise RuntimeError(
                f"本机端口 {port} 已被其他服务或旧版应用占用；请先检查，启动器不会终止其他进程。"
            )
    if all(ready(portal, port) for portal, port in ENDPOINTS) and not args.prepare_only and not args.rebuild:
        print("EduFlow 双入口已就绪。")
    else:
        for name in ("admin", "public"):
            if not (ROOT / "frontend/dist" / name / "index.html").is_file():
                raise RuntimeError("双入口前端尚未构建，请执行 setup.ps1 或 npm run build。")
        execute([str(PYTHON), "scripts/runtime_config.py", "--files-only"], env, "检查本机私有文件权限…")
        execute([*compose, "config", "--quiet"], env, "验证默认关闭的网络配置…")
        execute(
            [*compose, "up", "-d", "--wait", "postgres", "redis"],
            env,
            "启动本机数据库与队列，保留原有数据卷…",
        )
        execute([str(PYTHON), "scripts/backup_before_migrate.py", docker], env, "升级前生成本机加密备份…")
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
        execute([*compose, "build", "worker"], env, "构建共享应用镜像，复用已缓存依赖…")
        execute(
            [*compose, "up", "-d", "--wait", "worker", "beat", "admin-api", "public-api"],
            env,
            "启动隔离应用并等待健康检查…",
        )
        deadline = time.monotonic() + 120
        while not all(ready(portal, port) for portal, port in ENDPOINTS):
            if time.monotonic() >= deadline:
                raise RuntimeError("双入口健康检查超时，请查看私有启动日志。")
            time.sleep(1)
    print("管理员：http://127.0.0.1:8000/login")
    print("教师/学生：http://127.0.0.1:8100/login")
    print("公网关闭。关闭此终端后容器继续运行；停止应用可执行 docker compose stop。")
    if not args.no_browser:
        webbrowser.open("http://127.0.0.1:8000/login")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
