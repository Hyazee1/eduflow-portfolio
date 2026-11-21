from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from redis import Redis
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from eduflow.adapters.http_api import router
from eduflow.adapters.security import BodyLimitMiddleware, LoginLimiter, RateLimited, public_details
from eduflow.application.ports import Clock
from eduflow.bootstrap.composition import build_platform
from eduflow.bootstrap.settings import ROOT, Settings
from eduflow.domain.errors import BusinessError, Conflict, Forbidden, NotFound, Unauthenticated
from eduflow.domain.time_rules import SHANGHAI, validate_templates

logger = logging.getLogger(__name__)


class BuildAssets(StaticFiles):
    def __init__(self, distribution: Any) -> None:
        self.distribution = distribution
        super().__init__(directory=distribution / "assets", check_dir=False)

    async def get_response(self, path: str, scope: Any) -> Response:
        if (
            not (self.distribution / "assets" / path).resolve().is_relative_to(self.distribution.resolve())
            or "/" in path
            or "\\" in path
            or path.startswith(".")
            or not re.fullmatch(
                r"[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{6,}\.(?:js|css|woff2?|svg|png|jpe?g|webp|ico)", path
            )
        ):
            raise HTTPException(404, "资源不存在")
        return await super().get_response(path, scope)


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.platform = build_platform(settings, clock)
        app.state.settings = settings
        app.state.login_limiter = LoginLimiter(settings.login_limit, settings.login_window_seconds)
        yield
        app.state.platform.engine.dispose()

    app = FastAPI(
        title="EduFlow AI V2.0",
        version="2.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        description="教培 CRM、CP-SAT 排课、审批与双向确认 API",
    )
    selected = APIRouter()
    selected.routes.extend(
        route
        for route in router.routes
        if settings.portal != "PUBLIC" or not getattr(route, "path", "").startswith("/api/v1/admin")
    )
    app.include_router(selected)
    hosts = settings.allowed_hosts + (["testserver"] if settings.portal == "TEST" else [])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(BodyLimitMiddleware)

    def problem(request: Request, status: int, title: str, code: str, details: Any = None) -> JSONResponse:
        return JSONResponse(
            {
                "type": "about:blank",
                "title": title,
                "status": status,
                "code": code,
                "instance": request.url.path,
                "request_id": getattr(request.state, "request_id", ""),
                "details": public_details(details),
            },
            status_code=status,
            media_type="application/problem+json",
        )

    @app.middleware("http")
    async def request_boundary(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = perf_counter()
        request.state.request_id = str(uuid4())
        if settings.portal == "PUBLIC" and request.url.path.startswith("/api/v1/admin"):
            return problem(request, 404, "接口不存在", "NOT_FOUND")
        length = request.headers.get("content-length", "0")
        if not length.isdecimal():
            return problem(request, 400, "请求长度无效", "INVALID_LENGTH")
        if len(length) > 8 or int(length) > 1_048_576:
            return problem(request, 413, "请求内容过大", "BODY_TOO_LARGE")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            allowed = settings.allowed_origins
            if origin and origin not in allowed:
                return problem(request, 403, "跨站请求被拒绝", "FORBIDDEN")
            if request.headers.get("sec-fetch-site") == "cross-site":
                return problem(request, 403, "跨站请求被拒绝", "FORBIDDEN")
            if (
                settings.portal != "TEST"
                and request.cookies.get(settings.cookie_name)
                and request.url.path.startswith("/api/v1/auth/")
                and not origin
                and request.headers.get("x-eduflow-request") != "1"
            ):
                return problem(request, 403, "认证请求缺少来源保护", "CSRF_REQUIRED")
        response = await call_next(request)
        response.headers["Server-Timing"] = f"app;dur={(perf_counter() - started) * 1000:.3f}"
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(BusinessError)
    async def business_error(request: Request, error: BusinessError) -> JSONResponse:
        status = (
            429
            if isinstance(error, RateLimited)
            else 401
            if isinstance(error, Unauthenticated)
            else 403
            if isinstance(error, Forbidden)
            else (404 if isinstance(error, NotFound) else 409 if isinstance(error, Conflict) else 422)
        )
        if isinstance(error, Forbidden) and getattr(request.state, "actor", None):
            actor = request.state.actor
            try:
                with app.state.platform.uow(actor.organization_id) as transaction:
                    transaction.records.add(
                        "audit_logs",
                        {
                            "actor_id": actor.id,
                            "action": "authorization.denied",
                            "entity_type": "api",
                            "entity_id": actor.id,
                            "detail": {"path": request.url.path},
                            "request_id": request.state.request_id,
                        },
                    )
                    transaction.commit()
            except OperationalError:
                logger.warning("Unable to store denied-access audit")
        return problem(request, status, str(error), error.code, error.details)

    @app.exception_handler(StarletteHTTPException)
    async def rejected_http(request: Request, error: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(error.status_code, "REQUEST_REJECTED")
        title = {404: "页面或接口不存在", 405: "请求方法不支持"}.get(error.status_code, "请求被拒绝")
        return problem(request, error.status_code, title, code)

    @app.exception_handler(RequestValidationError)
    async def request_validation(request: Request, error: RequestValidationError) -> JSONResponse:
        details = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in error.errors()]
        return problem(request, 422, "请求字段校验失败", "VALIDATION_ERROR", details)

    @app.exception_handler(ValidationError)
    async def model_validation(request: Request, error: ValidationError) -> JSONResponse:
        details = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in error.errors()]
        return problem(request, 422, "资源字段校验失败", "VALIDATION_ERROR", details)

    @app.exception_handler(OperationalError)
    async def database_unavailable(request: Request, error: OperationalError) -> JSONResponse:
        logger.error("Database unavailable: %s", type(error).__name__)
        return problem(request, 503, "数据库暂不可用，请检查 Docker 服务", "DATABASE_UNAVAILABLE")

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception) -> JSONResponse:
        # Never attach traceback/user-controlled exception text to persisted application logs.
        logger.error(
            "Unhandled request failure: request=%s type=%s", request.state.request_id, type(error).__name__
        )
        return problem(request, 500, "服务内部错误，请通过请求编号查看日志", "INTERNAL_ERROR")

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok", "version": "2.0.0"}

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        services = {"database": False, "redis": False, "worker": False, "time_templates": False}
        try:
            with app.state.platform.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                services["database"] = True
            with app.state.platform.factory() as session:
                from sqlalchemy import select

                from eduflow.adapters.models import Organization
                from eduflow.adapters.repository import SqlRecords

                org = session.scalar(
                    select(Organization).where(Organization.slug == settings.organization_slug)
                )
                if org:
                    today = datetime.now(SHANGHAI).date()
                    snapshot = SqlRecords(session, org.id).snapshot(today - timedelta(days=today.weekday()))
                    validate_templates(snapshot.slots)
                    services["time_templates"] = True
        except (OperationalError, BusinessError):
            pass
        try:
            client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
            services["redis"] = bool(client.ping())
            services["worker"] = bool(client.get("eduflow:worker:heartbeat"))
            client.close()
        except Exception as error:
            logger.debug("Readiness Redis probe: %s", type(error).__name__)
        success = all(services.values())
        return JSONResponse(
            {"status": "ready" if success else "waiting", "services": services, "portal": settings.portal},
            status_code=200 if success else 503,
        )

    distribution = settings.frontend_dir or ROOT / "frontend" / "dist" / settings.portal.lower()
    app.mount("/assets", BuildAssets(distribution), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> Response:
        if path.startswith(("api/", "health/")):
            raise HTTPException(404, "接口不存在")
        # Only the build's assets and known SPA routes are delivered. No arbitrary file fallback.
        routes = {
            "",
            "login",
            "schedule",
            "notifications",
            "changes",
            "profile",
            "students",
            "teachers",
            "catalog",
            "rooms",
            "demand",
            "offering-reviews",
            "audit",
            "scheduling",
            "approvals",
            "analytics",
            "rules",
            "reviews",
            "classes",
            "requests",
            "courses",
            "applications",
            "account",
            "accept",
        }
        if settings.portal == "PUBLIC":
            routes.difference_update(
                {
                    "students",
                    "teachers",
                    "catalog",
                    "rooms",
                    "demand",
                    "offering-reviews",
                    "audit",
                    "scheduling",
                    "approvals",
                    "analytics",
                    "rules",
                }
            )
        if path not in routes or re.search(r"[.\\\x00]", path):
            raise HTTPException(404, "页面不存在")
        index = distribution / "index.html"
        if not index.resolve().is_relative_to(distribution.resolve()):
            raise HTTPException(404, "资源不存在")
        if not index.is_file():
            return JSONResponse(
                {
                    "message": "前端尚未构建，请在项目目录执行 setup.ps1，或使用前端开发服务器",
                },
                status_code=503,
            )
        return FileResponse(index)

    return app


def create_public_app() -> FastAPI:
    return create_app(Settings(portal="PUBLIC"))


def create_admin_app() -> FastAPI:
    return create_app(Settings(portal="ADMIN"))
