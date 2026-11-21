from datetime import date
from typing import Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Role = Literal["ADMIN", "TEACHER", "STUDENT"]
SlotCode = Literal["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)


class LoginBody(StrictBody):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=128)
    role: Role


class PasswordBody(StrictBody):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class AcceptAccountBody(StrictBody):
    token: str = Field(min_length=32, max_length=200)
    password: str = Field(min_length=12, max_length=128)
    purpose: Literal["INVITE", "RESET"]


class AccountTokenBody(StrictBody):
    purpose: Literal["INVITE", "RESET"]


class InviteAccountBody(StrictBody):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,60}$")
    name: str = Field(min_length=1, max_length=80)
    role: Literal["STUDENT", "TEACHER"]
    age: int = Field(default=10, ge=3, le=100)
    grade: int = Field(default=5, ge=0, le=20)


class PreferenceBody(StrictBody):
    day_index: int = Field(ge=0, le=4)
    slot_code: SlotCode


class EnrollmentBody(StrictBody):
    course_id: str = Field(min_length=1, max_length=36)
    week: date
    preferences: list[PreferenceBody] = Field(min_length=1, max_length=40)


class ReviewBody(StrictBody):
    decision_id: str = Field(max_length=36)
    decision: Literal["ACCEPTED", "REJECTED", "ADJUST"]
    version: int = Field(ge=1)
    reason: str = Field(default="", max_length=2000)


class SolveBody(StrictBody):
    week: date
    optimization_profile: Literal["balanced", "preference_first"] = "balanced"
    reference_version_id: str | None = Field(default=None, min_length=1, max_length=36)


class VersionBody(StrictBody):
    version: int = Field(ge=1)


class PublishBody(VersionBody):
    replace_expired_version_id: str | None = Field(default=None, min_length=1, max_length=36)
    replace_expired_version_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def replacement_pair(self) -> Self:
        if (self.replace_expired_version_id is None) != (self.replace_expired_version_revision is None):
            raise ValueError("Replacing an expired version requires its id and revision")
        return self


class ConfirmationBody(StrictBody):
    version_id: str = Field(max_length=36)
    session_ids: list[str] = Field(min_length=1, max_length=200)
    decision: Literal["ACCEPTED", "REJECTED"]
    reason: str = Field(default="", max_length=2000)


class EditSessionBody(VersionBody):
    session_id: str = Field(max_length=36)
    teacher_id: str = Field(max_length=36)
    room_id: str = Field(max_length=36)
    day: date
    slot_code: SlotCode


class ChangePayload(StrictBody):
    session_ids: list[str] = Field(default_factory=list, max_length=200)
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    target_day: date | None = None
    target_slot: SlotCode | None = None
    owner_id: str | None = Field(default=None, max_length=36)
    teacher_id: str | None = Field(default=None, max_length=36)
    room_id: str | None = Field(default=None, max_length=36)
    request_ids: list[str] = Field(default_factory=list, max_length=500)


class ChangeBody(StrictBody):
    version_id: str = Field(max_length=36)
    kind: Literal[
        "TEACHER_LEAVE",
        "STUDENT_LEAVE",
        "RESCHEDULE",
        "ROOM_FAULT",
        "EQUIPMENT_FAULT",
        "SUBSTITUTE",
        "CANCEL_SESSION",
        "ENROLLMENT_CHANGE",
        "ADD_SESSION",
    ]
    reason: str = Field(min_length=2, max_length=2000)
    payload: ChangePayload

    @model_validator(mode="after")
    def check_payload(self) -> Self:
        if self.kind in {"TEACHER_LEAVE", "STUDENT_LEAVE", "ROOM_FAULT", "EQUIPMENT_FAULT"}:
            if not self.payload.starts_at or not self.payload.ends_at:
                raise ValueError("请提供请假或故障的开始、结束时间")
        if self.kind != "ADD_SESSION" and not self.payload.session_ids:
            raise ValueError("请选择受影响课次")
        if self.kind == "ADD_SESSION" and not self.payload.request_ids:
            raise ValueError("临时增课必须选择真实且已审核的选课需求")
        return self


