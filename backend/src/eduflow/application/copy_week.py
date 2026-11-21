from datetime import date

from eduflow.application.commands import CommandHandler, audit, notify
from eduflow.application.ports import Record, RecordStore
from eduflow.domain.authorization import require_role
from eduflow.domain.entities import Principal
from eduflow.domain.errors import BusinessError, Conflict
from eduflow.domain.time_rules import week_days


class CopyWeek(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "ADMIN")
        source = date.fromisoformat(data["source_week"])
        target = date.fromisoformat(data["target_week"])
        week_days(source)
        week_days(target)
        if target <= source:
            raise BusinessError("目标周必须晚于来源周")
        store.lock(f"week:{target}")
        if store.list("enrollment_requests", {"week": target}):
            raise Conflict("目标周已有选课需求，不能覆盖")
        mappings = {}
        recipients = set()
        for offering in store.list("course_offerings", {"week": source, "status": "APPROVED"}):
            created = store.add(
                "course_offerings",
                {
                    "course_id": offering["course_id"],
                    "week": target,
                    "name": f"{offering['name']} · 复制",
                    "status": "PENDING_REVIEW",
                    "min_age": offering["min_age"],
                    "max_age": offering["max_age"],
                },
            )
            mappings[offering["id"]] = created["id"]
            for slot in store.list("offering_slots", {"offering_id": offering["id"]}):
                store.add(
                    "offering_slots",
                    {
                        "offering_id": created["id"],
                        "day_index": slot["day_index"],
                        "slot_code": slot["slot_code"],
                    },
                )
            for decision in store.list(
                "teacher_decisions", {"offering_id": offering["id"], "decision": "ACCEPTED"}
            ):
                recipients.add(decision["teacher_id"])
                store.add(
                    "teacher_decisions",
                    {
                        "offering_id": created["id"],
                        "teacher_id": decision["teacher_id"],
                        "decision": "PENDING",
                    },
                )
        total = 0
        for request in store.list("enrollment_requests", {"week": source, "status": "APPROVED"}):
            if request["offering_id"] not in mappings:
                continue
            created = store.add(
                "enrollment_requests",
                {
                    "student_id": request["student_id"],
                    "course_id": request["course_id"],
                    "offering_id": mappings[request["offering_id"]],
                    "week": target,
                    "status": "PENDING_REVIEW",
                },
            )
            for preference in store.list("enrollment_time_preferences", {"request_id": request["id"]}):
                store.add(
                    "enrollment_time_preferences",
                    {
                        "request_id": created["id"],
                        "day_index": preference["day_index"],
                        "slot_code": preference["slot_code"],
                        "rank": preference["rank"],
                    },
                )
            total += 1
        notify(store, list(recipients), "新一周开课审核", f"{target} 已复制需求，请重新审核。", "/reviews")
        audit(
            store,
            actor,
            "schedule.copy_week",
            "organizations",
            actor.organization_id,
            {"source_week": source, "target_week": target, "request_count": total},
        )
        return {"request_count": total, "target_week": target, "status": "PENDING_REVIEW"}
