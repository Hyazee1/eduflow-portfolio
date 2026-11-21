from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from eduflow.adapters.schemas import (
    AcceptAccountBody,
    AccountTokenBody,
    AgentBody,
    AttendanceBatchBody,
    AttendanceBody,
    AvailabilityBody,
    ChangeBody,
    ConfirmationBody,
    CopyWeekBody,
    CourseFields,
    DecisionBody,
    EditSessionBody,
    EnrollmentBody,
    FeedbackBody,
    InviteAccountBody,
    InviteReviewBody,
    LoginBody,
    PasswordBody,
    PublishBody,
    QualificationBody,
    ReasonBody,
    ResourceBody,
    ReviewBody,
    RoomAvailabilityBody,
    RoomFields,
    RulesBody,
    SolveBody,
    StrictBody,
    StudentFields,
    TeacherFields,
    UserBody,
    VersionBody,
)
from eduflow.domain.authorization import require_role
from eduflow.domain.entities import Principal
from eduflow.domain.errors import BusinessError, Forbidden, Unauthenticated

router = APIRouter(prefix="/api/v1")
bearer = HTTPBearer(auto_error=False)


def context(request: Request) -> Any:
    return request.app.state.platform


def principal(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> Principal:
    if credentials is None:
        raise Unauthenticated("请先登录")
    actor = context(request).auth.resolve(credentials.credentials)
    request.state.actor = actor
    return actor


def administrator(actor: Annotated[Principal, Depends(principal)]) -> Principal:
    require_role(actor, "ADMIN")
    return actor


Actor = Annotated[Principal, Depends(principal)]
Admin = Annotated[Principal, Depends(administrator)]
Idempotency = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]


def execute(request: Request, name: str, actor: Principal, data: dict[str, Any], key: str) -> dict[str, Any]:
    return context(request).commands[name].execute(actor, data, key)


def week_value(request: Request, actor: Principal, week: date | None) -> date:
    return week or date.fromisoformat(context(request).queries.bootstrap(actor)["default_week"])


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in request.app.state.settings.allowed_origins:
        raise Forbidden("跨站认证请求被拒绝")


def set_refresh(request: Request, response: Response, result: dict[str, Any]) -> dict[str, Any]:
    settings = request.app.state.settings
    raw = result.pop("refresh_token")
    response.set_cookie(
        settings.cookie_name,
        raw,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/api/v1/auth",
        max_age=settings.refresh_days * 86400,
    )
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/auth/config")
def login_configuration(request: Request) -> Any:
    settings = request.app.state.settings
    return {
        "portal": settings.portal,
        "demo_mode": settings.initialization_mode == "demo",
        "public_access_enabled": False,
    }


@router.post("/auth/login")
def login(body: LoginBody, request: Request, response: Response) -> Any:
    check_origin(request)
    request.app.state.login_limiter.check(body.username, request.client.host if request.client else "unknown")
    result = context(request).auth.login(body.username, body.password, body.role)
    old = request.cookies.get(request.app.state.settings.cookie_name)
    if old:
        context(request).auth.logout(old)
    return set_refresh(request, response, result)


@router.post("/auth/refresh")
def refresh(request: Request, response: Response) -> Any:
    check_origin(request)
    raw = request.cookies.get(request.app.state.settings.cookie_name)
    if not raw:
        raise Unauthenticated("没有有效登录会话")
    return set_refresh(request, response, context(request).auth.refresh(raw))


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> Any:
    check_origin(request)
    raw = request.cookies.get(request.app.state.settings.cookie_name)
    if raw:
        context(request).auth.logout(raw)
    response.delete_cookie(request.app.state.settings.cookie_name, path="/api/v1/auth")
    return {"status": "LOGGED_OUT"}


@router.get("/auth/sessions")
def auth_sessions(request: Request, actor: Actor) -> Any:
    return context(request).auth.sessions(actor)


@router.post("/auth/sessions/{session_id}/revoke")
def revoke_session(session_id: str, request: Request, actor: Actor, key: Idempotency) -> Any:
    return context(request).auth.revoke(actor, session_id=session_id)


@router.post("/auth/password")
def change_password(body: PasswordBody, request: Request, actor: Actor) -> Any:
    return context(request).auth.change_password(actor, body.current_password, body.new_password)


@router.post("/auth/accept")
def accept_account(body: AcceptAccountBody, request: Request) -> Any:
    check_origin(request)
    request.app.state.login_limiter.check(
        "credential-accept", request.client.host if request.client else "unknown"
    )
    return context(request).auth.accept_account_token(body.token, body.password, body.purpose)


