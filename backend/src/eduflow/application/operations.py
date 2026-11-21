from dataclasses import asdict, replace
from datetime import datetime

from eduflow.application.commands import CommandHandler, audit, notify
from eduflow.application.ports import Record, RecordStore
from eduflow.domain.authorization import require_owner, require_role
from eduflow.domain.entities import Principal
from eduflow.domain.errors import BusinessError, Conflict, Forbidden
from eduflow.domain.metrics import schedule_metrics
from eduflow.domain.time_rules import SHANGHAI, Rules
from eduflow.domain.validator import validate_schedule

EDIT_FIELDS = {
    "courses": {
        "name",
        "description",
        "course_group",
        "min_age",
        "max_age",
        "min_grade",
        "max_grade",
        "duration",
        "min_size",
        "max_size",
        "status",
        "preferred_room",
    },
    "rooms": {"name", "kind", "capacity", "status", "notes"},
    "student_profiles": {"age", "grade", "gender", "notes"},
    "teacher_profiles": {"bio"},
}


def protect_active(store: RecordStore) -> None:
    for version in store.list("schedule_versions", {"status": "ACTIVE"}):
        violations = validate_schedule(store.snapshot(version["week"]), store.lessons(version["id"]))
        if violations:
            raise Conflict(
                "此修改会破坏已生效课表，请先通过异常工作流调整课程", details=[asdict(v) for v in violations]
            )


class SaveResource(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "ADMIN")
        entity = data["entity"]
        if entity not in EDIT_FIELDS:
            raise Forbidden("此资源不能通过通用编辑接口修改")
        values = data["values"]
        if set(values) - EDIT_FIELDS[entity]:
            raise BusinessError("包含不允许修改的字段")
        if data.get("id"):
            record = store.update(entity, data["id"], values, data["version"])
        else:
            if entity not in {"courses", "rooms"}:
                raise BusinessError("请使用用户创建接口建立学生或教师")
            record = store.add(entity, {**values, "created_by": actor.id})
        if entity in {"courses", "rooms"}:
            link_entity = "course_features" if entity == "courses" else "room_features"
            link_field = "course_id" if entity == "courses" else "room_id"
            if "features" in data:
                safety = (
                    {
                        feature["feature"]
                        for feature in store.list("course_features", {"course_id": record["id"]})
                        if feature["safety_required"]
                    }
                    if entity == "courses"
                    else set()
                )
                if entity == "courses":
                    safety |= {"DRONE": {"drone", "safety"}, "LAB": {"lab", "safety"}}.get(
                        record["preferred_room"], set()
                    )
                if not safety <= set(data["features"]):
                    raise BusinessError("无人机及实验课程的必需设备和安全条件不能移除")
                store.remove_links(link_entity, {link_field: record["id"]})
                for feature in sorted(set(data["features"])):
                    extra = {"safety_required": feature in safety} if entity == "courses" else {}
                    store.add(link_entity, {link_field: record["id"], "feature": feature, **extra})
        if entity == "rooms" and not data.get("id"):
            for slot in store.list("time_slot_templates", {"revision": 1}):
                for day in range(5):
                    store.add(
                        "availability_rules",
                        {
                            "owner_id": record["id"],
                            "owner_kind": "ROOM",
                            "day_index": day,
                            "slot_code": slot["code"],
                        },
                    )
        protect_active(store)
        audit(store, actor, "resource.save", entity, record["id"], {"changed_fields": list(values)})
        return record