class DecisionBody(VersionBody):
    approved: bool
    comment: str = Field(default="", max_length=2000)


class ReasonBody(StrictBody):
    reason: str = Field(min_length=2, max_length=2000)


class ResourceBody(StrictBody):
    id: str | None = Field(default=None, max_length=36)
    version: int = Field(default=1, ge=1)
    values: dict[str, Any]
    features: list[str] | None = Field(default=None, max_length=30)


class CourseFields(StrictBody):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=4000)
    course_group: Literal["MAIN", "INTEREST", "EXTENSION", "QA"] | None = None
    min_age: int | None = Field(default=None, ge=3, le=100)
    max_age: int | None = Field(default=None, ge=3, le=100)
    min_grade: int | None = Field(default=None, ge=0, le=12)
    max_grade: int | None = Field(default=None, ge=0, le=12)
    duration: Literal[35, 45] | None = None
    min_size: int | None = Field(default=None, ge=1, le=300)
    max_size: int | None = Field(default=None, ge=1, le=300)
    status: Literal["OPEN", "CLOSED"] | None = None
    preferred_room: str | None = Field(default=None, max_length=40)


class RoomFields(StrictBody):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    kind: str | None = Field(default=None, min_length=1, max_length=40)
    capacity: int | None = Field(default=None, ge=1, le=300)
    status: Literal["OPEN", "CLOSED", "MAINTENANCE"] | None = None
    notes: str | None = Field(default=None, max_length=2000)


class StudentFields(StrictBody):
    age: int | None = Field(default=None, ge=3, le=100)
    grade: int | None = Field(default=None, ge=0, le=12)
    gender: str | None = Field(default=None, max_length=10)
    notes: str | None = Field(default=None, max_length=2000)


class TeacherFields(StrictBody):
    bio: str | None = Field(default=None, max_length=4000)


class UserBody(StrictBody):
    id: str | None = Field(default=None, max_length=36)
    version: int = Field(default=1, ge=1)
    role: Role
    name: str = Field(min_length=1, max_length=80)
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,60}$")
    phone: str = Field(default="", max_length=40)
    active: bool = True
    password: str | None = Field(default=None, min_length=12, max_length=128)
    age: int | None = Field(default=None, ge=3, le=100)
    grade: int | None = Field(default=None, ge=0, le=12)
    gender: str = Field(default="", max_length=10)
    bio: str = Field(default="", max_length=4000)
    notes: str = Field(default="", max_length=2000)


class AvailabilityBody(StrictBody):
    owner_id: str | None = Field(default=None, max_length=36)
    slots: list[PreferenceBody] = Field(max_length=40)


class RoomAvailabilityBody(VersionBody):
    slots: list[PreferenceBody] = Field(max_length=40)


class QualificationBody(StrictBody):
    course_ids: list[str] = Field(max_length=50)


class RulesBody(StrictBody):
    max_daily: int = Field(ge=1, le=5)
    max_weekly: int = Field(ge=1, le=24)
    max_consecutive: int = Field(ge=1, le=3)
    min_main_daily: int = Field(ge=2, le=50)
    solve_seconds: float = Field(gt=0, le=300)
    require_full_day_coverage: Literal[True] = True


class AttendanceBody(StrictBody):
    session_id: str = Field(max_length=36)
    student_id: str = Field(max_length=36)
    status: Literal["PRESENT", "ABSENT", "LATE", "EXCUSED"]
    notes: str = Field(default="", max_length=2000)


class AttendanceBatchBody(StrictBody):
    records: list[AttendanceBody] = Field(min_length=1, max_length=300)


class FeedbackBody(StrictBody):
    session_id: str = Field(max_length=36)
    student_id: str = Field(max_length=36)
    rating: int = Field(ge=1, le=5)
    content: str = Field(min_length=1, max_length=4000)


class AgentBody(StrictBody):
    message: str = Field(min_length=1, max_length=4000)
    context: dict[str, Any] = Field(default_factory=dict)


class CopyWeekBody(StrictBody):
    source_week: date
    target_week: date


class InviteReviewBody(StrictBody):
    teacher_ids: list[str] = Field(min_length=1, max_length=30)