@router.post("/admin/invitations", status_code=201)
def invite_account(body: InviteAccountBody, request: Request, actor: Admin, key: Idempotency) -> Any:
    return context(request).auth.invite_user(actor, body.model_dump(), key)


@router.post("/admin/users/{user_id}/account-token")
def account_token(
    user_id: str, body: AccountTokenBody, request: Request, actor: Admin, key: Idempotency
) -> Any:
    return context(request).auth.issue_account_token(actor, user_id, body.purpose, key)


@router.post("/admin/users/{user_id}/revoke-sessions")
def revoke_user_sessions(user_id: str, request: Request, actor: Admin, key: Idempotency) -> Any:
    return context(request).auth.revoke(actor, user_id=user_id)


@router.get("/auth/me")
def me(request: Request, actor: Actor) -> Any:
    return context(request).queries.profile(actor)["user"]


@router.get("/bootstrap")
def bootstrap(request: Request, actor: Actor) -> Any:
    result = context(request).queries.bootstrap(actor)
    result["agent_mode"] = "online" if context(request).agent.gateway else "offline"
    result["portal"] = request.app.state.settings.portal
    result["admin_portal_origin"] = request.app.state.settings.admin_portal_origin
    result["public_portal_origin"] = request.app.state.settings.public_portal_origin
    return result


@router.get("/time-slot-templates")
def templates(request: Request, actor: Actor) -> Any:
    data = context(request).queries.bootstrap(actor)
    return {"slots": data["slots"], "breaks": data["breaks"], "config_revision": data["config_revision"]}


@router.get("/courses")
def courses(request: Request, actor: Actor, week: date | None = None) -> Any:
    return context(request).queries.catalog(actor, week)


@router.get("/rooms")
def available_rooms(request: Request, actor: Actor) -> Any:
    return context(request).queries.rooms(actor)


@router.post("/enrollment-requests", status_code=201)
def enroll(body: EnrollmentBody, request: Request, actor: Actor, key: Idempotency) -> Any:
    return execute(request, "EnrollStudent", actor, body.model_dump(mode="json"), key)


@router.get("/enrollment-requests/mine")
def my_enrollments(request: Request, actor: Actor) -> Any:
    return context(request).queries.enrollments(actor)


@router.get("/students/me/schedule")
@router.get("/teachers/me/schedule")
def my_schedule(
    request: Request, actor: Actor, week: date | None = None, version_id: str | None = None
) -> Any:
    if "/students/" in request.url.path:
        require_role(actor, "STUDENT")
    else:
        require_role(actor, "TEACHER")
    return context(request).queries.schedule(actor, week_value(request, actor, week), version_id)


@router.get("/students/{owner_id}/schedule")
@router.get("/teachers/{owner_id}/schedule")
def owned_schedule(owner_id: str, request: Request, actor: Actor, week: date | None = None) -> Any:
    if owner_id != actor.id:
        require_role(actor, "ADMIN")
    return context(request).queries.schedule(actor, week_value(request, actor, week), owner_id=owner_id)


@router.get("/teachers/me/offering-reviews")
def reviews(request: Request, actor: Actor) -> Any:
    return context(request).queries.reviews(actor)


@router.post("/teacher-decisions")
def review(body: ReviewBody, request: Request, actor: Actor, key: Idempotency) -> Any:
    return execute(request, "ReviewOffering", actor, body.model_dump(), key)


@router.post("/schedule-confirmations")
def confirm(body: ConfirmationBody, request: Request, actor: Actor, key: Idempotency) -> Any:
    return execute(request, "ConfirmSchedule", actor, body.model_dump(), key)


@router.post("/admin/schedule/solve", status_code=202)
def solve(body: SolveBody, request: Request, actor: Admin, key: Idempotency) -> Any:
    return execute(request, "SolveWeek", actor, body.model_dump(mode="json"), key)


@router.get("/admin/schedule/jobs/{job_id}")
def solve_job(job_id: str, request: Request, actor: Admin) -> Any:
    return context(request).queries.job(actor, job_id)


@router.get("/admin/schedule/versions/{version_id}")
def get_version(version_id: str, request: Request, actor: Admin) -> Any:
    return context(request).queries.version(actor, version_id)


@router.post("/admin/schedule/versions/{version_id}/validate")
def validate_version(
    version_id: str, body: VersionBody, request: Request, actor: Admin, key: Idempotency
) -> Any:
    return execute(request, "ValidateVersion", actor, {**body.model_dump(), "version_id": version_id}, key)


@router.post("/admin/schedule/versions/{version_id}/publish")
def publish_version(
    version_id: str, body: PublishBody, request: Request, actor: Admin, key: Idempotency
) -> Any:
    return execute(request, "PublishVersion", actor, {**body.model_dump(), "version_id": version_id}, key)