class SaveUser(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "ADMIN")
        role = data["role"]
        if role not in {"ADMIN", "TEACHER", "STUDENT"}:
            raise BusinessError("身份无效")
        values = {name: data[name] for name in ("name", "username", "phone") if name in data}
        if "active" in data:
            values["active"] = data["active"]
        if data.get("id"):
            user = store.get("users", data["id"])
            if role != user["role"]:
                raise BusinessError("现有账号身份不可直接修改")
            if user["id"] == actor.id and data.get("active") is False:
                raise BusinessError("不能停用当前登录账号")
            if "active" in data:
                values["active"] = data["active"]
            if data.get("password_hash"):
                values["password_hash"] = data["password_hash"]
            user = store.update("users", user["id"], values, data["version"])
            if data.get("password_hash") or data.get("active") is False:
                for session in store.list("auth_sessions", {"user_id": user["id"]}):
                    if not session.get("revoked_at"):
                        store.update(
                            "auth_sessions",
                            session["id"],
                            {"revoked_at": self.clock.now()},
                            session["version"],
                        )
                for token in store.list("account_tokens", {"user_id": user["id"]}):
                    if not token.get("used_at"):
                        store.update(
                            "account_tokens", token["id"], {"used_at": self.clock.now()}, token["version"]
                        )
        else:
            if not data.get("password_hash"):
                raise BusinessError("新用户必须设置密码")
            if role == "STUDENT" and ("age" not in data or "grade" not in data):
                raise BusinessError("新学生必须填写年龄和年级")
            user = store.add("users", {**values, "role": role, "password_hash": data["password_hash"]})
            if role == "STUDENT":
                store.add(
                    "student_profiles",
                    {
                        "user_id": user["id"],
                        "student_code": f"S-{user['id'][:8]}",
                        "age": data["age"],
                        "grade": data["grade"],
                        "gender": data.get("gender", ""),
                        "notes": data.get("notes", ""),
                    },
                )
            if role == "TEACHER":
                store.add(
                    "teacher_profiles",
                    {
                        "user_id": user["id"],
                        "teacher_code": f"T-{user['id'][:8]}",
                        "bio": data.get("bio", ""),
                    },
                )
            if role != "ADMIN":
                for day in range(5):
                    for slot in store.list("time_slot_templates", {"revision": 1}):
                        store.add(
                            "availability_rules",
                            {
                                "owner_id": user["id"],
                                "owner_kind": role,
                                "day_index": day,
                                "slot_code": slot["code"],
                            },
                        )
        profile_entity = "student_profiles" if role == "STUDENT" else "teacher_profiles"
        if role != "ADMIN" and data.get("id"):
            profiles = store.list(profile_entity, {"user_id": user["id"]})
            allowed = {"age", "grade", "gender", "notes"} if role == "STUDENT" else {"bio"}
            profile_values = {key: data[key] for key in allowed if key in data}
            if profiles and profile_values:
                store.update(profile_entity, profiles[0]["id"], profile_values, profiles[0]["version"])
        protect_active(store)
        audit(store, actor, "user.save", "users", user["id"], {"role": role})
        return {key: value for key, value in user.items() if key != "password_hash"}


class SaveAvailability(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "ADMIN", "TEACHER", "STUDENT")
        is_room = data.get("owner_kind") == "ROOM"
        if is_room:
            require_role(actor, "ADMIN")
            owner = store.get("rooms", data["owner_id"])
        else:
            owner = store.get("users", data.get("owner_id", actor.id))
            require_owner(actor, owner["id"], owner["organization_id"])
        templates = {s["code"] for s in store.list("time_slot_templates", {"revision": 1})}
        slots = data["slots"]
        if len({(s["day_index"], s["slot_code"]) for s in slots}) != len(slots):
            raise BusinessError("可用时段不能重复")
        if any(not 0 <= s["day_index"] <= 4 or s["slot_code"] not in templates for s in slots):
            raise BusinessError("可用时段无效")
        store.lock(f"availability:{owner['id']}")
        owner_kind = "ROOM" if is_room else owner["role"]
        if is_room:
            owner = store.update("rooms", owner["id"], {}, data["version"])
        store.remove_links("availability_rules", {"owner_id": owner["id"], "owner_kind": owner_kind})
        for slot in slots:
            store.add("availability_rules", {"owner_id": owner["id"], "owner_kind": owner_kind, **slot})
        protect_active(store)
        audit(store, actor, "availability.save", "rooms" if is_room else "users", owner["id"])
        return {
            "owner_id": owner["id"],
            "owner_kind": owner_kind,
            "version": owner["version"],
            "slots": slots,
        }


class SaveQualifications(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "ADMIN")
        teacher = store.get("users", data["teacher_id"])
        if teacher["role"] != "TEACHER":
            raise BusinessError("目标用户不是教师")
        store.remove_links("teacher_qualifications", {"teacher_id": teacher["id"]})
        for course_id in set(data["course_ids"]):
            store.get("courses", course_id)
            store.add("teacher_qualifications", {"teacher_id": teacher["id"], "course_id": course_id})
        protect_active(store)
        audit(store, actor, "qualification.save", "users", teacher["id"])
        return {"teacher_id": teacher["id"], "course_ids": data["course_ids"]}


