from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from eduflow.domain.time_rules import Rules, Slot


@dataclass(frozen=True, slots=True)
class Principal:
    id: str
    organization_id: str
    role: str
    name: str = ""


@dataclass(frozen=True, slots=True)
class Student:
    id: str
    age: int
    grade: int
    available: frozenset[str]


@dataclass(frozen=True, slots=True)
class Teacher:
    id: str
    qualifications: frozenset[str]
    available: frozenset[str]


@dataclass(frozen=True, slots=True)
class Room:
    id: str
    capacity: int
    features: frozenset[str]
    available: frozenset[str]
    status: str = "OPEN"
    kind: str = "GENERAL"


@dataclass(frozen=True, slots=True)
class Course:
    id: str
    name: str
    group: str
    min_age: int
    max_age: int
    min_grade: int
    max_grade: int
    min_size: int
    max_size: int
    features: frozenset[str]
    preferred_rooms: tuple[str, ...] = ()
    duration: int = 45
    status: str = "OPEN"

    def accepts(self, student: Student) -> bool:
        return (
            self.min_age <= student.age <= self.max_age and self.min_grade <= student.grade <= self.max_grade
        )


@dataclass(frozen=True, slots=True)
class Demand:
    id: str
    student_id: str
    course_id: str
    offering_id: str
    preferred: tuple[str, ...]
    approved_teachers: frozenset[str]
    allowed: frozenset[str]
    priority: int = 1
    status: str = "APPROVED"
    allowed_rooms: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Lesson:
    id: str
    offering_id: str
    course_id: str
    teacher_id: str
    room_id: str
    day: date
    slot_code: str
    start: str
    end: str
    students: tuple[str, ...]
    request_ids: tuple[str, ...]
    status: str = "SCHEDULED"

    @property
    def fingerprint(self) -> str:
        data = asdict(self)
        data["students"] = sorted(self.students)
        data["request_ids"] = sorted(self.request_ids)
        return hashlib.sha256(json.dumps(data, default=str, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["day"] = self.day.isoformat()
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Lesson:
        return cls(
            **{
                **value,
                "day": date.fromisoformat(value["day"]),
                "students": tuple(value["students"]),
                "request_ids": tuple(value["request_ids"]),
            }
        )


@dataclass(frozen=True, slots=True)
class Snapshot:
    organization_id: str
    week: date
    slots: tuple[Slot, ...]
    students: tuple[Student, ...]
    teachers: tuple[Teacher, ...]
    rooms: tuple[Room, ...]
    courses: tuple[Course, ...]
    demands: tuple[Demand, ...]
    rules: Rules
    locked: tuple[Lesson, ...] = ()
    baseline: tuple[Lesson, ...] = ()
    config_revision: int = 1

    def to_dict(self) -> dict[str, Any]:
        def default(value: Any) -> Any:
            if isinstance(value, (set, frozenset)):
                return sorted(value)
            if isinstance(value, date):
                return value.isoformat()
            raise TypeError(type(value).__name__)

        return json.loads(json.dumps(asdict(self), default=default))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Snapshot:
        return cls(
            organization_id=data["organization_id"],
            week=date.fromisoformat(data["week"]),
            slots=tuple(Slot(**s) for s in data["slots"]),
            students=tuple(
                Student(**{**s, "available": frozenset(s["available"])}) for s in data["students"]
            ),
            teachers=tuple(
                Teacher(
                    **{
                        **t,
                        "available": frozenset(t["available"]),
                        "qualifications": frozenset(t["qualifications"]),
                    }
                )
                for t in data["teachers"]
            ),
            rooms=tuple(
                Room(**{**r, "available": frozenset(r["available"]), "features": frozenset(r["features"])})
                for r in data["rooms"]
            ),
            courses=tuple(
                Course(
                    **{
                        **c,
                        "features": frozenset(c["features"]),
                        "preferred_rooms": tuple(c["preferred_rooms"]),
                    }
                )
                for c in data["courses"]
            ),
            demands=tuple(
                Demand(
                    **{
                        **d,
                        "preferred": tuple(d["preferred"]),
                        "approved_teachers": frozenset(d["approved_teachers"]),
                        "allowed": frozenset(d["allowed"]),
                        "allowed_rooms": frozenset(d.get("allowed_rooms", [])),
                    }
                )
                for d in data["demands"]
            ),
            # Missing fields belong to a historical, pre-coverage snapshot.
            rules=Rules(require_full_day_coverage=False, **data["rules"])
            if "require_full_day_coverage" not in data["rules"]
            else Rules(**data["rules"]),
            locked=tuple(Lesson.from_dict(s) for s in data.get("locked", [])),
            baseline=tuple(Lesson.from_dict(s) for s in data.get("baseline", [])),
            config_revision=data.get("config_revision", 1),
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    message: str
    session_ids: tuple[str, ...] = ()
    resource_id: str = ""


@dataclass(frozen=True, slots=True)
class SolveResult:
    status: str
    lessons: tuple[Lesson, ...]
    wall_seconds: float
    objective: float | None = None
    bound: float | None = None
    diagnostics: tuple[str, ...] = ()
    unmet_requests: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
