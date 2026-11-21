from __future__ import annotations

from collections import defaultdict
from datetime import date
from types import TracebackType
from typing import Any, Self
from uuid import uuid4

from sqlalchemy import Date, DateTime, String, cast, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from eduflow.adapters.models import ENTITY_MODELS, Organization, ResourceReservation
from eduflow.application.ports import Record
from eduflow.domain.entities import Course, Demand, Lesson, Room, Snapshot, Student, Teacher
from eduflow.domain.errors import Conflict, NotFound, StaleVersion
from eduflow.domain.time_rules import Rules, Slot, slot_key, week_days


def plain(row: Any) -> Record:
    return {column.key: getattr(row, column.key) for column in row.__table__.columns}


class SqlRecords:
    def __init__(self, session: Session, organization_id: str) -> None:
        self.session = session
        self.organization_id = organization_id

    def _model(self, entity: str) -> Any:
        if entity not in ENTITY_MODELS:
            raise NotFound("未知资源类型")
        return ENTITY_MODELS[entity]

    def _values(self, model: Any, values: Record) -> Record:
        output = dict(values)
        for name, value in output.items():
            if name not in model.__table__.c:
                raise ValueError(f"Unknown field {name}")
            column = model.__table__.c[name]
            if isinstance(value, str) and isinstance(column.type, DateTime):
                from datetime import datetime

                output[name] = datetime.fromisoformat(value)
            elif isinstance(value, str) and isinstance(column.type, Date):
                output[name] = date.fromisoformat(value)
        return output

    def get(self, entity: str, record_id: str, *, lock: bool = False) -> Record:
        model = self._model(entity)
        query = select(model).where(model.organization_id == self.organization_id, model.id == record_id)
        if lock:
            query = query.with_for_update()
        row = self.session.scalar(query)
        if row is None:
            raise NotFound("资源不存在或不在当前授权范围")
        return plain(row)

    def list(self, entity: str, filters: Record | None = None, *, limit: int = 10000) -> list[Record]:
        model = self._model(entity)
        query = select(model).where(model.organization_id == self.organization_id)
        for field, value in (filters or {}).items():
            column = getattr(model, field)
            if isinstance(value, (list, tuple, set, frozenset)):
                query = query.where(column.in_(value))
            elif value is None:
                query = query.where(column.is_(None))
            else:
                query = query.where(column == self._values(model, {field: value})[field])
        ordering = (
            (model.ordinal, model.id) if entity == "time_slot_templates" else (model.created_at, model.id)
        )
        return [plain(row) for row in self.session.scalars(query.order_by(*ordering).limit(limit))]

    def add(self, entity: str, values: Record) -> Record:
        model = self._model(entity)
        row = model(
            **self._values(
                model,
                {
                    **values,
                    "organization_id": self.organization_id,
                    "id": values.get("id", str(uuid4())),
                },
            )
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as error:
            raise Conflict("数据违反唯一约束、机构关联或资源占用规则") from error
        return plain(row)

    def page(self, entity: str, filters: Record, number: int, size: int, search: str = "") -> Record:
        model = self._model(entity)
        query = select(model).where(model.organization_id == self.organization_id)
        for field, value in filters.items():
            column = getattr(model, field)
            query = query.where(
                column.in_(value) if isinstance(value, (list, tuple, set)) else column == value
            )
        columns = [
            getattr(model, name)
            for name in ("name", "username", "student_code", "teacher_code", "reason")
            if hasattr(model, name)
        ]
        if entity in {"student_profiles", "teacher_profiles"}:
            user = self._model("users")
            query = query.join(
                user, (user.id == model.user_id) & (user.organization_id == self.organization_id)
            )
            columns += [user.name, user.username]
        if entity == "enrollment_requests":
            course = self._model("courses")
            query = query.join(
                course, (course.id == model.course_id) & (course.organization_id == self.organization_id)
            )
            columns += [course.name]
        if search and columns:
            needle = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            query = query.where(or_(*(cast(column, String).ilike(needle, escape="\\") for column in columns)))
        total = self.session.scalar(select(func.count()).select_from(query.subquery())) or 0
        descending = entity in {"schedule_versions", "audit_logs", "solve_jobs", "change_requests"}
        order = [model.created_at.desc(), model.id.desc()] if descending else [model.created_at, model.id]
        rows = self.session.scalars(query.order_by(*order).offset((number - 1) * size).limit(size))
        return {"items": [plain(row) for row in rows], "total": total, "page": number, "page_size": size}

    def update(self, entity: str, record_id: str, values: Record, expected: int) -> Record:
        model = self._model(entity)
        protected = {"id", "organization_id", "created_at", "created_by", "version"}
        if protected & values.keys():
            raise ValueError("Attempt to modify immutable fields")
        statement = (
            update(model)
            .where(
                model.organization_id == self.organization_id,
                model.id == record_id,
                model.version == expected,
            )
            .values(**self._values(model, values), version=expected + 1)
            .execution_options(synchronize_session=False)
        )
        try:
            count = 1 if self.session.scalar(statement.returning(model.id)) is not None else 0
            self.session.flush()
        except IntegrityError as error:
            raise Conflict("更新违反唯一约束、机构关联或资源占用规则") from error
        if count != 1:
            raise StaleVersion("数据已被修改，请刷新后重试")
        self.session.expire_all()
        return self.get(entity, record_id)

    def remove_links(self, entity: str, filters: Record) -> None:
        allowed = {
            "teacher_qualifications",
            "room_features",
            "course_features",
            "availability_rules",
            "enrollment_time_preferences",
            "offering_slots",
            "course_sessions",
            "session_enrollments",
            "schedule_confirmations",
        }
        if entity not in allowed or not filters:
            raise ValueError("Only explicitly scoped link replacement is permitted")
        model = self._model(entity)
        statement = delete(model).where(model.organization_id == self.organization_id)
        for name, value in filters.items():
            statement = statement.where(getattr(model, name) == value)
        self.session.execute(statement)
        self.session.flush()

    def organization(self) -> Record:
        row = self.session.get(Organization, self.organization_id)
        if row is None:
            raise NotFound("机构不存在")
        return plain(row)

    def delete_candidate(self, version_id: str, expected: int) -> None:
        version = self.get("schedule_versions", version_id, lock=True)
        if version["version"] != expected:
            raise StaleVersion("课表版本已更新")
        if (
            version["status"] not in {"DRAFT", "VALIDATED", "CANCELLED"}
            or version["published_at"]
            or version["activated_at"]
        ):
            raise Conflict("不可删除已发布的业务历史")
        for row in self.list("course_sessions", {"version_id": version_id}):
            self.remove_links("session_enrollments", {"session_id": row["id"]})
        self.remove_links("course_sessions", {"version_id": version_id})
        model = self._model("schedule_versions")
        self.session.execute(
            delete(model).where(
                model.id == version_id,
                model.organization_id == self.organization_id,
                model.version == expected,
            )
        )
        self.session.flush()

    def set_rules(self, rules: Record) -> None:
        row = self.session.get(Organization, self.organization_id, with_for_update=True)
        if row is None:
            raise NotFound("机构不存在")
        row.rules = rules
        row.config_revision += 1
        self.session.flush()

    def lock(self, key: str) -> None:
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{self.organization_id}:{key}"},
        )

    def snapshot(self, week: date) -> Snapshot:
        days = week_days(week)
        org = self.organization()
        templates = self.list("time_slot_templates", {"revision": 1})
        slots = tuple(
            Slot(s["code"], s["start"], s["end"], s["kind"], s["period"], s["ordinal"])
            for s in sorted(templates, key=lambda s: s["ordinal"])
        )
        availability: dict[str, set[str]] = defaultdict(set)
        for rule in self.list("availability_rules", limit=50000):
            availability[rule["owner_id"]].add(f"{rule['day_index']}:{rule['slot_code']}")
        for exception in self.list("availability_exceptions"):
            for day in days:
                for slot in slots:
                    start, end = slot.at(day)
                    if start < exception["ends_at"] and exception["starts_at"] < end:
                        availability[exception["owner_id"]].discard(slot_key(day, slot.code))
        active_users = {u["id"]: u for u in self.list("users", {"active": True})}
        qualifications: dict[str, set[str]] = defaultdict(set)
        for q in self.list("teacher_qualifications"):
            qualifications[q["teacher_id"]].add(q["course_id"])
        features: dict[str, set[str]] = defaultdict(set)
        for feature in self.list("room_features"):
            features[feature["room_id"]].add(feature["feature"])
        requirements: dict[str, set[str]] = defaultdict(set)
        for feature in self.list("course_features"):
            requirements[feature["course_id"]].add(feature["feature"])
        accepted: dict[str, set[str]] = defaultdict(set)
        offering_ids = [offering["id"] for offering in self.list("course_offerings", {"week": week})]
        requests = self.list("enrollment_requests", {"week": week, "status": "APPROVED"})
        for decision in self.list("teacher_decisions", {"decision": "ACCEPTED", "offering_id": offering_ids}):
            if decision["teacher_id"] in active_users:
                accepted[decision["offering_id"]].add(decision["teacher_id"])
        allowed: dict[str, set[str]] = defaultdict(set)
        for allowed_slot in self.list("offering_slots", {"offering_id": offering_ids}):
            allowed[allowed_slot["offering_id"]].add(
                f"{allowed_slot['day_index']}:{allowed_slot['slot_code']}"
            )
        preferences: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for preference in self.list(
            "enrollment_time_preferences", {"request_id": [d["id"] for d in requests]}
        ):
            preferences[preference["request_id"]].append(
                (preference["rank"], f"{preference['day_index']}:{preference['slot_code']}")
            )
        return Snapshot(
            organization_id=self.organization_id,
            week=week,
            slots=slots,
            students=tuple(
                Student(s["user_id"], s["age"], s["grade"], frozenset(availability[s["user_id"]]))
                for s in self.list("student_profiles")
                if s["user_id"] in active_users
            ),
            teachers=tuple(
                Teacher(
                    t["user_id"],
                    frozenset(qualifications[t["user_id"]]),
                    frozenset(availability[t["user_id"]]),
                )
                for t in self.list("teacher_profiles")
                if t["user_id"] in active_users
            ),
            rooms=tuple(
                Room(
                    r["id"],
                    r["capacity"],
                    frozenset(features[r["id"]]),
                    frozenset(availability[r["id"]]),
                    r["status"],
                    r["kind"],
                )
                for r in self.list("rooms")
            ),
            courses=tuple(
                Course(
                    c["id"],
                    c["name"],
                    c["course_group"],
                    c["min_age"],
                    c["max_age"],
                    c["min_grade"],
                    c["max_grade"],
                    c["min_size"],
                    c["max_size"],
                    frozenset(requirements[c["id"]]),
                    (c["preferred_room"],),
                    c["duration"],
                    c["status"],
                )
                for c in self.list("courses")
            ),
            demands=tuple(
                Demand(
                    d["id"],
                    d["student_id"],
                    d["course_id"],
                    d["offering_id"],
                    tuple(key for _, key in sorted(preferences[d["id"]])),
                    frozenset(accepted[d["offering_id"]]),
                    frozenset(allowed[d["offering_id"]]),
                    d["priority"],
                    d["status"],
                )
                for d in requests
            ),
            rules=Rules(**org["rules"]),
            config_revision=org["config_revision"],
        )

    def lessons(self, version_id: str) -> tuple[Lesson, ...]:
        self.get("schedule_versions", version_id)
        sessions = self.list("course_sessions", {"version_id": version_id})
        members: dict[str, list[Record]] = defaultdict(list)
        for member in self.list("session_enrollments", {"session_id": [s["id"] for s in sessions]}):
            members[member["session_id"]].append(member)
        return tuple(
            Lesson(
                s["logical_id"],
                s["offering_id"],
                s["course_id"],
                s["teacher_id"],
                s["room_id"],
                s["day"],
                s["slot_code"],
                s["start_time"],
                s["end_time"],
                tuple(m["student_id"] for m in sorted(members[s["id"]], key=lambda m: m["student_id"])),
                tuple(m["request_id"] for m in sorted(members[s["id"]], key=lambda m: m["student_id"])),
                s["status"],
            )
            for s in sessions
        )

    def save_lessons(self, version_id: str, lessons: tuple[Lesson, ...]) -> None:
        for lesson in lessons:
            row = self.add(
                "course_sessions",
                {
                    "logical_id": lesson.id,
                    "version_id": version_id,
                    "offering_id": lesson.offering_id,
                    "course_id": lesson.course_id,
                    "teacher_id": lesson.teacher_id,
                    "room_id": lesson.room_id,
                    "day": lesson.day,
                    "slot_code": lesson.slot_code,
                    "start_time": lesson.start,
                    "end_time": lesson.end,
                    "fingerprint": lesson.fingerprint,
                    "status": lesson.status,
                },
            )
            for student_id, request_id in zip(lesson.students, lesson.request_ids, strict=True):
                self.add(
                    "session_enrollments",
                    {
                        "session_id": row["id"],
                        "student_id": student_id,
                        "request_id": request_id,
                    },
                )

    def replace_reservations(self, version_id: str, old_version_id: str | None) -> None:
        if old_version_id:
            old_sessions = self.list("course_sessions", {"version_id": old_version_id})
            self.session.execute(
                update(ResourceReservation)
                .where(
                    ResourceReservation.organization_id == self.organization_id,
                    ResourceReservation.session_id.in_([s["id"] for s in old_sessions]),
                )
                .values(is_active=False)
            )
            self.session.flush()
        row_ids = {s["logical_id"]: s["id"] for s in self.list("course_sessions", {"version_id": version_id})}
        templates = {
            s.code: s for s in self.snapshot(self.get("schedule_versions", version_id)["week"]).slots
        }
        for lesson in self.lessons(version_id):
            if lesson.status in {"CANCELLED", "SUSPENDED"}:
                continue
            start, end = templates[lesson.slot_code].at(lesson.day)
            resources = [("TEACHER", lesson.teacher_id), ("ROOM", lesson.room_id)]
            resources += [("STUDENT", student_id) for student_id in lesson.students]
            for kind, resource_id in resources:
                self.session.add(
                    ResourceReservation(
                        organization_id=self.organization_id,
                        session_id=row_ids[lesson.id],
                        resource_kind=kind,
                        resource_id=resource_id,
                        user_id=resource_id if kind != "ROOM" else None,
                        room_id=resource_id if kind == "ROOM" else None,
                        during=Range(start, end, bounds="[)"),
                        is_active=True,
                    )
                )
        try:
            self.session.flush()
        except IntegrityError as error:
            raise Conflict("资源最终复检失败：存在并发时间占用，原课表保持有效") from error


class SqlUnitOfWork:
    def __init__(self, factory: sessionmaker[Session], organization_id: str) -> None:
        self._factory = factory
        self._organization_id = organization_id

    def __enter__(self) -> Self:
        self._session = self._factory()
        self.records = SqlRecords(self._session, self._organization_id)
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self._session.rollback()
        self._session.close()

    def commit(self) -> None:
        try:
            self._session.commit()
        except IntegrityError as error:
            raise Conflict("提交失败：并发更新或资源约束冲突") from error