class SaveRules(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "ADMIN")
        rules = Rules(**data["rules"])
        store.set_rules(asdict(rules))
        protect_active(store)
        audit(store, actor, "rules.save", "organizations", actor.organization_id, asdict(rules))
        return {"rules": asdict(rules), "config_revision": store.organization()["config_revision"]}


class RecordAttendance(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "TEACHER", "ADMIN")
        session = store.get("course_sessions", data["session_id"])
        version = store.get("schedule_versions", session["version_id"])
        if version["status"] != "ACTIVE" or session["status"] in {"SUSPENDED", "CANCELLED"}:
            raise BusinessError("只能登记当前生效且正常授课的课次")
        if (
            datetime.fromisoformat(f"{session['day']}T{session['end_time']}").replace(tzinfo=SHANGHAI)
            > self.clock.now()
        ):
            raise BusinessError("课程尚未结束，请课后登记出勤")
        if actor.role != "ADMIN" and session["teacher_id"] != actor.id:
            raise Forbidden("只能登记本人授课班次")
        if not store.list(
            "session_enrollments", {"session_id": session["id"], "student_id": data["student_id"]}
        ):
            raise BusinessError("学生不属于当前班次")
        if data["status"] not in {"PRESENT", "ABSENT", "LATE", "EXCUSED"}:
            raise BusinessError("出勤状态无效")
        existing = store.list(
            "attendance_records", {"session_id": session["id"], "student_id": data["student_id"]}
        )
        values = {
            "status": data["status"],
            "notes": data.get("notes", ""),
            "teacher_id": session["teacher_id"],
        }
        record = (
            store.update("attendance_records", existing[0]["id"], values, existing[0]["version"])
            if existing
            else (
                store.add(
                    "attendance_records",
                    {"session_id": session["id"], "student_id": data["student_id"], **values},
                )
            )
        )
        audit(store, actor, "attendance.record", "course_sessions", session["id"])
        return record


class RecordFeedback(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "TEACHER", "ADMIN")
        session = store.get("course_sessions", data["session_id"])
        if actor.role != "ADMIN" and session["teacher_id"] != actor.id:
            raise Forbidden("只能反馈本人授课班次")
        if store.get("schedule_versions", session["version_id"])["status"] != "ACTIVE":
            raise BusinessError("只能为生效课程登记反馈")
        if session["status"] != "SCHEDULED":
            raise BusinessError("只能为正常授课的课次登记反馈")
        if (
            datetime.fromisoformat(f"{session['day']}T{session['end_time']}").replace(tzinfo=SHANGHAI)
            > self.clock.now()
        ):
            raise BusinessError("课程尚未结束，请课后登记反馈")
        if not store.list(
            "session_enrollments", {"session_id": session["id"], "student_id": data["student_id"]}
        ):
            raise BusinessError("学生不属于当前班次")
        values = {"content": data["content"], "rating": data["rating"], "teacher_id": session["teacher_id"]}
        existing = store.list(
            "course_feedback", {"session_id": session["id"], "student_id": data["student_id"]}
        )
        record = (
            store.update("course_feedback", existing[0]["id"], values, existing[0]["version"])
            if existing
            else (
                store.add(
                    "course_feedback",
                    {"session_id": session["id"], "student_id": data["student_id"], **values},
                )
            )
        )
        notify(store, [data["student_id"]], "课程反馈已更新", data["content"], "/profile")
        audit(store, actor, "feedback.record", "course_sessions", session["id"])
        return record


