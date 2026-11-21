"""Explicit lifecycle boundary for permanently removing unpublished candidates."""

from eduflow.application.commands import CommandHandler, audit
from eduflow.application.ports import Record, RecordStore
from eduflow.domain.authorization import require_role
from eduflow.domain.entities import Principal
from eduflow.domain.errors import Conflict, StaleVersion


def deletion_block(store: RecordStore, version: Record) -> str:
    if (
        version["status"] not in {"DRAFT", "VALIDATED", "CANCELLED"}
        or version.get("published_at")
        or version.get("activated_at")
    ):
        return "只有从未发布的草稿、已校验或已取消候选可以删除"
    if store.list("schedule_versions", {"base_version_id": version["id"]}):
        return "该候选被其他课表版本引用"
    if store.list("change_requests", {"base_version_id": version["id"]}):
        return "该候选被异常事务作为基础版本引用"
    if version.get("change_request_id"):
        change = store.get("change_requests", version["change_request_id"])
        if change["status"] not in {"APPLIED", "FAILED", "CANCELLED", "REJECTED"}:
            return "关联异常事务仍在处理中，请先结束该事务"
    for job in store.list("solve_jobs", {"version_id": version["id"]}):
        if job["status"] not in {"COMPLETED", "FAILED", "STALE"}:
            return "关联求解任务尚未结束"
    sessions = store.list("course_sessions", {"version_id": version["id"]})
    for entity in (
        "schedule_confirmations",
        "resource_reservations",
        "attendance_records",
        "course_feedback",
    ):
        if sessions and store.list(entity, {"session_id": [s["id"] for s in sessions]}):
            return "该候选存在确认、资源预订、出勤或反馈记录，必须保留业务历史"
    return ""


class DeleteDraftVersion(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "ADMIN")
        version = store.get("schedule_versions", data["version_id"], lock=True)
        store.lock(f"week:{version['week']}")
        if version["version"] != data["version"]:
            raise StaleVersion("课表版本已更新，请刷新后再删除")
        if reason := deletion_block(store, version):
            raise Conflict(reason)
        for job in store.list("solve_jobs", {"version_id": version["id"]}):
            summary = {
                k: v
                for k, v in (job.get("result") or {}).items()
                if k in {"solver_status", "wall_seconds", "objective", "bound"}
            }
            summary.update(candidate_deleted=True, deleted_version_id=version["id"])
            store.update(
                "solve_jobs",
                job["id"],
                {"version_id": None, "snapshot": {}, "result": summary},
                job["version"],
            )
        count = len(store.lessons(version["id"]))
        store.delete_candidate(version["id"], version["version"])
        audit(
            store,
            actor,
            "schedule.delete_draft",
            "schedule_versions",
            version["id"],
            {"week": version["week"], "status": version["status"], "session_count": count},
        )
        return {"id": version["id"], "deleted": True}
