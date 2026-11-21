from datetime import date

from eduflow.application.commands import CommandHandler, audit, notify
from eduflow.application.operations import protect_active
from eduflow.application.ports import Record, RecordStore
from eduflow.domain.authorization import require_role
from eduflow.domain.entities import Principal
from eduflow.domain.errors import BusinessError, Conflict, Forbidden
from eduflow.domain.time_rules import week_days


class EnrollStudent(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "STUDENT")
        week = date.fromisoformat(data["week"])
        days = week_days(week)
        if days[-1] < self.clock.now().date():
            raise BusinessError("不能提交已结束周的选课需求")
        course = store.get("courses", data["course_id"])
        if course["status"] != "OPEN":
            raise BusinessError("课程尚未开放选课")
        profiles = store.list("student_profiles", {"user_id": actor.id})
        if not profiles:
            raise BusinessError("学生资料未完成")
        profile = profiles[0]
        if not (
            course["min_age"] <= profile["age"] <= course["max_age"]
            and course["min_grade"] <= profile["grade"] <= course["max_grade"]
        ):
            raise BusinessError("年龄或年级不符合课程要求")
        preferences = data["preferences"]
        if not preferences:
            raise BusinessError("至少选择一个意向时段")
        templates = {s["code"]: s for s in store.list("time_slot_templates", {"revision": 1})}
        for preference in preferences:
            slot = templates.get(preference["slot_code"])
            if not slot or not 0 <= preference["day_index"] <= 4:
                raise BusinessError("意向时段无效")
            if (course["course_group"] == "QA") != (slot["kind"] == "QA"):
                raise BusinessError("答疑只允许选择 S8；常规课只允许选择 S1—S7")
        store.lock(f"offering:{week}:{course['id']}")
        existing = store.list(
            "enrollment_requests",
            {
                "student_id": actor.id,
                "course_id": course["id"],
                "week": week,
            },
        )
        if existing and existing[0]["status"] not in {"CANCELLED", "EXCUSED", "REJECTED"}:
            raise Conflict("本周已经提交此课程需求，请在我的申请中查看")
        # Each new request is reviewed; candidate teachers may later be merged by administrator-approved plans.
        offering = store.add(
            "course_offerings",
            {
                "course_id": course["id"],
                "week": week,
                "name": f"{course['name']} · {week}",
                "status": "PENDING_REVIEW",
                "min_age": profile["age"],
                "max_age": profile["age"],
            },
        )
        # Hard allowed range is based on student availability; ranked preferences remain soft.
        allowed_slots = [
            slot for slot in templates.values() if (course["course_group"] == "QA") == (slot["kind"] == "QA")
        ]
        for day_index in range(5):
            for slot in allowed_slots:
                store.add(
                    "offering_slots",
                    {
                        "offering_id": offering["id"],
                        "day_index": day_index,
                        "slot_code": slot["code"],
                    },
                )
        request_values = {
            "student_id": actor.id,
            "course_id": course["id"],
            "offering_id": offering["id"],
            "week": week,
            "status": "PENDING_REVIEW",
        }
        if existing:
            request = store.update(
                "enrollment_requests", existing[0]["id"], request_values, existing[0]["version"]
            )
            store.remove_links("enrollment_time_preferences", {"request_id": request["id"]})
        else:
            request = store.add("enrollment_requests", request_values)
        for rank, preference in enumerate(preferences):
            store.add(
                "enrollment_time_preferences",
                {
                    "request_id": request["id"],
                    "rank": rank,
                    **preference,
                },
            )
        qualified = store.list("teacher_qualifications", {"course_id": course["id"]})
        teacher_ids = []
        for qualification in qualified:
            teacher = store.get("users", qualification["teacher_id"])
            if not teacher["active"]:
                continue
            teacher_ids.append(teacher["id"])
            store.add(
                "teacher_decisions",
                {
                    "teacher_id": teacher["id"],
                    "offering_id": offering["id"],
                    "decision": "PENDING",
                },
            )
        notify(
            store, teacher_ids, "新的开课审核", f"{course['name']} 有新选课需求，请审核授课意向。", "/reviews"
        )
        audit(store, actor, "enrollment.submit", "enrollment_requests", request["id"])
        return request


class ReviewOffering(CommandHandler):
    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        require_role(actor, "TEACHER")
        decision = store.get("teacher_decisions", data["decision_id"], lock=True)
        if decision["teacher_id"] != actor.id:
            raise Forbidden("不能审核其他教师的开课任务")
        target = data["decision"]
        if target not in {"ACCEPTED", "REJECTED", "ADJUST"}:
            raise BusinessError("审核结果无效")
        if target != "ACCEPTED" and not data.get("reason", "").strip():
            raise BusinessError("拒绝或调整时必须填写原因")
        offering = store.get("course_offerings", decision["offering_id"], lock=True)
        if not store.list(
            "teacher_qualifications", {"teacher_id": actor.id, "course_id": offering["course_id"]}
        ):
            raise Forbidden("当前教师无此课程资质")
        result = store.update(
            "teacher_decisions",
            decision["id"],
            {
                "decision": target,
                "reason": data.get("reason", ""),
            },
            data["version"],
        )
        accepted = store.list("teacher_decisions", {"offering_id": offering["id"], "decision": "ACCEPTED"})
        is_approved = bool(accepted)
        store.update(
            "course_offerings",
            offering["id"],
            {
                "status": "APPROVED" if is_approved else "PENDING_REVIEW",
            },
            offering["version"],
        )
        requests = store.list(
            "enrollment_requests", {"offering_id": offering["id"], "status": ["PENDING_REVIEW", "APPROVED"]}
        )
        destination = offering
        if target == "ACCEPTED":
            # Merge only compatible, reviewed demand. Existing approvals and active classes remain intact.
            store.lock(f"offering:{offering['week']}:{offering['course_id']}")
            others = store.list(
                "course_offerings",
                {
                    "course_id": offering["course_id"],
                    "week": offering["week"],
                    "status": "APPROVED",
                },
            )
            for other in others:
                if other["id"] == offering["id"]:
                    continue
                if store.list(
                    "teacher_decisions",
                    {
                        "offering_id": other["id"],
                        "teacher_id": actor.id,
                        "decision": "ACCEPTED",
                    },
                ):
                    destination = other
                    break
        for request in requests:
            store.update(
                "enrollment_requests",
                request["id"],
                {
                    "status": "APPROVED" if is_approved else "PENDING_REVIEW",
                    "offering_id": destination["id"],
                },
                request["version"],
            )
        if destination["id"] != offering["id"]:
            latest_offering = store.get("course_offerings", offering["id"])
            store.update("course_offerings", offering["id"], {"status": "MERGED"}, latest_offering["version"])
        protect_active(store)
        notify(
            store,
            [r["student_id"] for r in requests],
            "选课审核更新",
            f"教师审核结果：{target}。课表仍须排课、发布和双方确认。",
            "/applications",
        )
        admins = store.list("users", {"role": "ADMIN", "active": True})
        notify(store, [a["id"] for a in admins], "教师完成开课审核", offering["name"], "/demand")
        audit(store, actor, "offering.review", "teacher_decisions", decision["id"], {"decision": target})
        return result
