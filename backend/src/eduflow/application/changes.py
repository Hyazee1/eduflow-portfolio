from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta

from eduflow.application.commands import CommandHandler, audit, json_record, notify
from eduflow.application.ports import Clock, Record, RecordStore, Scheduler, UnitOfWorkFactory
from eduflow.application.scheduling import activate, new_version, publish
from eduflow.domain.authorization import require_owner, require_role
from eduflow.domain.entities import Lesson, Principal, Snapshot
from eduflow.domain.errors import BusinessError, Conflict, Forbidden, StaleVersion
from eduflow.domain.metrics import schedule_metrics
from eduflow.domain.states import transition
from eduflow.domain.time_rules import SHANGHAI, slot_key, week_days
from eduflow.domain.validator import validate_schedule

CHANGE_KINDS = {
    "TEACHER_LEAVE",
    "STUDENT_LEAVE",
    "RESCHEDULE",
    "ROOM_FAULT",
    "EQUIPMENT_FAULT",
    "SUBSTITUTE",
    "CANCEL_SESSION",
    "ENROLLMENT_CHANGE",
    "ADD_SESSION",
}


def move_change(store: RecordStore, actor: Principal, change: Record, target: str, message: str) -> Record:
    transition(change["status"], target, change=True)
    store.add(
        "change_request_events",
        {
            "change_request_id": change["id"],
            "actor_id": actor.id,
            "from_state": change["status"],
            "to_state": target,
            "message": message,
        },
    )
    return store.update("change_requests", change["id"], {"status": target}, change["version"])


