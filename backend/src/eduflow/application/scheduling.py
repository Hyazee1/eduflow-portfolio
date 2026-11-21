from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from typing import Any

from eduflow.application.commands import CommandHandler, audit, json_record, notify
from eduflow.application.ports import Clock, Record, RecordStore, Scheduler, UnitOfWorkFactory
from eduflow.domain.authorization import require_role
from eduflow.domain.entities import Lesson, Principal, Snapshot
from eduflow.domain.errors import BusinessError, Conflict, Forbidden, StaleVersion
from eduflow.domain.metrics import schedule_metrics
from eduflow.domain.states import transition
from eduflow.domain.validator import validate_schedule


def validate_version(store: RecordStore, version: Record) -> tuple[Snapshot, tuple[Lesson, ...], Record]:
    current = store.snapshot(version["week"])
    original = Snapshot.from_dict(version["snapshot"])
    if current.rules.require_full_day_coverage and not original.rules.require_full_day_coverage:
        raise StaleVersion("该候选使用旧覆盖规则，请按全天覆盖要求重新排课；原候选可删除")
    current = replace(current, locked=original.locked, baseline=original.baseline)
    lessons = store.lessons(version["id"])
    violations = validate_schedule(current, lessons)
    report = {
        **version["report"],
        "metrics": schedule_metrics(current, lessons),
        "violations": [asdict(v) for v in violations],
        "hard_conflicts": len(violations),
    }
    return current, lessons, report


def new_version(
    store: RecordStore,
    actor: Principal,
    snapshot: Snapshot,
    lessons: tuple[Lesson, ...],
    solver_status: str,
    report: Record,
    base_version_id: str | None = None,
    change_request_id: str | None = None,
) -> Record:
    if base_version_id:
        retained = {lesson.id for lesson in lessons}
        lessons = (
            *lessons,
            *(
                replace(old, status="CANCELLED")
                for old in store.lessons(base_version_id)
                if old.id not in retained
            ),
        )
    violations = validate_schedule(snapshot, lessons)
    if violations:
        raise BusinessError("候选课表违反硬约束", details=[asdict(v) for v in violations])
    version = store.add(
        "schedule_versions",
        {
            "week": snapshot.week,
            "status": "DRAFT",
            "solver_status": solver_status,
            "base_version_id": base_version_id,
            "base_version_revision": store.get("schedule_versions", base_version_id)["version"]
            if base_version_id
            else None,
            "snapshot": snapshot.to_dict(),
            "report": json_record(
                {
                    **report,
                    "metrics": schedule_metrics(snapshot, lessons),
                    "violations": [],
                    "hard_conflicts": 0,
                }
            ),
            "change_request_id": change_request_id,
            "created_by": actor.id,
        },
    )
    store.save_lessons(version["id"], lessons)
    return version


class SolveWeek(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "ADMIN")
        week = date.fromisoformat(data["week"])
        store.lock(f"week:{week}")
        profile = data.get("optimization_profile", "balanced")
        if profile not in {"balanced", "preference_first"}:
            raise BusinessError("未知排课优化策略")
        reference_id = data.get("reference_version_id")
        options: Record = {"optimization_profile": profile}
        if reference_id:
            reference_version = store.get("schedule_versions", reference_id)
            if reference_version["week"] != week:
                raise Conflict("参考课表必须属于同一业务周")
            _, reference_lessons, reference_report = validate_version(store, reference_version)
            if reference_report["hard_conflicts"]:
                raise Conflict("参考课表已不符合当前规则，请先处理资源变化")
            options.update(
                reference_version_id=reference_id,
                reference_version_revision=reference_version["version"],
                reference_lessons=[s.to_dict() for s in reference_lessons],
            )
        pending = store.list("schedule_versions", {"week": week, "status": "PUBLISHED_PENDING_CONFIRMATION"})
        if pending and not (
            profile == "preference_first"
            and reference_id == pending[0]["id"]
            and pending[0]["confirmation_deadline"] < self.clock.now()
        ):
            raise Conflict("本周已有待确认版本；仅可对明确引用的逾期版本生成优化草稿")
        snapshot = store.snapshot(week)
        active = store.list("schedule_versions", {"week": week, "status": "ACTIVE"})
        if active:
            snapshot = replace(snapshot, baseline=store.lessons(active[0]["id"]))
        job = store.add(
            "solve_jobs",
            {
                "requested_by": actor.id,
                "week": week,
                "status": "QUEUED",
                "snapshot": snapshot.to_dict(),
                "result": {"request_options": options},
            },
        )
        store.add("outbox_events", {"event_type": "SOLVE", "payload": {"job_id": job["id"]}})
        audit(store, actor, "schedule.solve_requested", "solve_jobs", job["id"])
        return {"id": job["id"], "status": job["status"]}


