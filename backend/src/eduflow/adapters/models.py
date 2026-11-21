"""Persistence models; SQL protects relational and cross-tenant integrity."""

from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSTZRANGE, ExcludeConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class AuditRow(Base):
    __abstract__ = True
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class Organization(AuditRow):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    timezone: Mapped[str] = mapped_column(String(40), default="Asia/Shanghai")
    config_revision: Mapped[int] = mapped_column(Integer, default=1)
    rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    seeded: Mapped[bool] = mapped_column(Boolean, default=False)


class TenantRow(AuditRow):
    __abstract__ = True
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)


class User(TenantRow):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("organization_id", "username"),
        CheckConstraint("role IN ('ADMIN','TEACHER','STUDENT')"),
    )
    username: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(16))
    password_hash: Mapped[str] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    phone: Mapped[str] = mapped_column(String(40), default="")


class StudentProfile(TenantRow):
    __tablename__ = "student_profiles"
    __table_args__ = (CheckConstraint("age BETWEEN 3 AND 100"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    student_code: Mapped[str] = mapped_column(String(30))
    gender: Mapped[str] = mapped_column(String(10), default="")
    age: Mapped[int] = mapped_column(Integer)
    grade: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, default="")


class TeacherProfile(TenantRow):
    __tablename__ = "teacher_profiles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    teacher_code: Mapped[str] = mapped_column(String(30))
    bio: Mapped[str] = mapped_column(Text, default="")


class CourseRow(TenantRow):
    __tablename__ = "courses"
    __table_args__ = (
        CheckConstraint("course_group IN ('MAIN','INTEREST','EXTENSION','QA')"),
        CheckConstraint("min_size > 0 AND max_size >= min_size"),
        CheckConstraint("min_age <= max_age AND min_grade <= max_grade"),
        CheckConstraint(
            "(course_group = 'QA' AND duration = 35) OR (course_group <> 'QA' AND duration = 45)"
        ),
    )
    name: Mapped[str] = mapped_column(String(80))
    course_group: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text, default="")
    min_age: Mapped[int] = mapped_column(Integer, default=6)
    max_age: Mapped[int] = mapped_column(Integer, default=18)
    min_grade: Mapped[int] = mapped_column(Integer, default=1)
    max_grade: Mapped[int] = mapped_column(Integer, default=12)
    duration: Mapped[int] = mapped_column(Integer, default=45)
    min_size: Mapped[int] = mapped_column(Integer, default=3)
    max_size: Mapped[int] = mapped_column(Integer, default=20)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    preferred_room: Mapped[str] = mapped_column(String(30), default="GENERAL")


class CourseFeature(TenantRow):
    __tablename__ = "course_features"
    __table_args__ = (UniqueConstraint("course_id", "feature"),)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), index=True)
    feature: Mapped[str] = mapped_column(String(40))
    safety_required: Mapped[bool] = mapped_column(Boolean, default=False)


class Qualification(TenantRow):
    __tablename__ = "teacher_qualifications"
    __table_args__ = (UniqueConstraint("teacher_id", "course_id"),)
    teacher_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), index=True)


class RoomRow(TenantRow):
    __tablename__ = "rooms"
    __table_args__ = (CheckConstraint("capacity > 0"),)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(40))
    capacity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    notes: Mapped[str] = mapped_column(Text, default="")


class RoomFeature(TenantRow):
    __tablename__ = "room_features"
    __table_args__ = (UniqueConstraint("room_id", "feature"),)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"), index=True)
    feature: Mapped[str] = mapped_column(String(40))


class TimeSlot(TenantRow):
    __tablename__ = "time_slot_templates"
    __table_args__ = (
        UniqueConstraint("organization_id", "revision", "code"),
        CheckConstraint("duration_minutes IN (35,45)"),
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    code: Mapped[str] = mapped_column(String(5))
    start: Mapped[str] = mapped_column(String(5))
    end: Mapped[str] = mapped_column(String(5))
    kind: Mapped[str] = mapped_column(String(12))
    period: Mapped[str] = mapped_column(String(4))
    ordinal: Mapped[int] = mapped_column(Integer)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    is_bookable: Mapped[bool] = mapped_column(Boolean, default=True)


class AvailabilityRule(TenantRow):
    __tablename__ = "availability_rules"
    __table_args__ = (
        UniqueConstraint("organization_id", "owner_id", "day_index", "slot_code"),
        CheckConstraint("day_index BETWEEN 0 AND 4"),
    )
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    owner_kind: Mapped[str] = mapped_column(String(16))
    day_index: Mapped[int] = mapped_column(Integer)
    slot_code: Mapped[str] = mapped_column(String(5))


class AvailabilityException(TenantRow):
    __tablename__ = "availability_exceptions"
    __table_args__ = (CheckConstraint("ends_at > starts_at"),)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    owner_kind: Mapped[str] = mapped_column(String(16))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(500))
    change_request_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class Offering(TenantRow):
    __tablename__ = "course_offerings"
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), index=True)
    week: Mapped[date] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), default="PENDING_REVIEW")
    min_age: Mapped[int] = mapped_column(Integer, default=6)
    max_age: Mapped[int] = mapped_column(Integer, default=18)


