from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from eduflow.application.candidate_cleanup import deletion_block
from eduflow.application.ports import Clock, Record, RecordStore, UnitOfWorkFactory
from eduflow.domain.authorization import require_owner, require_role
from eduflow.domain.diagnostics import demand_diagnostics
from eduflow.domain.entities import Principal, Snapshot
from eduflow.domain.errors import Forbidden, NotFound
from eduflow.domain.time_rules import BREAKS, SHANGHAI


def safe_user(user: Record) -> Record:
    return fields(user, "id", "organization_id", "version", "username", "name", "role", "phone", "active")


def fields(record: Record, *names: str) -> Record:
    """Public views opt in to fields; future persistence columns stay private."""
    return {name: record[name] for name in names if name in record}


def available_week(today: date, offerings: list[Record]) -> date:
    current = today - timedelta(days=today.weekday())
    return min((item["week"] for item in offerings if item["week"] >= current), default=current)


def page(items: list[Record], page_number: int, page_size: int) -> Record:
    start = (page_number - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": len(items),
        "page": page_number,
        "page_size": page_size,
    }


def change_view(store: RecordStore, actor: Principal, change: Record) -> Record:
    require_owner(actor, change["requester_id"], actor.organization_id)
    result = dict(change)
    result["events"] = sorted(
        store.list("change_request_events", {"change_request_id": change["id"]}),
        key=lambda event: event["sequence"],
    )
    if change["proposal"]:
        proposal = {k: v for k, v in change["proposal"].items() if k not in {"snapshot", "lessons"}}
        if actor.role == "ADMIN":
            proposal["lessons"] = change["proposal"]["lessons"]
        else:
            original = store.lessons(change["base_version_id"]) if change["base_version_id"] else ()
            related = {s.id for s in original if s.teacher_id == actor.id or actor.id in s.students}
            proposal["lessons"] = [
                {k: v for k, v in lesson.items() if k not in {"students", "request_ids"}}
                for lesson in change["proposal"]["lessons"]
                if lesson["id"] in related
            ]
            proposal.pop("metrics", None)
            result["payload"] = {
                k: v for k, v in change["payload"].items() if k not in {"removed_request_ids", "session_ids"}
            }
            result["payload"]["session_ids"] = [
                s for s in change["payload"].get("session_ids", []) if s in related
            ]
        result["proposal"] = proposal
        courses = {c["id"]: c["name"] for c in store.list("courses")}
        teachers = {t["id"]: t["name"] for t in store.list("users", {"role": "TEACHER"})}
        rooms = {r["id"]: r["name"] for r in store.list("rooms")}
        proposal["lessons"] = [
            {
                **lesson,
                "course_name": courses.get(lesson["course_id"], ""),
                "teacher_name": teachers.get(lesson["teacher_id"], ""),
                "room_name": rooms.get(lesson["room_id"], ""),
            }
            for lesson in proposal["lessons"]
        ]
    return result