@dataclass(frozen=True)
class RunSolveJob:
    uow: UnitOfWorkFactory
    clock: Clock
    scheduler: Scheduler

    def execute(self, organization_id: str, job_id: str) -> Record:
        with self.uow(organization_id) as transaction:
            store = transaction.records
            store.lock("resources")
            store.lock(f"job:{job_id}")
            job = store.get("solve_jobs", job_id, lock=True)
            if job["status"] in {"COMPLETED", "STALE", "FAILED"}:
                return job
            if job["status"] == "RUNNING" and job["started_at"] > self.clock.now() - timedelta(minutes=6):
                return job
            snapshot = Snapshot.from_dict(job["snapshot"])
            options = (job.get("result") or {}).get("request_options", {})
            profile = options.get("optimization_profile", "balanced")
            reference = tuple(Lesson.from_dict(s) for s in options.get("reference_lessons", []))
            store.update(
                "solve_jobs", job_id, {"status": "RUNNING", "started_at": self.clock.now()}, job["version"]
            )
            transaction.commit()
        result = (
            self.scheduler.solve(snapshot, profile=profile, reference=reference)
            if profile != "balanced"
            else self.scheduler.solve(snapshot)
        )
        with self.uow(organization_id) as transaction:
            store = transaction.records
            store.lock("resources")
            store.lock(f"week:{snapshot.week}")
            job = store.get("solve_jobs", job_id, lock=True)
            if job["status"] in {"COMPLETED", "STALE", "FAILED"}:
                return job
            current = store.snapshot(snapshot.week)
            # Baseline does not affect source freshness; it is immutable version history.
            active_source = store.list("schedule_versions", {"week": snapshot.week, "status": "ACTIVE"})
            if active_source:
                current = replace(current, baseline=store.lessons(active_source[0]["id"]))
            stale = current.fingerprint != snapshot.fingerprint
            report: Record = {
                "solver_status": result.status,
                "wall_seconds": round(result.wall_seconds, 3),
                "objective": result.objective,
                "bound": result.bound,
                "diagnostics": list(result.diagnostics),
                "unmet_requests": list(result.unmet_requests),
                "optimization": result.metadata,
                "request_options": {k: v for k, v in options.items() if k != "reference_lessons"},
            }
            version_id = None
            actor_user = store.get("users", job["requested_by"])
            actor = Principal(actor_user["id"], organization_id, actor_user["role"], actor_user["name"])
            if result.status in {"OPTIMAL", "FEASIBLE"} and not stale:
                active = store.list("schedule_versions", {"week": snapshot.week, "status": "ACTIVE"})
                version = new_version(
                    store,
                    actor,
                    snapshot,
                    result.lessons,
                    result.status,
                    report,
                    active[0]["id"] if active else None,
                )
                version_id = version["id"]
            updated = store.update(
                "solve_jobs",
                job_id,
                {
                    "status": "STALE" if stale else "COMPLETED",
                    "completed_at": self.clock.now(),
                    "result": json_record(report),
                    "version_id": version_id,
                },
                job["version"],
            )
            audit(store, actor, "schedule.solve_completed", "solve_jobs", job_id, report)
            notify(
                store,
                [actor.id],
                "排课任务完成",
                "源数据已改变，请重新排课" if stale else f"求解状态：{result.status}",
                "/scheduling",
            )
            transaction.commit()
            return updated