class Enrollment(TenantRow):
    __tablename__ = "enrollment_requests"
    __table_args__ = (UniqueConstraint("student_id", "course_id", "week"),)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), index=True)
    offering_id: Mapped[str] = mapped_column(ForeignKey("course_offerings.id"), index=True)
    week: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(30), default="PENDING_REVIEW")
    priority: Mapped[int] = mapped_column(Integer, default=1)


class EnrollmentPreference(TenantRow):
    __tablename__ = "enrollment_time_preferences"
    __table_args__ = (UniqueConstraint("request_id", "day_index", "slot_code"),)
    request_id: Mapped[str] = mapped_column(ForeignKey("enrollment_requests.id"), index=True)
    day_index: Mapped[int] = mapped_column(Integer)
    slot_code: Mapped[str] = mapped_column(String(5))
    rank: Mapped[int] = mapped_column(Integer)


class TeacherDecision(TenantRow):
    __tablename__ = "teacher_decisions"
    __table_args__ = (
        UniqueConstraint("teacher_id", "offering_id"),
        CheckConstraint("decision IN ('PENDING','ACCEPTED','REJECTED','ADJUST')"),
    )
    teacher_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    offering_id: Mapped[str] = mapped_column(ForeignKey("course_offerings.id"), index=True)
    decision: Mapped[str] = mapped_column(String(20), default="PENDING")
    reason: Mapped[str] = mapped_column(Text, default="")


class OfferingSlot(TenantRow):
    __tablename__ = "offering_slots"
    __table_args__ = (UniqueConstraint("offering_id", "day_index", "slot_code"),)
    offering_id: Mapped[str] = mapped_column(ForeignKey("course_offerings.id"), index=True)
    day_index: Mapped[int] = mapped_column(Integer)
    slot_code: Mapped[str] = mapped_column(String(5))


class ScheduleVersion(TenantRow):
    __tablename__ = "schedule_versions"
    __table_args__ = (
        Index(
            "uq_pending_week",
            "organization_id",
            "week",
            unique=True,
            postgresql_where=text("status = 'PUBLISHED_PENDING_CONFIRMATION'"),
        ),
        Index(
            "uq_active_week",
            "organization_id",
            "week",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        CheckConstraint(
            "status IN ('DRAFT','VALIDATED','PUBLISHED_PENDING_CONFIRMATION','ACTIVE','CANCELLED','SUPERSEDED')"
        ),
    )
    week: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(40), default="DRAFT")
    solver_status: Mapped[str] = mapped_column(String(30))
    base_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("schedule_versions.id"), nullable=True, index=True
    )
    base_version_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmation_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    change_request_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class CourseSession(TenantRow):
    __tablename__ = "course_sessions"
    __table_args__ = (
        UniqueConstraint("version_id", "logical_id"),
        CheckConstraint("end_time > start_time"),
    )
    logical_id: Mapped[str] = mapped_column(String(36), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("schedule_versions.id"), index=True)
    offering_id: Mapped[str] = mapped_column(ForeignKey("course_offerings.id"), index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), index=True)
    teacher_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    slot_code: Mapped[str] = mapped_column(String(5))
    start_time: Mapped[str] = mapped_column(String(5))
    end_time: Mapped[str] = mapped_column(String(5))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")