class CreateChangeRequest(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "ADMIN", "TEACHER", "STUDENT")
        kind = data["kind"]
        if not isinstance(data.get("reason"), str) or not 2 <= len(data["reason"].strip()) <= 2000:
            raise BusinessError("申请原因必须为 2—2000 字符")
        if kind not in CHANGE_KINDS:
            raise BusinessError("异常类型无效")
        if actor.role == "STUDENT" and kind not in {"STUDENT_LEAVE", "RESCHEDULE", "ENROLLMENT_CHANGE"}:
            raise Forbidden("学生只可申请本人请假、调课或调整选课")
        if kind == "TEACHER_LEAVE":
            require_role(actor, "TEACHER", "ADMIN")
        if kind in {"CANCEL_SESSION", "ADD_SESSION", "SUBSTITUTE"}:
            require_role(actor, "ADMIN", "TEACHER")
        if kind == "ADD_SESSION":
            require_role(actor, "ADMIN")
        version = store.get("schedule_versions", data["version_id"])
        if version["status"] != "ACTIVE":
            raise Conflict("异常申请必须引用当前生效课表")
        payload = dict(data["payload"])
        allowed_fields = {
            "session_ids",
            "starts_at",
            "ends_at",
            "target_day",
            "target_slot",
            "owner_id",
            "teacher_id",
            "room_id",
            "request_ids",
        }
        if set(payload) - allowed_fields:
            raise BusinessError("异常申请包含未知参数")
        if not isinstance(payload.get("session_ids", []), list) or len(payload.get("session_ids", [])) > 200:
            raise BusinessError("受影响课次参数无效")
        selected = set(payload.get("session_ids", []))
        lessons = store.lessons(version["id"])
        if payload.get("target_day") and date.fromisoformat(payload["target_day"]) not in week_days(
            version["week"]
        ):
            raise BusinessError("局部调课仅限当前排课周；跨周安排请分别建立对应周的需求")
        if payload.get("room_id"):
            store.get("rooms", payload["room_id"])
        if payload.get("teacher_id") and store.get("users", payload["teacher_id"])["role"] != "TEACHER":
            raise BusinessError("代课人员必须为教师")
        if kind == "ADD_SESSION":
            assigned = {r for lesson in lessons for r in lesson.request_ids}
            selected_requests = payload.get("request_ids", [])
            if not selected_requests:
                raise BusinessError("增课需要选择已审核且未安排的真实需求")
            for request_id in selected_requests:
                request = store.get("enrollment_requests", request_id)
                if (
                    request["status"] != "APPROVED"
                    or request["week"] != version["week"]
                    or request_id in assigned
                ):
                    raise BusinessError("增课需求必须属于本周、已审核且尚未安排")
        affected = [s for s in lessons if s.id in selected]
        if kind != "ADD_SESSION" and (not affected or len(affected) != len(selected)):
            raise BusinessError("请选择有效的受影响课次")
        for lesson in affected:
            if actor.role == "TEACHER" and lesson.teacher_id != actor.id:
                raise Forbidden("不能申请调整其他教师的课程")
            if actor.role == "STUDENT" and actor.id not in lesson.students:
                raise Forbidden("不能申请调整其他学生的课程")
        if kind in {"TEACHER_LEAVE", "STUDENT_LEAVE", "ROOM_FAULT", "EQUIPMENT_FAULT"}:
            try:
                start = datetime.fromisoformat(payload["starts_at"])
                end = datetime.fromisoformat(payload["ends_at"])
            except (KeyError, ValueError, TypeError) as error:
                raise BusinessError("请提供有效的请假或故障开始、结束时间") from error
            if not start.tzinfo or not end.tzinfo or end <= start:
                raise BusinessError("请假或故障时间范围无效，必须包含时区")
            week_start = datetime.combine(version["week"], time(), SHANGHAI)
            if start < week_start or end > week_start + timedelta(days=5):
                raise BusinessError("请将跨周的请假或故障拆分为各排课周申请")
            owner = payload.get("owner_id", actor.id)
            if actor.role != "ADMIN" and kind in {"TEACHER_LEAVE", "STUDENT_LEAVE"} and owner != actor.id:
                raise Forbidden("只能为本人申请请假")
            payload["owner_id"] = owner
            if kind in {"TEACHER_LEAVE", "STUDENT_LEAVE"}:
                if store.get("users", owner)["role"] != ("TEACHER" if kind == "TEACHER_LEAVE" else "STUDENT"):
                    raise BusinessError("请假人员身份不匹配")
            slots = {s.code: s for s in store.snapshot(version["week"]).slots}
            if kind in {"ROOM_FAULT", "EQUIPMENT_FAULT"}:
                room = store.get("rooms", owner)
                if actor.role == "TEACHER" and not any(s.room_id == room["id"] for s in affected):
                    raise Forbidden("只能报告本人授课教室的资源异常")
            # Include all impacted sessions; partial selection must not leave hidden conflicting classes.
            relevant = []
            for lesson in lessons:
                left, right = slots[lesson.slot_code].at(lesson.day)
                belongs = (
                    lesson.room_id == owner
                    if kind in {"ROOM_FAULT", "EQUIPMENT_FAULT"}
                    else lesson.teacher_id == owner
                    if kind == "TEACHER_LEAVE"
                    else owner in lesson.students
                )
                if belongs and left < end and start < right:
                    relevant.append(lesson)
            if not relevant:
                raise BusinessError("所选时间没有受影响课次")
            payload["session_ids"] = [s.id for s in relevant]
        change = store.add(
            "change_requests",
            {
                "requester_id": actor.id,
                "kind": kind,
                "week": version["week"],
                "status": "SUBMITTED",
                "reason": data["reason"],
                "base_version_id": version["id"],
                "payload": json_record(payload),
            },
        )
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
        change = move_change(store, actor, change, "VALIDATING", "校验申请范围与资源归属")
        change = move_change(store, actor, change, "PENDING_ADMIN", "申请有效，等待方案与管理员审批")
        notify(
            store,
            [u["id"] for u in store.list("users", {"role": "ADMIN", "active": True})],
            "新的异常事务",
            f"事务编号：{change['id']}，类型：{kind}",
            "/changes",
        )
        audit(store, actor, "change.submit", "change_requests", change["id"], {"kind": kind})
        return change