class ValidateVersion(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "ADMIN")
        version = store.get("schedule_versions", data["version_id"], lock=True)
        if version["version"] != data["version"]:
            raise StaleVersion("课表版本已更新")
        if version["solver_status"] not in {"OPTIMAL", "FEASIBLE"}:
            raise Conflict("仅可验证具有有效可行解的课表")
        _, _, report = validate_version(store, version)
        if report["hard_conflicts"]:
            raise BusinessError("课表校验失败", details=report)
        target = "VALIDATED" if version["status"] == "DRAFT" else version["status"]
        if target != "VALIDATED":
            raise Conflict("只能验证草稿或已校验版本")
        if version["status"] == "DRAFT":
            transition(version["status"], target)
        updated = store.update(
            "schedule_versions",
            version["id"],
            {
                "status": target,
                "report": json_record(report),
            },
            version["version"],
        )
        audit(store, actor, "schedule.validate", "schedule_versions", version["id"], report)
        return updated


def confirmation_requirements(
    lessons: tuple[Lesson, ...], originals: tuple[Lesson, ...]
) -> dict[str, set[str]]:
    previous = {lesson.id: lesson for lesson in originals}
    requirements = {}
    for lesson in lessons:
        participants = {lesson.teacher_id, *lesson.students}
        old = previous.get(lesson.id)
        if old and old.fingerprint != lesson.fingerprint:
            participants.update((old.teacher_id, *old.students))
        requirements[lesson.id] = participants
    return requirements


def ensure_future_changes(
    snapshot: Snapshot, lessons: tuple[Lesson, ...], originals: tuple[Lesson, ...], now: Any
) -> None:
    previous = {lesson.id: lesson for lesson in originals}
    slots = {slot.code: slot for slot in snapshot.slots}
    for lesson in lessons:
        old = previous.get(lesson.id)
        if (not old or old.fingerprint != lesson.fingerprint) and slots[lesson.slot_code].at(lesson.day)[
            0
        ] <= now:
            raise Conflict("新增或调整课次已开始，请管理员取消此候选版本并重新处理")


def publish(store: RecordStore, actor: Principal, version: Record, now: Any) -> Record:
    store.lock(f"week:{version['week']}")
    if version["status"] != "VALIDATED":
        raise Conflict("请先校验课表，再发布")
    active = store.list("schedule_versions", {"week": version["week"], "status": "ACTIVE"})
    active_id = active[0]["id"] if active else None
    if active_id != version["base_version_id"]:
        raise StaleVersion("基础生效版本已经改变，请重新生成方案")
    if active and active[0]["version"] != version.get("base_version_revision"):
        raise StaleVersion("基础课表内容已经改变，请重新生成方案")
    if store.list("schedule_versions", {"week": version["week"], "status": "PUBLISHED_PENDING_CONFIRMATION"}):
        raise Conflict("本周已有待确认版本")
    snapshot, lessons, report = validate_version(store, version)
    if report["hard_conflicts"]:
        raise BusinessError("发布前最终校验失败", details=report)
    originals = store.lessons(active_id) if active_id else ()
    original_by_id = {s.id: s for s in originals}
    ensure_future_changes(snapshot, lessons, originals, now)
    required = confirmation_requirements(lessons, originals)
    old_confirmations: dict[tuple[str, str], Record] = {}
    if active_id:
        old_rows = {s["id"]: s for s in store.list("course_sessions", {"version_id": active_id})}
        for confirmation in store.list("schedule_confirmations", {"session_id": list(old_rows)}):
            if confirmation["decision"] == "ACCEPTED":
                old_confirmations[
                    (old_rows[confirmation["session_id"]]["logical_id"], confirmation["user_id"])
                ] = confirmation
    recipients = set()
    lesson_by_id = {s.id: s for s in lessons}
    for row in store.list("course_sessions", {"version_id": version["id"]}):
        lesson = lesson_by_id[row["logical_id"]]
        participants = required[lesson.id]
        original = original_by_id.get(lesson.id)
        for user_id in participants:
            inherited = old_confirmations.get((lesson.id, user_id))
            carry = (
                inherited
                and original
                and original.fingerprint == lesson.fingerprint
                and inherited["fingerprint"] == lesson.fingerprint
            )
            store.add(
                "schedule_confirmations",
                {
                    "session_id": row["id"],
                    "user_id": user_id,
                    "fingerprint": lesson.fingerprint,
                    "decision": "ACCEPTED" if carry else "PENDING",
                    "confirmed_at": inherited["confirmed_at"] if inherited and carry else None,
                    "inherited_from": inherited["id"] if inherited and carry else None,
                },
            )
            if not carry:
                recipients.add(user_id)
    updated = store.update(
        "schedule_versions",
        version["id"],
        {
            "status": transition(version["status"], "PUBLISHED_PENDING_CONFIRMATION"),
            "published_at": now,
            "confirmation_deadline": now + timedelta(hours=24),
            "report": json_record(report),
        },
        version["version"],
    )
    notify(
        store, list(recipients), "课表待确认", "请确认本人相关课次，全部确认完成后整周统一生效。", "/schedule"
    )
    audit(store, actor, "schedule.publish", "schedule_versions", version["id"])
    return updated