class RecordAttendanceBatch(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        handler = RecordAttendance(self.uow, self.clock)
        results = [handler.apply(store, actor, values) for values in data["records"]]
        return {"items": results, "total": len(results)}


class InviteOfferingReview(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "ADMIN")
        offering = store.get("course_offerings", data["offering_id"])
        for teacher_id in data["teacher_ids"]:
            teacher = store.get("users", teacher_id)
            if teacher["role"] != "TEACHER" or not store.list(
                "teacher_qualifications",
                {
                    "teacher_id": teacher_id,
                    "course_id": offering["course_id"],
                },
            ):
                raise BusinessError("候选教师必须具有对应课程资质")
            if not store.list("teacher_decisions", {"offering_id": offering["id"], "teacher_id": teacher_id}):
                store.add(
                    "teacher_decisions",
                    {
                        "offering_id": offering["id"],
                        "teacher_id": teacher_id,
                        "decision": "PENDING",
                    },
                )
        notify(store, data["teacher_ids"], "授课审核邀请", offering["name"], "/reviews")
        audit(store, actor, "offering.invite_review", "course_offerings", offering["id"])
        return {"offering_id": offering["id"], "invited": len(data["teacher_ids"])}


class MarkNotificationRead(CommandHandler):
    locks_resources = False

    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        store.lock(f"notification:{data['id']}")
        notification = store.get("notifications", data["id"])
        require_owner(actor, notification["user_id"], notification["organization_id"])
        if notification.get("read_at"):
            return notification
        return store.update(
            "notifications", notification["id"], {"read_at": self.clock.now()}, notification["version"]
        )


class EmergencyPause(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "ADMIN")
        session = store.get("course_sessions", data["session_id"], lock=True)
        version = store.get("schedule_versions", session["version_id"])
        store.lock(f"week:{version['week']}")
        if version["status"] != "ACTIVE" or session["status"] != "SCHEDULED":
            raise Conflict("仅可紧急暂停生效课次")
        lesson = next(s for s in store.lessons(version["id"]) if s.id == session["logical_id"])
        suspended = replace(lesson, status="SUSPENDED")
        result = store.update(
            "course_sessions",
            session["id"],
            {
                "status": "SUSPENDED",
                "fingerprint": suspended.fingerprint,
            },
            session["version"],
        )
        for reservation in store.list(
            "resource_reservations", {"session_id": session["id"], "is_active": True}
        ):
            store.update(
                "resource_reservations", reservation["id"], {"is_active": False}, reservation["version"]
            )
        members = store.list("session_enrollments", {"session_id": session["id"]})
        snapshot = store.snapshot(version["week"])
        lessons = store.lessons(version["id"])
        violations = validate_schedule(snapshot, lessons)
        report = {
            **version["report"],
            "metrics": schedule_metrics(snapshot, lessons),
            "violations": [asdict(v) for v in violations],
            "hard_conflicts": len(violations),
            "emergency_paused": session["logical_id"],
        }
        from eduflow.application.commands import json_record
        from eduflow.domain.entities import Snapshot

        original = Snapshot.from_dict(version["snapshot"])
        updated_snapshot = replace(
            original, locked=tuple(suspended if s.id == suspended.id else s for s in original.locked)
        )
        store.update(
            "schedule_versions",
            version["id"],
            {
                "report": json_record(report),
                "snapshot": updated_snapshot.to_dict(),
            },
            version["version"],
        )
        change = store.add(
            "change_requests",
            {
                "requester_id": actor.id,
                "kind": "EMERGENCY_PAUSE",
                "status": "SUBMITTED",
                "reason": data["reason"],
                "week": version["week"],
                "base_version_id": version["id"],
                "payload": {"session_ids": [session["logical_id"]], "record_id": session["id"]},
            },
        )
        from eduflow.application.changes import move_change

        store.add(
            "change_request_events",
            {
                "change_request_id": change["id"],
                "actor_id": actor.id,
                "from_state": "",
                "to_state": "SUBMITTED",
                "message": data["reason"],
            },
        )
        for state in ("VALIDATING", "PENDING_ADMIN", "APPROVED", "APPLIED"):
            change = move_change(store, actor, change, state, "管理员紧急停课，已撤销资源预订")
        notify(
            store,
            [session["teacher_id"], *[m["student_id"] for m in members]],
            "紧急停课通知",
            data["reason"],
            "/schedule",
        )
        audit(
            store,
            actor,
            "session.emergency_pause",
            "course_sessions",
            session["id"],
            {
                "reason": data["reason"],
                "before": lesson.to_dict(),
                "after": suspended.to_dict(),
                "change_request_id": change["id"],
                "hard_conflicts": len(violations),
            },
        )
        return result