@router.post("/admin/schedule/versions/{version_id}/cancel")
def cancel_version(
    version_id: str, body: VersionBody, request: Request, actor: Admin, key: Idempotency
) -> Any:
    return execute(request, "CancelVersion", actor, {**body.model_dump(), "version_id": version_id}, key)


@router.post("/admin/schedule/versions/{version_id}/sessions")
def edit_session(
    version_id: str, body: EditSessionBody, request: Request, actor: Admin, key: Idempotency
) -> Any:
    return execute(
        request, "EditDraftSession", actor, {**body.model_dump(mode="json"), "version_id": version_id}, key
    )


@router.post("/admin/schedule/versions/{version_id}/delete")
def delete_draft_version(
    version_id: str, body: VersionBody, request: Request, actor: Admin, key: Idempotency
) -> Any:
    return execute(request, "DeleteDraftVersion", actor, {**body.model_dump(), "version_id": version_id}, key)


@router.post("/admin/schedule/copy-week")
def copy_week(body: CopyWeekBody, request: Request, actor: Admin, key: Idempotency) -> Any:
    return execute(request, "CopyWeek", actor, body.model_dump(mode="json"), key)


@router.get("/admin/schedule")
def admin_schedule(
    request: Request, actor: Admin, week: date | None = None, version_id: str | None = None
) -> Any:
    return context(request).queries.schedule(actor, week_value(request, actor, week), version_id)


@router.post("/change-requests", status_code=201)
def create_change(body: ChangeBody, request: Request, actor: Actor, key: Idempotency) -> Any:
    result = execute(
        request, "CreateChangeRequest", actor, body.model_dump(mode="json", exclude_none=True), key
    )
    return context(request).queries.changes(actor, result["id"])


@router.get("/change-requests/mine")
def my_changes(request: Request, actor: Actor) -> Any:
    return context(request).queries.changes(actor)


@router.get("/change-requests/{change_id}")
def get_change(change_id: str, request: Request, actor: Actor) -> Any:
    return context(request).queries.changes(actor, change_id)


@router.post("/change-requests/{change_id}/simulate")
def simulate_change(change_id: str, request: Request, actor: Actor) -> Any:
    context(request).simulate.execute(actor, change_id)
    return context(request).queries.changes(actor, change_id)


@router.post("/change-requests/{change_id}/cancel")
def cancel_change(change_id: str, body: ReasonBody, request: Request, actor: Actor, key: Idempotency) -> Any:
    execute(request, "CancelChange", actor, {"change_id": change_id, **body.model_dump()}, key)
    return context(request).queries.changes(actor, change_id)


@router.get("/admin/change-requests")
def admin_changes(request: Request, actor: Admin) -> Any:
    return context(request).queries.changes(actor)


@router.post("/admin/change-requests/{change_id}/decision")
def decide_change(
    change_id: str, body: DecisionBody, request: Request, actor: Admin, key: Idempotency
) -> Any:
    execute(request, "DecideChange", actor, {"change_id": change_id, **body.model_dump()}, key)
    return context(request).queries.changes(actor, change_id)


@router.get("/notifications")
def notifications(request: Request, actor: Actor) -> Any:
    return context(request).queries.notifications(actor)


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: str, request: Request, actor: Actor, key: Idempotency) -> Any:
    return execute(request, "MarkNotificationRead", actor, {"id": notification_id}, key)


@router.get("/admin/analytics")
def analytics(request: Request, actor: Admin, week: date | None = None) -> Any:
    return context(request).queries.analytics(actor, week_value(request, actor, week))


@router.get("/rooms/{room_id}/schedule")
def room_schedule(room_id: str, request: Request, actor: Admin, week: date | None = None) -> Any:
    return context(request).queries.schedule(actor, week_value(request, actor, week), room_id=room_id)


@router.get("/profile")
@router.get("/availability/me")
def profile(request: Request, actor: Actor) -> Any:
    return context(request).queries.profile(actor)


@router.post("/availability/me")
def save_availability(body: AvailabilityBody, request: Request, actor: Actor, key: Idempotency) -> Any:
    return execute(request, "SaveAvailability", actor, body.model_dump(exclude_none=True), key)


@router.get("/admin/rooms/{room_id}/availability")
def room_availability(room_id: str, request: Request, actor: Admin) -> Any:
    return context(request).queries.room_availability(actor, room_id)


@router.post("/admin/rooms/{room_id}/availability")
def save_room_availability(
    room_id: str, body: RoomAvailabilityBody, request: Request, actor: Admin, key: Idempotency
) -> Any:
    return execute(
        request,
        "SaveAvailability",
        actor,
        {"owner_id": room_id, "owner_kind": "ROOM", **body.model_dump()},
        key,
    )