class PublishVersion(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "ADMIN")
        version = store.get("schedule_versions", data["version_id"], lock=True)
        if version["version"] != data["version"]:
            raise StaleVersion("课表版本已经更新")
        replacement_id = data.get("replace_expired_version_id")
        if replacement_id:
            store.lock(f"week:{version['week']}")
            previous = store.get("schedule_versions", replacement_id, lock=True)
            if previous["version"] != data.get("replace_expired_version_revision"):
                raise StaleVersion("待替换版本已更新，请刷新后重试")
            if (
                previous["id"] == version["id"]
                or previous["week"] != version["week"]
                or previous["status"] != "PUBLISHED_PENDING_CONFIRMATION"
                or previous["confirmation_deadline"] >= self.clock.now()
                or previous.get("change_request_id")
            ):
                raise Conflict("只能替换同周、已逾期且无关联活动工单的待确认版本")
            _, _, old_report = validate_version(store, previous)
            _, _, new_report = validate_version(store, version)
            old_metrics, new_metrics = old_report["metrics"], new_report["metrics"]
            if (
                old_report["hard_conflicts"]
                or new_report["hard_conflicts"]
                or any(
                    new_metrics[key] < old_metrics[key]
                    for key in ("fulfilled_count", "first_preferred_count", "preferred_count")
                )
            ):
                raise Conflict("替换方案未通过独立校验或需求与偏好指标低于原方案")
            store.update("schedule_versions", previous["id"], {"status": "CANCELLED"}, previous["version"])
            audit(
                store,
                actor,
                "schedule.expired_replaced",
                "schedule_versions",
                previous["id"],
                {"replacement_version_id": version["id"]},
            )
        published = publish(store, actor, version, self.clock.now())
        rows = store.list("course_sessions", {"version_id": version["id"]})
        pending = store.list(
            "schedule_confirmations",
            {
                "session_id": [r["id"] for r in rows],
                "decision": "PENDING",
            },
        )
        return activate(store, actor, published, self.clock.now()) if not pending else published