def proposed_snapshot(snapshot: Snapshot, change: Record, lessons: tuple[Lesson, ...]) -> Snapshot:
    payload = change["payload"]
    affected_ids = set(payload.get("session_ids", []))
    affected = tuple(s for s in lessons if s.id in affected_ids)
    locked = tuple(s for s in lessons if s.id not in affected_ids)
    request_ids = {r for s in lessons for r in s.request_ids}
    selected_requests = request_ids | set(payload.get("request_ids", []))
    demands = (
        tuple(d for d in snapshot.demands if d.id in selected_requests)
        if change["kind"] == "ADD_SESSION"
        else tuple(d for d in snapshot.demands if d.id in request_ids)
    )
    teachers = snapshot.teachers
    students = snapshot.students
    rooms = snapshot.rooms
    removed: set[str] = set()
    if change["kind"] in {"TEACHER_LEAVE", "STUDENT_LEAVE", "ROOM_FAULT", "EQUIPMENT_FAULT"}:
        start, end = datetime.fromisoformat(payload["starts_at"]), datetime.fromisoformat(payload["ends_at"])
        blocked = set()
        for day in {s.day for s in lessons}:
            for slot in snapshot.slots:
                left, right = slot.at(day)
                if left < end and start < right:
                    blocked.add(slot_key(day, slot.code))
        owner_id = payload["owner_id"]
        if change["kind"] == "TEACHER_LEAVE":
            teachers = tuple(
                replace(t, available=t.available - blocked) if t.id == owner_id else t for t in teachers
            )
        elif change["kind"] in {"ROOM_FAULT", "EQUIPMENT_FAULT"}:
            rooms = tuple(
                replace(r, available=r.available - blocked) if r.id == owner_id else r for r in rooms
            )
        else:
            students = tuple(
                replace(s, available=s.available - blocked) if s.id == owner_id else s for s in students
            )
            updated = []
            for lesson in affected:
                members = [
                    (u, r) for u, r in zip(lesson.students, lesson.request_ids, strict=True) if u != owner_id
                ]
                removed.update(
                    r for u, r in zip(lesson.students, lesson.request_ids, strict=True) if u == owner_id
                )
                updated.append(
                    replace(
                        lesson,
                        students=tuple(u for u, _ in members),
                        request_ids=tuple(r for _, r in members),
                    )
                )
            affected = tuple(updated)
    if change["kind"] in {"CANCEL_SESSION", "ENROLLMENT_CHANGE"}:
        if change["kind"] == "CANCEL_SESSION":
            removed.update(r for s in affected for r in s.request_ids)
            locked = (*locked, *(replace(s, status="CANCELLED") for s in affected))
            affected = ()
        else:
            owner = payload.get("owner_id", change["requester_id"])
            updated = []
            for lesson in affected:
                members = [
                    (u, r) for u, r in zip(lesson.students, lesson.request_ids, strict=True) if u != owner
                ]
                removed.update(
                    r for u, r in zip(lesson.students, lesson.request_ids, strict=True) if u == owner
                )
                updated.append(
                    replace(
                        lesson,
                        students=tuple(u for u, _ in members),
                        request_ids=tuple(r for _, r in members),
                    )
                )
            affected = tuple(updated)
    demands = tuple(d for d in demands if d.id not in removed)
    # Restrict explicit target choices through the same hard domains used by the solver.
    affected_requests = {r for s in affected for r in s.request_ids}
    if payload.get("target_day") and payload.get("target_slot"):
        key = slot_key(date.fromisoformat(payload["target_day"]), payload["target_slot"])
        demands = tuple(
            replace(d, allowed=d.allowed & {key}) if d.id in affected_requests else d for d in demands
        )
    if payload.get("teacher_id"):
        demands = tuple(
            replace(d, approved_teachers=d.approved_teachers & {payload["teacher_id"]})
            if d.id in affected_requests
            else d
            for d in demands
        )
    if payload.get("room_id"):
        demands = tuple(
            replace(d, allowed_rooms=frozenset({payload["room_id"]})) if d.id in affected_requests else d
            for d in demands
        )
    if change["kind"] == "SUBSTITUTE":
        original_teachers = {r: s.teacher_id for s in affected for r in s.request_ids}
        original_keys = {r: slot_key(s.day, s.slot_code) for s in affected for r in s.request_ids}
        demands = tuple(
            replace(
                d,
                approved_teachers=d.approved_teachers - {original_teachers[d.id]},
                allowed=d.allowed & {original_keys[d.id]},
            )
            if d.id in affected_requests
            else d
            for d in demands
        )
    return replace(
        snapshot,
        demands=demands,
        teachers=teachers,
        students=students,
        rooms=rooms,
        locked=locked,
        baseline=affected,
        rules=replace(snapshot.rules, solve_seconds=10),
    )