class SessionEnrollment(TenantRow):
    __tablename__ = "session_enrollments"
    __table_args__ = (UniqueConstraint("session_id", "student_id"),)
    session_id: Mapped[str] = mapped_column(ForeignKey("course_sessions.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("enrollment_requests.id"), index=True)


class Confirmation(TenantRow):
    __tablename__ = "schedule_confirmations"
    __table_args__ = (UniqueConstraint("session_id", "user_id"),)
    session_id: Mapped[str] = mapped_column(ForeignKey("course_sessions.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(20), default="PENDING")
    reason: Mapped[str] = mapped_column(Text, default="")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    inherited_from: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ChangeRequest(TenantRow):
    __tablename__ = "change_requests"
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(40), default="SUBMITTED")
    reason: Mapped[str] = mapped_column(Text)
    week: Mapped[date] = mapped_column(Date, index=True)
    base_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("schedule_versions.id"), nullable=True, index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    proposal: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    admin_comment: Mapped[str] = mapped_column(Text, default="")


class ChangeEvent(TenantRow):
    __tablename__ = "change_request_events"
    __table_args__ = (UniqueConstraint("sequence", name="uq_change_event_sequence"),)
    sequence: Mapped[int] = mapped_column(BigInteger, Identity())
    change_request_id: Mapped[str] = mapped_column(ForeignKey("change_requests.id"), index=True)
    from_state: Mapped[str] = mapped_column(String(40))
    to_state: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    message: Mapped[str] = mapped_column(Text, default="")


class ResourceReservation(TenantRow):
    __tablename__ = "resource_reservations"
    __table_args__ = (
        ExcludeConstraint(
            ("organization_id", "="),
            ("resource_kind", "="),
            ("resource_id", "="),
            ("during", "&&"),
            name="ex_resource_time",
            using="gist",
            where=text("is_active"),
        ),
        CheckConstraint(
            "(resource_kind IN ('TEACHER','STUDENT') AND user_id = resource_id AND room_id IS NULL)"
            " OR (resource_kind = 'ROOM' AND room_id = resource_id AND user_id IS NULL)"
        ),
    )
    session_id: Mapped[str] = mapped_column(ForeignKey("course_sessions.id"), index=True)
    resource_kind: Mapped[str] = mapped_column(String(12))
    resource_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    room_id: Mapped[str | None] = mapped_column(ForeignKey("rooms.id"), nullable=True, index=True)
    during: Mapped[Any] = mapped_column(TSTZRANGE)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class Notification(TenantRow):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "dedup_key"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    dedup_key: Mapped[str] = mapped_column(String(160))
    link: Mapped[str] = mapped_column(String(250), default="")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OutboxEvent(TenantRow):
    __tablename__ = "outbox_events"
    __table_args__ = (UniqueConstraint("organization_id", "dedup_key"),)
    event_type: Mapped[str] = mapped_column(String(50))
    dedup_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(String(500), default="")


class Attendance(TenantRow):
    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint("session_id", "student_id"),
        CheckConstraint("status IN ('PRESENT','ABSENT','LATE','EXCUSED')"),
    )
    session_id: Mapped[str] = mapped_column(ForeignKey("course_sessions.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    teacher_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(20))
    notes: Mapped[str] = mapped_column(Text, default="")


class Feedback(TenantRow):
    __tablename__ = "course_feedback"
    __table_args__ = (
        UniqueConstraint("session_id", "student_id"),
        CheckConstraint("rating BETWEEN 1 AND 5"),
    )
    session_id: Mapped[str] = mapped_column(ForeignKey("course_sessions.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    teacher_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    rating: Mapped[int] = mapped_column(Integer, default=5)
    content: Mapped[str] = mapped_column(Text)


class AuditLog(TenantRow):
    __tablename__ = "audit_logs"
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(36))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    request_id: Mapped[str] = mapped_column(String(100), default="")


class SolveJob(TenantRow):
    __tablename__ = "solve_jobs"
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    week: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    version_id: Mapped[str | None] = mapped_column(
        ForeignKey("schedule_versions.id"), nullable=True, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(String(500), default="")


class IdempotencyRecord(TenantRow):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("organization_id", "actor_id", "operation", "key"),)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    operation: Mapped[str] = mapped_column(String(100))
    key: Mapped[str] = mapped_column(String(128))
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]] = mapped_column(JSON)


class AuthSession(TenantRow):
    __tablename__ = "auth_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    portal: Mapped[str] = mapped_column(String(12), default="LEGACY", index=True)


class AccountToken(TenantRow):
    __tablename__ = "account_tokens"
    __table_args__ = (
        UniqueConstraint("organization_id", "created_by", "request_key", name="uq_account_token_request"),
        CheckConstraint("purpose IN ('INVITE', 'RESET')", name="ck_account_token_purpose"),
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    purpose: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    request_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), default="")


# Composite foreign keys protect tenant identity even when writes bypass the HTTP adapter.
for _table in tuple(Base.metadata.tables.values()):
    if "organization_id" in _table.c:
        _table.append_constraint(
            UniqueConstraint("organization_id", "id", name=f"uq_{_table.name}_tenant_id")
        )
for _table in tuple(Base.metadata.tables.values()):
    if "organization_id" not in _table.c:
        continue
    for _fk in tuple(_table.foreign_keys):
        _target = _fk.column.table
        _target_name = _fk.target_fullname.rsplit(".", 1)[0]
        if "organization_id" in _target.c and _fk.parent.name != "organization_id":
            _table.append_constraint(
                ForeignKeyConstraint(
                    ["organization_id", _fk.parent.name],
                    [f"{_target_name}.organization_id", f"{_target_name}.id"],
                    name=f"fk_{_table.name}_{_fk.parent.name}_tenant",
                )
            )

ENTITY_MODELS = {
    str(getattr(mapper.class_, "__tablename__")): mapper.class_
    for mapper in Base.registry.mappers
    if mapper.class_ is not Organization
}