def activate(store: RecordStore, actor: Principal, version: Record, now: Any) -> Record:
    from eduflow.application.change_effects import apply_change_effects

    if version["change_request_id"]:
        apply_change_effects(store, store.get("change_requests", version["change_request_id"]))
    store.lock(f"week:{version['week']}")
    if version["status"] != "PUBLISHED_PENDING_CONFIRMATION":
        raise Conflict("仅待确认版本可以生效")
    if now > version["confirmation_deadline"]:
        raise Conflict("确认期限已过，请管理员重新发布有效方案")
    snapshot, lessons, report = validate_version(store, version)
    if report["hard_conflicts"]:
        raise BusinessError("生效前资源复检失败，原有效课表保持不变", details=report)
    active = store.list("schedule_versions", {"week": snapshot.week, "status": "ACTIVE"})
    old_id = active[0]["id"] if active else None
    if old_id != version["base_version_id"]:
        raise StaleVersion("生效版本已变化，必须重新生成调整方案")
    if active and active[0]["version"] != version.get("base_version_revision"):
        raise StaleVersion("原课表内容已改变，不能应用过期的调整版本")
    originals = store.lessons(old_id) if old_id else ()
    ensure_future_changes(snapshot, lessons, originals, now)
    rows = store.list("course_sessions", {"version_id": version["id"]})
    confirmations = store.list("schedule_confirmations", {"session_id": [s["id"] for s in rows]})
    row_by_logical = {row["logical_id"]: row for row in rows}
    required = confirmation_requirements(lessons, originals)
    expected = {
        (row_by_logical[lesson.id]["id"], user_id, lesson.fingerprint)
        for lesson in lessons
        for user_id in required[lesson.id]
    }
    actual = {(c["session_id"], c["user_id"], c["fingerprint"]) for c in confirmations}
    if actual != expected or any(c["decision"] != "ACCEPTED" for c in confirmations):
        raise Conflict("尚未收齐必要确认")
    if any(row_by_logical[lesson.id]["fingerprint"] != lesson.fingerprint for lesson in lessons):
        raise StaleVersion("课次内容与确认指纹不一致")
    if not confirmations:
        raise Conflict("没有有效确认记录")
    store.replace_reservations(version["id"], old_id)
    if active:
        store.update(
            "schedule_versions", old_id, {"status": transition("ACTIVE", "SUPERSEDED")}, active[0]["version"]
        )
    updated = store.update(
        "schedule_versions",
        version["id"],
        {
            "status": transition(version["status"], "ACTIVE"),
            "activated_at": now,
            "report": json_record(report),
        },
        version["version"],
    )
    if version["change_request_id"]:
        change = store.get("change_requests", version["change_request_id"])
        if change["status"] == "WAITING_CONFIRMATION":
            store.add(
                "change_request_events",
                {
                    "change_request_id": change["id"],
                    "actor_id": actor.id,
                    "from_state": change["status"],
                    "to_state": "APPLIED",
                    "message": "确认齐全，事务生效",
                },
            )
            store.update("change_requests", change["id"], {"status": "APPLIED"}, change["version"])
    notify(
        store,
        [c["user_id"] for c in confirmations],
        "课表正式生效",
        f"{snapshot.week} 周课表已完成全部必要确认。",
        "/schedule",
    )
    audit(store, actor, "schedule.activate", "schedule_versions", version["id"], {"replaces": old_id})
    return updated


class ConfirmSchedule(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "STUDENT", "TEACHER")
        version = store.get("schedule_versions", data["version_id"])
        store.lock(f"week:{version['week']}")
        version = store.get("schedule_versions", version["id"], lock=True)
        if version["status"] != "PUBLISHED_PENDING_CONFIRMATION":
            raise Conflict("该版本不处于待确认状态")
        if self.clock.now() > version["confirmation_deadline"]:
            raise Conflict("确认已超时，请联系管理员处理")
        rows = store.list(
            "course_sessions",
            {
                "version_id": version["id"],
                "logical_id": data["session_ids"],
            },
        )
        if len(rows) != len(set(data["session_ids"])) or not rows:
            raise BusinessError("确认范围包含无效课次")
        decision = data["decision"]
        if decision not in {"ACCEPTED", "REJECTED"}:
            raise BusinessError("确认结果无效")
        if decision == "REJECTED" and not data.get("reason", "").strip():
            raise BusinessError("拒绝确认必须填写原因")
        for row in rows:
            owned = store.list("schedule_confirmations", {"session_id": row["id"], "user_id": actor.id})
            if not owned:
                raise Forbidden("只能确认本人参与或负责的课次")
            confirmation = owned[0]
            if confirmation["fingerprint"] != row["fingerprint"]:
                raise StaleVersion("课次内容已经改变，请重新查看")
            if confirmation["decision"] != "PENDING":
                if confirmation["decision"] != decision:
                    raise Conflict("已提交的确认决定不能直接覆盖")
                continue
            store.update(
                "schedule_confirmations",
                confirmation["id"],
                {
                    "decision": decision,
                    "reason": data.get("reason", ""),
                    "confirmed_at": self.clock.now(),
                },
                confirmation["version"],
            )
        if decision == "REJECTED":
            updated = store.update(
                "schedule_versions",
                version["id"],
                {
                    "status": transition(version["status"], "CANCELLED"),
                },
                version["version"],
            )
            request = store.add(
                "change_requests",
                {
                    "requester_id": actor.id,
                    "kind": "CONFIRMATION_REJECTED",
                    "week": version["week"],
                    "status": "SUBMITTED",
                    "reason": data["reason"],
                    "base_version_id": version["base_version_id"],
                    "payload": {"session_ids": data["session_ids"], "rejected_version_id": version["id"]},
                },
            )
            for source, target in (
                ("", "SUBMITTED"),
                ("SUBMITTED", "VALIDATING"),
                ("VALIDATING", "PENDING_ADMIN"),
            ):
                store.add(
                    "change_request_events",
                    {
                        "change_request_id": request["id"],
                        "actor_id": actor.id,
                        "from_state": source,
                        "to_state": target,
                        "message": data["reason"],
                    },
                )
            store.update("change_requests", request["id"], {"status": "PENDING_ADMIN"}, request["version"])
            if version["change_request_id"]:
                change = store.get("change_requests", version["change_request_id"])
                store.add(
                    "change_request_events",
                    {
                        "change_request_id": change["id"],
                        "actor_id": actor.id,
                        "from_state": change["status"],
                        "to_state": "CANCELLED",
                        "message": "受影响人员拒绝确认",
                    },
                )
                store.update("change_requests", change["id"], {"status": "CANCELLED"}, change["version"])
            notify(
                store,
                [u["id"] for u in store.list("users", {"role": "ADMIN", "active": True})],
                "课表确认被拒绝",
                f"异常事务编号：{request['id']}",
                "/changes",
            )
        else:
            all_rows = store.list("course_sessions", {"version_id": version["id"]})
            pending = store.list(
                "schedule_confirmations",
                {
                    "session_id": [r["id"] for r in all_rows],
                    "decision": "PENDING",
                },
            )
            updated = activate(store, actor, version, self.clock.now()) if not pending else version
        audit(
            store,
            actor,
            "schedule.confirm",
            "schedule_versions",
            version["id"],
            {
                "session_ids": data["session_ids"],
                "decision": decision,
                "reason": data.get("reason", ""),
            },
        )
        return {"id": version["id"], "status": updated["status"]}