@dataclass(frozen=True)
class SimulateChange:
    uow: UnitOfWorkFactory
    clock: Clock
    scheduler: Scheduler

    def execute(self, actor: Principal, change_id: str) -> Record:
        with self.uow(actor.organization_id) as transaction:
            store = transaction.records
            store.lock("resources")
            change = store.get("change_requests", change_id)
            require_owner(actor, change["requester_id"], actor.organization_id)
            if change["status"] != "PENDING_ADMIN":
                raise Conflict("仅可对待审批工单生成方案")
            base = store.get("schedule_versions", change["base_version_id"])
            if base["status"] != "ACTIVE":
                raise StaleVersion("原课表已经更新，请重新提交申请")
            lessons = store.lessons(base["id"])
            source_snapshot = store.snapshot(change["week"])
            snapshot = proposed_snapshot(source_snapshot, change, lessons)
        strategy = "D：锁定无关课程，执行局部最小变更求解"
        result = None
        if change["kind"] == "TEACHER_LEAVE":
            original_keys = {
                r: slot_key(s.day, s.slot_code) for s in snapshot.baseline for r in s.request_ids
            }
            fixed_time = replace(
                snapshot,
                demands=tuple(
                    replace(d, allowed=d.allowed & {original_keys[d.id]}) if d.id in original_keys else d
                    for d in snapshot.demands
                ),
            )
            candidate = self.scheduler.solve(fixed_time)
            if candidate.status in {"OPTIMAL", "FEASIBLE"}:
                result = candidate
                strategy = "A：保留原时段，更换已审核且具备资质的代课教师"
            if result is None:
                original_teachers = {r: s.teacher_id for s in snapshot.baseline for r in s.request_ids}
                same_teacher = replace(
                    snapshot,
                    demands=tuple(
                        replace(d, approved_teachers=d.approved_teachers & {original_teachers[d.id]})
                        if d.id in original_teachers
                        else d
                        for d in snapshot.demands
                    ),
                )
                candidate = self.scheduler.solve(same_teacher)
                if candidate.status in {"OPTIMAL", "FEASIBLE"}:
                    result = candidate
                    strategy = "B：保留教师，选择请假范围以外的共同可用时段"
        if result is None:
            result = self.scheduler.solve(snapshot)
        violations = validate_schedule(snapshot, result.lessons) if result.lessons else ()
        original_requests = {
            r for lesson in lessons if lesson.status == "SCHEDULED" for r in lesson.request_ids
        }
        proposed_requests = {
            r for lesson in result.lessons if lesson.status == "SCHEDULED" for r in lesson.request_ids
        }
        proposal: Record = {
            "base_version_revision": base["version"],
            "source_fingerprint": source_snapshot.fingerprint,
            "solver_status": result.status,
            "wall_seconds": result.wall_seconds,
            "strategy": strategy,
            "diagnostics": list(result.diagnostics),
            "snapshot": snapshot.to_dict(),
            "lessons": [s.to_dict() for s in result.lessons],
            "metrics": schedule_metrics(snapshot, result.lessons) if result.lessons else {},
            "violations": [asdict(v) for v in violations],
        }
        with self.uow(actor.organization_id) as transaction:
            store = transaction.records
            store.lock("resources")
            store.lock(f"week:{change['week']}")
            latest = store.get("change_requests", change_id, lock=True)
            if latest["version"] != change["version"]:
                raise StaleVersion("工单已更新，请重新生成方案")
            current_base = store.get("schedule_versions", base["id"])
            if (
                current_base["status"] != "ACTIVE"
                or current_base["version"] != base["version"]
                or store.snapshot(change["week"]).fingerprint != source_snapshot.fingerprint
            ):
                raise StaleVersion("课表或资源已改变，请重新生成方案")
            payload = {
                **latest["payload"],
                "removed_request_ids": sorted(original_requests - proposed_requests),
            }
            updated = store.update(
                "change_requests",
                change_id,
                {
                    "proposal": json_record(proposal),
                    "payload": payload,
                },
                latest["version"],
            )
            audit(
                store,
                actor,
                "change.simulate",
                "change_requests",
                change_id,
                {"solver_status": result.status, "strategy": strategy},
            )
            transaction.commit()
            return updated


