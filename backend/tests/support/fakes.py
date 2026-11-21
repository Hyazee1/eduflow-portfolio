from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Self
from uuid import uuid4

from eduflow.domain.entities import Lesson, Snapshot
from eduflow.domain.errors import Conflict, NotFound, StaleVersion


@dataclass
class FixedClock:
    current: datetime = datetime(2026, 10, 30, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current


class MemoryRecords:
    def __init__(self, snapshot: Snapshot) -> None:
        self.source = snapshot
        self.rows: dict[str, dict[str, dict]] = defaultdict(dict)
        self.reservations: str | None = None
        self.fail_reservations = False

    def get(self, entity: str, record_id: str, *, lock: bool = False) -> dict:
        if record_id not in self.rows[entity]:
            raise NotFound("资源不存在")
        return deepcopy(self.rows[entity][record_id])

    def list(self, entity: str, filters: dict | None = None, *, limit: int = 10000) -> list[dict]:
        def matches(row: dict) -> bool:
            return all(
                row.get(key) in value
                if isinstance(value, (list, tuple, set, frozenset))
                else row.get(key) == value
                for key, value in (filters or {}).items()
            )

        return [deepcopy(row) for row in self.rows[entity].values() if matches(row)][:limit]

    def add(self, entity: str, values: dict) -> dict:
        if entity == "change_request_events":
            values = {"sequence": len(self.rows[entity]) + 1, **values}
        record = {
            "id": str(uuid4()),
            "version": 1,
            "organization_id": self.source.organization_id,
            "created_at": datetime(2026, 10, 30, tzinfo=UTC),
            **deepcopy(values),
        }
        if record["id"] in self.rows[entity]:
            raise Conflict("主键重复")
        self.rows[entity][record["id"]] = record
        return deepcopy(record)

    def page(self, entity: str, filters: dict, number: int, size: int, search: str = "") -> dict:
        items = self.list(entity, filters)
        if search:

            def matches(item):
                user = self.rows["users"].get(item.get("user_id"), {})
                course = self.rows["courses"].get(item.get("course_id"), {})
                return (
                    search.casefold()
                    in " ".join(
                        str(v)
                        for v in [
                            item.get("name", ""),
                            item.get("username", ""),
                            item.get("reason", ""),
                            item.get("student_code", ""),
                            item.get("teacher_code", ""),
                            user.get("name", ""),
                            user.get("username", ""),
                            course.get("name", ""),
                        ]
                    ).casefold()
                )

            items = [item for item in items if matches(item)]
        items.sort(
            key=lambda item: (item.get("created_at", ""), item["id"]),
            reverse=entity in {"schedule_versions", "audit_logs", "solve_jobs", "change_requests"},
        )
        return {
            "items": items[(number - 1) * size : number * size],
            "total": len(items),
            "page": number,
            "page_size": size,
        }

    def update(self, entity: str, record_id: str, values: dict, expected: int) -> dict:
        old = self.get(entity, record_id)
        if old["version"] != expected:
            raise StaleVersion("数据已更新")
        self.rows[entity][record_id] = {**old, **deepcopy(values), "version": expected + 1}
        return self.get(entity, record_id)

    def lock(self, key: str) -> None:
        pass

    def organization(self) -> dict:
        return {"id": self.source.organization_id, "config_revision": self.source.config_revision}

    def snapshot(self, week) -> Snapshot:
        return replace(self.source, week=week)

    def lessons(self, version_id: str) -> tuple[Lesson, ...]:
        rows = self.list("course_sessions", {"version_id": version_id})
        output = []
        for row in rows:
            members = sorted(
                self.list("session_enrollments", {"session_id": row["id"]}), key=lambda m: m["student_id"]
            )
            output.append(
                Lesson(
                    row["logical_id"],
                    row["offering_id"],
                    row["course_id"],
                    row["teacher_id"],
                    row["room_id"],
                    row["day"],
                    row["slot_code"],
                    row["start_time"],
                    row["end_time"],
                    tuple(m["student_id"] for m in members),
                    tuple(m["request_id"] for m in members),
                    row["status"],
                )
            )
        return tuple(output)

    def save_lessons(self, version_id: str, lessons: tuple[Lesson, ...]) -> None:
        for lesson in lessons:
            row = self.add(
                "course_sessions",
                {
                    "version_id": version_id,
                    "logical_id": lesson.id,
                    "offering_id": lesson.offering_id,
                    "course_id": lesson.course_id,
                    "teacher_id": lesson.teacher_id,
                    "room_id": lesson.room_id,
                    "day": lesson.day,
                    "slot_code": lesson.slot_code,
                    "start_time": lesson.start,
                    "end_time": lesson.end,
                    "status": lesson.status,
                    "fingerprint": lesson.fingerprint,
                },
            )
            for student, request in zip(lesson.students, lesson.request_ids, strict=True):
                self.add(
                    "session_enrollments",
                    {"session_id": row["id"], "student_id": student, "request_id": request},
                )

    def replace_reservations(self, version_id: str, old_version_id: str | None) -> None:
        if self.fail_reservations:
            raise Conflict("最终资源占用冲突")
        self.reservations = version_id

    def delete_candidate(self, version_id: str, expected: int) -> None:
        version = self.get("schedule_versions", version_id)
        if version["version"] != expected:
            raise StaleVersion("课表版本已更新")
        if (
            version["status"] not in {"DRAFT", "VALIDATED", "CANCELLED"}
            or version.get("published_at")
            or version.get("activated_at")
        ):
            raise Conflict("不可删除已发布的业务历史")
        row_ids = {r["id"] for r in self.list("course_sessions", {"version_id": version_id})}
        for entity, key in (("session_enrollments", "session_id"), ("course_sessions", "id")):
            self.rows[entity] = {k: v for k, v in self.rows[entity].items() if v[key] not in row_ids}
        del self.rows["schedule_versions"][version_id]


class MemoryDatabase:
    def __init__(self, records: MemoryRecords) -> None:
        self.records = records
        self.commits = 0

    def __call__(self, organization_id: str) -> MemoryUnitOfWork:
        if organization_id != self.records.source.organization_id:
            raise NotFound("机构不存在")
        return MemoryUnitOfWork(self)


class MemoryUnitOfWork:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    def __enter__(self) -> Self:
        self.records = deepcopy(self.database.records)
        return self

    def __exit__(self, *args) -> None:
        pass

    def commit(self) -> None:
        self.database.records = deepcopy(self.records)
        self.database.commits += 1