class EditDraftSession(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "ADMIN")
        version = store.get("schedule_versions", data["version_id"], lock=True)
        if version["status"] not in {"DRAFT", "VALIDATED"} or version["version"] != data["version"]:
            raise Conflict("只能调整当前草稿或已校验版本")
        snapshot = store.snapshot(version["week"])
        source = store.lessons(version["id"])
        found = next((s for s in source if s.id == data["session_id"]), None)
        if found is None:
            raise BusinessError("课次不存在")
        slot = next((s for s in snapshot.slots if s.code == data["slot_code"]), None)
        if not slot:
            raise BusinessError("时段无效")
        adjusted = replace(
            found,
            teacher_id=data["teacher_id"],
            room_id=data["room_id"],
            day=date.fromisoformat(data["day"]),
            slot_code=slot.code,
            start=slot.start,
            end=slot.end,
        )
        lessons = tuple(adjusted if s.id == found.id else s for s in source)
        result = new_version(
            store,
            actor,
            snapshot,
            lessons,
            version["solver_status"],
            {"origin": "MANUAL_EDIT", "source_version": version["id"]},
            version["base_version_id"],
        )
        audit(
            store,
            actor,
            "schedule.manual_edit",
            "schedule_versions",
            result["id"],
            {"before": found.to_dict(), "after": adjusted.to_dict()},
        )
        return result


class CancelVersion(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "ADMIN")
        version = store.get("schedule_versions", data["version_id"], lock=True)
        store.lock(f"week:{version['week']}")
        target = transition(version["status"], "CANCELLED")
        result = store.update("schedule_versions", version["id"], {"status": target}, data["version"])
        if version["change_request_id"]:
            from eduflow.application.changes import move_change

            change = store.get("change_requests", version["change_request_id"], lock=True)
            if change["status"] in {"APPROVED", "WAITING_CONFIRMATION"}:
                move_change(store, actor, change, "CANCELLED", "关联候选课表已由管理员取消")
                notify(store, [change["requester_id"]], "调整方案已取消", "原生效课表保持有效。", "/changes")
        audit(store, actor, "schedule.cancel", "schedule_versions", version["id"])
        return result