@dataclass(frozen=True)
class Queries:
    uow: UnitOfWorkFactory
    clock: Clock

    def bootstrap(self, actor: Principal) -> Record:
        with self.uow(actor.organization_id) as transaction:
            store = transaction.records
            org = store.organization()
            offerings = store.list("course_offerings")
            today = self.clock.now().astimezone(SHANGHAI).date()
            default_week = available_week(today, offerings)
            return {
                "organization": {"id": org["id"], "name": org["name"], "timezone": org["timezone"]},
                "default_week": str(default_week),
                "today": str(today),
                "rules": org["rules"],
                "config_revision": org["config_revision"],
                "slots": store.list("time_slot_templates", {"revision": 1}),
                "breaks": list(BREAKS),
                "demo": org["seeded"],
            }

    def catalog(self, actor: Principal, week: date | None = None) -> list[Record]:
        with self.uow(actor.organization_id) as transaction:
            store = transaction.records
            features = store.list("course_features")
            if week is None:
                offerings = store.list("course_offerings")
                today = self.clock.now().astimezone(SHANGHAI).date()
                week = available_week(today, offerings)
            active = store.list("schedule_versions", {"week": week, "status": "ACTIVE"})
            lessons = store.lessons(active[0]["id"]) if active else ()
            users = {u["id"]: u["name"] for u in store.list("users", {"role": "TEACHER"})}
            rooms = {r["id"]: r for r in store.list("rooms")}
            classes = [
                {
                    "id": lesson.id,
                    "course_id": lesson.course_id,
                    "teacher_name": users[lesson.teacher_id],
                    "room_name": rooms[lesson.room_id]["name"],
                    "day": str(lesson.day),
                    "slot_code": lesson.slot_code,
                    "start": lesson.start,
                    "end": lesson.end,
                    "student_count": len(lesson.students),
                    "room_capacity": rooms[lesson.room_id]["capacity"],
                    "status": lesson.status,
                }
                for lesson in lessons
                if lesson.status == "SCHEDULED"
            ]
            return [
                {
                    **(
                        c
                        if actor.role == "ADMIN"
                        else fields(
                            c,
                            "id",
                            "name",
                            "course_group",
                            "description",
                            "min_age",
                            "max_age",
                            "min_grade",
                            "max_grade",
                            "duration",
                            "min_size",
                            "max_size",
                            "status",
                            "preferred_room",
                        )
                    ),
                    "features": [f["feature"] for f in features if f["course_id"] == c["id"]],
                    "week": str(week),
                    "classes": [
                        {
                            **lesson,
                            "remaining_seats": max(
                                0, min(c["max_size"], lesson["room_capacity"]) - lesson["student_count"]
                            ),
                        }
                        for lesson in classes
                        if lesson["course_id"] == c["id"]
                    ],
                    "teacher_name": "、".join(
                        sorted({s["teacher_name"] for s in classes if s["course_id"] == c["id"]})
                    )
                    or "待审核与排课",
                    "room_name": "、".join(
                        sorted({s["room_name"] for s in classes if s["course_id"] == c["id"]})
                    )
                    or "待资源匹配",
                    "provisional": not any(s["course_id"] == c["id"] for s in classes),
                }
                for c in store.list("courses")
                if actor.role == "ADMIN" or c["status"] == "OPEN"
            ]

    def rooms(self, actor: Principal) -> list[Record]:
        require_role(actor, "ADMIN", "TEACHER", "STUDENT")
        with self.uow(actor.organization_id) as transaction:
            return [
                {key: row[key] for key in ("id", "name", "kind", "capacity", "status")}
                for row in transaction.records.list("rooms")
            ]

    def records(
        self,
        actor: Principal,
        entity: str,
        page_number: int = 1,
        page_size: int = 20,
        search: str = "",
        filters: Record | None = None,
    ) -> Record:
        require_role(actor, "ADMIN")
        allowed = {
            "users",
            "student_profiles",
            "teacher_profiles",
            "courses",
            "rooms",
            "teacher_qualifications",
            "teacher_decisions",
            "course_offerings",
            "enrollment_requests",
            "schedule_versions",
            "change_requests",
            "audit_logs",
            "solve_jobs",
            "outbox_events",
        }
        if entity not in allowed:
            raise Forbidden("不允许直接查询此资源")
        with self.uow(actor.organization_id) as transaction:
            store = transaction.records
            result = store.page(entity, filters or {}, page_number, page_size, search)
            items = result["items"]
            users = (
                {u["id"]: u for u in store.list("users")}
                if entity
                in {
                    "student_profiles",
                    "teacher_profiles",
                    "enrollment_requests",
                    "change_requests",
                    "teacher_decisions",
                }
                else {}
            )
            courses = {c["id"]: c for c in store.list("courses")}
            rooms_features = store.list("room_features") if entity == "rooms" else []
            course_features = store.list("course_features") if entity == "courses" else []
            offerings = (
                {o["id"]: o for o in store.list("course_offerings")} if entity == "teacher_decisions" else {}
            )
            for item in items:
                item.pop("password_hash", None)
                item.pop("snapshot", None)
                if entity in {"student_profiles", "teacher_profiles"}:
                    user = users.get(item["user_id"], {})
                    item.update({k: user.get(k) for k in ("name", "username", "phone", "active")})
                    item["user_version"] = user.get("version")
                    if entity == "teacher_profiles":
                        item["course_ids"] = [
                            q["course_id"]
                            for q in store.list("teacher_qualifications", {"teacher_id": item["user_id"]})
                        ]
                if "student_id" in item:
                    item["student_name"] = users.get(item["student_id"], {}).get("name", "")
                if "course_id" in item:
                    item["course_name"] = courses.get(item["course_id"], {}).get("name", "")
                if entity == "rooms":
                    item["features"] = [f["feature"] for f in rooms_features if f["room_id"] == item["id"]]
                if entity == "courses":
                    item["features"] = [f["feature"] for f in course_features if f["course_id"] == item["id"]]
                    item["safety_features"] = [
                        f["feature"]
                        for f in course_features
                        if f["course_id"] == item["id"] and f["safety_required"]
                    ]
                if entity == "teacher_decisions":
                    offering = offerings[item["offering_id"]]
                    item.update(
                        {
                            "teacher_name": users[item["teacher_id"]]["name"],
                            "week": offering["week"],
                            "course_name": courses[offering["course_id"]]["name"],
                            "offering_status": offering["status"],
                        }
                    )
                if entity == "change_requests":
                    item.pop("proposal", None)
                    item["requester_name"] = users.get(item["requester_id"], {}).get("name", "")
            return result

    def enrollments(self, actor: Principal) -> list[Record]:
        require_role(actor, "STUDENT")
        with self.uow(actor.organization_id) as transaction:
            store = transaction.records
            courses = {c["id"]: c for c in store.list("courses")}
            items = store.list("enrollment_requests", {"student_id": actor.id})
            for item in items:
                item["course_name"] = courses[item["course_id"]]["name"]
                item["preferences"] = store.list("enrollment_time_preferences", {"request_id": item["id"]})
            return items

    def reviews(self, actor: Principal) -> list[Record]:
        require_role(actor, "TEACHER")
        with self.uow(actor.organization_id) as transaction:
            store = transaction.records
            result = []
            active_versions = [
                version["id"] for version in store.list("schedule_versions", {"status": "ACTIVE"})
            ]
            active_offerings = {
                row["offering_id"]
                for row in store.list(
                    "course_sessions",
                    {"version_id": active_versions, "teacher_id": actor.id, "status": "SCHEDULED"},
                )
            }
            for review in store.list("teacher_decisions", {"teacher_id": actor.id}):
                offering = store.get("course_offerings", review["offering_id"])
                if offering["status"] == "MERGED":
                    continue
                course = store.get("courses", offering["course_id"])
                requests = store.list(
                    "enrollment_requests",
                    {"offering_id": offering["id"], "status": ["APPROVED", "PENDING_REVIEW"]},
                )
                profiles = store.list("student_profiles", {"user_id": [r["student_id"] for r in requests]})
                preferences = store.list(
                    "enrollment_time_preferences", {"request_id": [r["id"] for r in requests]}
                )
                result.append(
                    {
                        **review,
                        "offering": offering,
                        "course": course,
                        "active_teaching": offering["id"] in active_offerings,
                        "student_count": len(requests),
                        "age_distribution": dict(Counter(p["age"] for p in profiles)),
                        "grade_distribution": dict(Counter(p["grade"] for p in profiles)),
                        "preferences": dict(
                            Counter(f"{p['day_index']}:{p['slot_code']}" for p in preferences)
                        ),
                        "required_features": store.list("course_features", {"course_id": course["id"]}),
                    }
                )
            return result

    def schedule(
        self,
        actor: Principal,
        week: date,
        version_id: str | None = None,
        owner_id: str | None = None,
        room_id: str | None = None,
    ) -> Record:
        owner = owner_id or actor.id
        if owner != actor.id:
            require_role(actor, "ADMIN")
        if room_id:
            require_role(actor, "ADMIN")
        with self.uow(actor.organization_id) as transaction:
            store = transaction.records
            versions = store.list("schedule_versions", {"week": week})
            if actor.role != "ADMIN":
                versions = [
                    v for v in versions if v["status"] in {"ACTIVE", "PUBLISHED_PENDING_CONFIRMATION"}
                ]
            users = {u["id"]: u for u in store.list("users")}
            courses = {c["id"]: c for c in store.list("courses")}
            rooms = {r["id"]: r for r in store.list("rooms")}
            result = []
            visible_versions = []
            version_slots = {}
            for version in versions:
                rows = store.list("course_sessions", {"version_id": version["id"]})
                own_confirmations = store.list(
                    "schedule_confirmations",
                    {
                        "session_id": [r["id"] for r in rows],
                        "user_id": owner,
                    },
                )
                recipient_sessions = {c["session_id"] for c in own_confirmations}
                members = store.list("session_enrollments", {"session_id": [r["id"] for r in rows]})
                memberships: dict[str, list[Record]] = {}
                for member in members:
                    memberships.setdefault(member["session_id"], []).append(member)
                relevant = [
                    row
                    for row in rows
                    if (
                        actor.role == "ADMIN"
                        and not owner_id
                        or row["teacher_id"] == owner
                        or any(m["student_id"] == owner for m in memberships.get(row["id"], []))
                        or row["id"] in recipient_sessions
                    )
                    and (not room_id or row["room_id"] == room_id)
                ]
                if not relevant and actor.role != "ADMIN":
                    continue
                version_slots[version["id"]] = [
                    {**asdict(slot), "duration_minutes": slot.minutes}
                    for slot in Snapshot.from_dict(version["snapshot"]).slots
                ]
                visible_versions.append(
                    {
                        k: version[k]
                        for k in (
                            "id",
                            "week",
                            "status",
                            "version",
                            "solver_status",
                            "confirmation_deadline",
                            "created_at",
                        )
                    }
                )
                if version_id and version_id != version["id"]:
                    continue
                for row in relevant:
                    roster = memberships.get(row["id"], [])
                    own_confirm = store.list(
                        "schedule_confirmations", {"session_id": row["id"], "user_id": actor.id}
                    )
                    result.append(
                        {
                            **row,
                            "record_id": row["id"],
                            "id": row["logical_id"],
                            "version_status": version["status"],
                            "course_name": courses[row["course_id"]]["name"],
                            "course_group": courses[row["course_id"]]["course_group"],
                            "teacher_name": users[row["teacher_id"]]["name"],
                            "room_name": rooms[row["room_id"]]["name"],
                            "student_count": len(roster),
                            "students": [
                                m["student_id"]
                                for m in roster
                                if actor.role != "STUDENT" or m["student_id"] == actor.id
                            ],
                            "confirmation": own_confirm[0]["decision"] if own_confirm else None,
                        }
                    )
            if version_id and not any(v["id"] == version_id for v in visible_versions):
                raise NotFound("课表版本不存在或不属于本人")
            preferred = next(
                (v for v in visible_versions if v["status"] == "PUBLISHED_PENDING_CONFIRMATION"),
                next(
                    (v for v in visible_versions if v["status"] == "ACTIVE"),
                    visible_versions[-1] if visible_versions else {},
                ),
            )
            return {
                "week": str(week),
                "versions": visible_versions,
                "sessions": result,
                "slots": version_slots.get(
                    version_id or preferred.get("id"), store.list("time_slot_templates", {"revision": 1})
                ),
                "version_slots": version_slots,
                "breaks": list(BREAKS),
            }

    def version(self, actor: Principal, version_id: str) -> Record:
        require_role(actor, "ADMIN")
        with self.uow(actor.organization_id) as transaction:
            store = transaction.records
            version = store.get("schedule_versions", version_id)
            version["delete_block_reason"] = deletion_block(store, version)
            version["can_delete"] = not version["delete_block_reason"]
            version["pending_request_count"] = store.page(
                "enrollment_requests", {"week": version["week"], "status": "PENDING_REVIEW"}, 1, 1
            )["total"]
            original = Snapshot.from_dict(version.pop("snapshot"))
            input_issues = demand_diagnostics(original)
            version["requires_resolve"] = (
                store.organization()["rules"].get("require_full_day_coverage", True)
                and not original.rules.require_full_day_coverage
            )
            rows = store.list("course_sessions", {"version_id": version_id})
            row_ids = {r["id"]: r["logical_id"] for r in rows}
            users = {u["id"]: u for u in store.list("users")}
            confirmations = store.list("schedule_confirmations", {"session_id": list(row_ids)})
            for confirmation in confirmations:
                confirmation["user_name"] = users[confirmation["user_id"]]["name"]
                confirmation["role"] = users[confirmation["user_id"]]["role"]
                confirmation["logical_id"] = row_ids[confirmation["session_id"]]
            version["confirmations"] = confirmations
            version["time_templates"] = [asdict(s) for s in original.slots]
            version["lessons"] = [s.to_dict() for s in store.lessons(version_id)]
            courses = {c.id: c for c in original.courses}
            demands = {d.id: d for d in original.demands}
            details = []
            for request_id in version["report"].get("unmet_requests", []):
                demand = demands.get(request_id)
                if not demand:
                    continue
                course = courses[demand.course_id]
                qualified = [
                    t
                    for t in original.teachers
                    if t.id in demand.approved_teachers and course.id in t.qualifications
                ]
                rooms = [
                    r
                    for r in original.rooms
                    if r.status == "OPEN" and course.features <= r.features and r.capacity >= course.min_size
                ]
                reason = input_issues.get(request_id) or (
                    "没有已同意且具备资质的教师"
                    if not qualified
                    else "没有满足设备与容量要求的教室"
                    if not rooms
                    else "共同可用时间、最小开班人数或全局资源与负载约束未能同时满足"
                )
                details.append(
                    {
                        "request_id": request_id,
                        "student_name": users.get(demand.student_id, {}).get("name", ""),
                        "course_name": course.name,
                        "reason": reason,
                    }
                )
            version["unmet_details"] = details
            return version

    def job(self, actor: Principal, job_id: str) -> Record:
        require_role(actor, "ADMIN")
        with self.uow(actor.organization_id) as transaction:
            item = transaction.records.get("solve_jobs", job_id)
            item.pop("snapshot", None)
            return item

    def changes(self, actor: Principal, change_id: str | None = None) -> Any:
        with self.uow(actor.organization_id) as transaction:
            store = transaction.records
            if change_id:
                return change_view(store, actor, store.get("change_requests", change_id))
            filters = {} if actor.role == "ADMIN" else {"requester_id": actor.id}
            result = store.list("change_requests", filters)
            return [change_view(store, actor, item) for item in reversed(result)]

    def notifications(self, actor: Principal) -> list[Record]:
        with self.uow(actor.organization_id) as transaction:
            return list(reversed(transaction.records.list("notifications", {"user_id": actor.id}, limit=500)))

    def profile(self, actor: Principal) -> Record:
        with self.uow(actor.organization_id) as transaction:
            store = transaction.records
            user = safe_user(store.get("users", actor.id))
            profile_entity = "student_profiles" if actor.role == "STUDENT" else "teacher_profiles"
            profile = store.list(profile_entity, {"user_id": actor.id}) if actor.role != "ADMIN" else []
            return {
                "user": user,
                "profile": fields(
                    profile[0],
                    "id",
                    "user_id",
                    "version",
                    "student_code",
                    "teacher_code",
                    "gender",
                    "age",
                    "grade",
                    "bio",
                )
                if profile
                else None,
                "availability": store.list("availability_rules", {"owner_id": actor.id}),
                "qualifications": store.list("teacher_qualifications", {"teacher_id": actor.id})
                if actor.role == "TEACHER"
                else [],
                "feedback": store.list("course_feedback", {"student_id": actor.id})
                if actor.role == "STUDENT"
                else [],
            }

    def room_availability(self, actor: Principal, room_id: str) -> Record:
        require_role(actor, "ADMIN")
        with self.uow(actor.organization_id) as transaction:
            store = transaction.records
            return {
                "room": store.get("rooms", room_id),
                "availability": store.list("availability_rules", {"owner_id": room_id, "owner_kind": "ROOM"}),
                "slots": store.list("time_slot_templates", {"revision": 1}),
            }

    def roster(self, actor: Principal, session_id: str) -> Record:
        require_role(actor, "ADMIN", "TEACHER")
        with self.uow(actor.organization_id) as transaction:
            store = transaction.records
            session = store.get("course_sessions", session_id)
            require_owner(actor, session["teacher_id"], actor.organization_id)
            members = store.list("session_enrollments", {"session_id": session_id})
            students = []
            for member in members:
                user = store.get("users", member["student_id"])
                profile = store.list("student_profiles", {"user_id": user["id"]})[0]
                students.append(
                    {
                        "id": user["id"],
                        "name": user["name"],
                        "age": profile["age"],
                        "grade": profile["grade"],
                        "attendance": store.list(
                            "attendance_records", {"session_id": session_id, "student_id": user["id"]}
                        ),
                        "feedback": store.list(
                            "course_feedback", {"session_id": session_id, "student_id": user["id"]}
                        ),
                    }
                )
            return {"session": session, "students": students}

    def analytics(self, actor: Principal, week: date) -> Record:
        require_role(actor, "ADMIN")
        with self.uow(actor.organization_id) as transaction:
            store = transaction.records
            versions = store.list("schedule_versions", {"week": week})
            chosen = next(
                (v for v in versions if v["status"] == "ACTIVE"), versions[-1] if versions else None
            )
            return {
                "student_count": len(store.list("users", {"role": "STUDENT", "active": True})),
                "teacher_count": len(store.list("users", {"role": "TEACHER", "active": True})),
                "room_count": len(store.list("rooms")),
                "pending_reviews": len(store.list("teacher_decisions", {"decision": "PENDING"})),
                "pending_changes": len(
                    store.list("change_requests", {"status": ["PENDING_ADMIN", "WAITING_CONFIRMATION"]})
                ),
                "enrollment_count": len(store.list("enrollment_requests", {"week": week})),
                "active_version": chosen["id"] if chosen else None,
                "version_status": chosen["status"] if chosen else None,
                "report": chosen["report"] if chosen else None,
                "teachers": [
                    {"id": t["id"], "name": t["name"]} for t in store.list("users", {"role": "TEACHER"})
                ],
                "rooms": [{"id": r["id"], "name": r["name"]} for r in store.list("rooms")],
                "sessions": [s.to_dict() for s in store.lessons(chosen["id"])] if chosen else [],
            }