class DecideChange(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "ADMIN")
        change = store.get("change_requests", data["change_id"], lock=True)
        if change["version"] != data["version"]:
            raise StaleVersion("工单已更新")
        store.lock(f"week:{change['week']}")
        if not data["approved"]:
            if not data.get("comment", "").strip():
                raise BusinessError("拒绝申请必须填写原因")
            result = move_change(store, actor, change, "REJECTED", data["comment"])
        else:
            proposal = change["proposal"]
            if not proposal or proposal["solver_status"] not in {"OPTIMAL", "FEASIBLE"}:
                raise BusinessError("请先生成有效可行调整方案")
            base = store.get("schedule_versions", change["base_version_id"])
            if base["status"] != "ACTIVE":
                raise StaleVersion("基础课表已经改变，请重新生成申请")
            if base["version"] != proposal.get("base_version_revision") or store.snapshot(
                change["week"]
            ).fingerprint != proposal.get("source_fingerprint"):
                raise StaleVersion("方案基础课表或资源已改变，请重新模拟")
            snapshot = Snapshot.from_dict(proposal["snapshot"])
            lessons = tuple(Lesson.from_dict(s) for s in proposal["lessons"])
            change = move_change(store, actor, change, "APPROVED", data.get("comment", ""))
            version = new_version(
                store,
                actor,
                snapshot,
                lessons,
                proposal["solver_status"],
                {"origin": "CHANGE_REQUEST", "strategy": proposal["strategy"]},
                base["id"],
                change["id"],
            )
            version = store.update(
                "schedule_versions",
                version["id"],
                {
                    "status": transition(version["status"], "VALIDATED"),
                },
                version["version"],
            )
            version = publish(store, actor, version, self.clock.now())
            result = move_change(store, actor, change, "WAITING_CONFIRMATION", f"拟调整版本：{version['id']}")
            session_rows = store.list("course_sessions", {"version_id": version["id"]})
            if not store.list(
                "schedule_confirmations",
                {
                    "session_id": [row["id"] for row in session_rows],
                    "decision": "PENDING",
                },
            ):
                activate(store, actor, version, self.clock.now())
                result = store.get("change_requests", change["id"])
        result = store.update(
            "change_requests",
            result["id"],
            {
                "admin_comment": data.get("comment", ""),
            },
            result["version"],
        )
        notify(
            store,
            [change["requester_id"]],
            "异常事务审批更新",
            f"事务 {change['id']}：{result['status']}",
            "/changes",
        )
        audit(
            store, actor, "change.decision", "change_requests", change["id"], {"approved": data["approved"]}
        )
        return result


class CancelChange(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        change = store.get("change_requests", data["change_id"], lock=True)
        require_owner(actor, change["requester_id"], actor.organization_id)
        if change["status"] == "WAITING_CONFIRMATION":
            require_role(actor, "ADMIN")
            pending = store.list(
                "schedule_versions",
                {"change_request_id": change["id"], "status": "PUBLISHED_PENDING_CONFIRMATION"},
            )
            for version in pending:
                store.update("schedule_versions", version["id"], {"status": "CANCELLED"}, version["version"])
        result = move_change(store, actor, change, "CANCELLED", data.get("reason", "撤回申请"))
        audit(store, actor, "change.cancel", "change_requests", change["id"])
        return result