@router.post("/admin/users")
def save_user(body: UserBody, request: Request, actor: Admin, key: Idempotency) -> Any:
    data = body.model_dump(exclude_none=True)
    raw_password = data.pop("password", None)
    if raw_password:
        data["password_hash"] = context(request).auth.hasher.hash(raw_password)
    return execute(request, "SaveUser", actor, data, key)


@router.post("/admin/resources/{entity}")
def save_resource(entity: str, body: ResourceBody, request: Request, actor: Admin, key: Idempotency) -> Any:
    validators: dict[str, type[StrictBody]] = {
        "courses": CourseFields,
        "rooms": RoomFields,
        "student_profiles": StudentFields,
        "teacher_profiles": TeacherFields,
    }
    if entity not in validators:
        raise HTTPException(404, "资源类型不存在")
    values = validators[entity].model_validate(body.values).model_dump(exclude_unset=True, exclude_none=True)
    return execute(
        request,
        "SaveResource",
        actor,
        {
            **body.model_dump(exclude_none=True),
            "entity": entity,
            "values": values,
        },
        key,
    )


@router.post("/admin/teachers/{teacher_id}/qualifications")
def qualifications(
    teacher_id: str, body: QualificationBody, request: Request, actor: Admin, key: Idempotency
) -> Any:
    return execute(request, "SaveQualifications", actor, {"teacher_id": teacher_id, **body.model_dump()}, key)


@router.post("/admin/offerings/{offering_id}/reviews")
def invite_review(
    offering_id: str, body: InviteReviewBody, request: Request, actor: Admin, key: Idempotency
) -> Any:
    return execute(
        request, "InviteOfferingReview", actor, {"offering_id": offering_id, **body.model_dump()}, key
    )


@router.get("/admin/rules")
def get_rules(request: Request, actor: Admin) -> Any:
    return context(request).queries.bootstrap(actor)


@router.post("/admin/rules")
def save_rules(body: RulesBody, request: Request, actor: Admin, key: Idempotency) -> Any:
    return execute(request, "SaveRules", actor, {"rules": body.model_dump()}, key)


@router.get("/classes/{session_id}/roster")
def roster(session_id: str, request: Request, actor: Actor) -> Any:
    return context(request).queries.roster(actor, session_id)


@router.post("/attendance")
def attendance(body: AttendanceBody, request: Request, actor: Actor, key: Idempotency) -> Any:
    return execute(request, "RecordAttendance", actor, body.model_dump(), key)


@router.post("/attendance/batch")
def attendance_batch(body: AttendanceBatchBody, request: Request, actor: Actor, key: Idempotency) -> Any:
    return execute(request, "RecordAttendanceBatch", actor, body.model_dump(), key)


@router.post("/feedback")
def feedback(body: FeedbackBody, request: Request, actor: Actor, key: Idempotency) -> Any:
    return execute(request, "RecordFeedback", actor, body.model_dump(), key)


@router.post("/admin/sessions/{session_id}/pause")
def emergency_pause(
    session_id: str, body: ReasonBody, request: Request, actor: Admin, key: Idempotency
) -> Any:
    return execute(request, "EmergencyPause", actor, {"session_id": session_id, **body.model_dump()}, key)


@router.get("/agent/tools")
def agent_tools(request: Request, actor: Actor) -> Any:
    return context(request).agent.tools.definitions(actor)


@router.post("/agent/chat")
def agent_chat(body: AgentBody, request: Request, actor: Actor, key: Idempotency) -> Any:
    return context(request).agent.execute(actor, body.message, body.context, key)


@router.get("/admin/{entity}")
def admin_records(
    entity: str,
    request: Request,
    actor: Admin,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    search: str = Query("", max_length=100),
    week: date | None = None,
    role: str | None = None,
) -> Any:
    aliases = {
        "students": "student_profiles",
        "teachers": "teacher_profiles",
        "demand": "enrollment_requests",
        "versions": "schedule_versions",
        "offerings": "course_offerings",
        "audit": "audit_logs",
        "jobs": "solve_jobs",
        "outbox": "outbox_events",
        "teacher-decisions": "teacher_decisions",
    }
    target = aliases.get(entity, entity)
    filters: dict[str, Any] = {}
    if week and target in {"schedule_versions", "enrollment_requests", "course_offerings", "solve_jobs"}:
        filters["week"] = week
    if role and target == "users":
        if role not in {"ADMIN", "TEACHER", "STUDENT"}:
            raise BusinessError("身份筛选无效")
        filters["role"] = role
    return context(request).queries.records(actor, target, page, page_size, search, filters)
