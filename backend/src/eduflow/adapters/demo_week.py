"""Create one explicitly fictional full-day week from persisted course definitions."""

import random
from dataclasses import asdict
from datetime import date, timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.orm import Session

from eduflow.adapters.repository import SqlRecords
from eduflow.domain.errors import BusinessError, Conflict
from eduflow.domain.time_rules import Rules


def create_full_day_demo(
    session: Session, organization_id: str, source_week: date | None = None, target_week: date | None = None
) -> dict:
    store = SqlRecords(session, organization_id)
    store.lock("resources")
    org = store.organization()
    if not org["seeded"] or org["slug"] != "demo":
        raise BusinessError("只允许在明确的 demo 演示机构创建模拟周")
    previous = store.list("audit_logs", {"action": "demo.full_day_created"})
    if previous and target_week is None:
        latest = max(previous, key=lambda r: r["created_at"])
        return {**latest["detail"], "created": False}
    requests = store.list("enrollment_requests", {"status": "APPROVED"})
    if not requests:
        raise BusinessError("需要已有演示需求作为独立周的数据来源")
    source_week = source_week or max(r["week"] for r in requests)
    source = sorted((r for r in requests if r["week"] == source_week), key=lambda r: r["id"])
    all_weeks = [r["week"] for r in store.list("course_offerings")] + [
        r["week"] for r in store.list("schedule_versions")
    ]
    target_week = target_week or max([source_week, *all_weeks]) + timedelta(days=7)
    if target_week.weekday() != 0 or target_week <= source_week:
        raise BusinessError("独立演示周必须是来源周之后的周一")
    if any(
        store.list(entity, {"week": target_week})
        for entity in ("course_offerings", "enrollment_requests", "schedule_versions")
    ):
        raise Conflict("目标周已有业务数据，禁止覆盖")
    courses = {c["id"]: c for c in store.list("courses")}
    qualifications = store.list("teacher_qualifications")
    active = {u["id"] for u in store.list("users", {"role": "TEACHER", "active": True})}
    availability: dict[str, set[str]] = {}
    for r in store.list("availability_rules", limit=50000):
        availability.setdefault(r["owner_id"], set()).add(f"{r['day_index']}:{r['slot_code']}")
    slots = store.list("time_slot_templates", {"revision": 1})
    offerings = {}
    rng = random.Random(42)

    def identifier(kind: str, value: str) -> str:
        return str(
            uuid5(NAMESPACE_URL, f"eduflow/full-day/v1/{organization_id}/{target_week}/{kind}/{value}")
        )

    for course_id in sorted({r["course_id"] for r in source}):
        c = courses[course_id]
        legal = [
            f"{d}:{s['code']}"
            for d in range(5)
            for s in slots
            if (c["course_group"] == "QA") == (s["kind"] == "QA") and c["duration"] == s["duration_minutes"]
        ]
        if not legal:
            raise BusinessError("课程类型与时长没有合法授课时段")
        offering = store.add(
            "course_offerings",
            {
                "id": identifier("offering", course_id),
                "course_id": course_id,
                "week": target_week,
                "name": f"{c['name']} · 全天演示周 {target_week}",
                "status": "APPROVED",
            },
        )
        offerings[course_id] = (offering["id"], legal)
        for key in legal:
            day, code = key.split(":")
            store.add(
                "offering_slots", {"offering_id": offering["id"], "day_index": int(day), "slot_code": code}
            )
        teachers = {
            q["teacher_id"]
            for q in qualifications
            if q["course_id"] == course_id and q["teacher_id"] in active
        }
        if not teachers:
            raise BusinessError("演示课程没有具备资质的教师，不能虚构审核")
        for teacher_id in sorted(teachers):
            store.add(
                "teacher_decisions",
                {
                    "offering_id": offering["id"],
                    "teacher_id": teacher_id,
                    "decision": "ACCEPTED",
                    "reason": "明确标识的模拟数据：独立全天演示周审核同意",
                },
            )
    for r in source:
        offering_id, legal = offerings[r["course_id"]]
        request = store.add(
            "enrollment_requests",
            {
                "id": identifier("request", r["id"]),
                "student_id": r["student_id"],
                "course_id": r["course_id"],
                "offering_id": offering_id,
                "week": target_week,
                "status": "APPROVED",
                "priority": r["priority"],
            },
        )
        choices = sorted(set(legal) & availability.get(r["student_id"], set()))
        if not choices:
            raise BusinessError("演示学生没有合法可用时段")
        for rank, key in enumerate(rng.sample(choices, min(3, len(choices)))):
            day, code = key.split(":")
            store.add(
                "enrollment_time_preferences",
                {"request_id": request["id"], "day_index": int(day), "slot_code": code, "rank": rank},
            )
    store.set_rules(asdict(Rules(**{**org["rules"], "require_full_day_coverage": True})))
    result = {
        "source_week": str(source_week),
        "week": str(target_week),
        "request_count": len(source),
        "seed": 42,
    }
    store.add(
        "audit_logs",
        {
            "action": "demo.full_day_created",
            "entity_type": "organizations",
            "entity_id": organization_id,
            "detail": result,
        },
    )
    return {**result, "created": True}
