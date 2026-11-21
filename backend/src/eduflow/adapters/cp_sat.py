"""Joint CP-SAT model with factored teacher/room/slot decisions and optional intervals.

The logical x[c,t,r,s] is the conjunction of teacher[c,t], room[c,r], place[c,s].
Factoring preserves the feasible set while avoiding the Cartesian resource product.
"""

from __future__ import annotations

import math
import os
from collections import defaultdict
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ortools.sat.python import cp_model

from eduflow.domain.diagnostics import demand_diagnostics, uncovered_input_slots
from eduflow.domain.entities import Demand, Lesson, Snapshot, SolveResult
from eduflow.domain.metrics import schedule_metrics
from eduflow.domain.time_rules import slot_key, validate_templates, week_days
from eduflow.domain.validator import validate_schedule


@dataclass(frozen=True)
class Candidate:
    id: str
    offering_id: str
    course_id: str
    demands: tuple[Demand, ...]
    original: Lesson | None = None


class CpSatScheduler:
    def __init__(self, preference_budget: float = 300.0) -> None:
        self.preference_budget = min(300.0, max(0.01, preference_budget))

    def solve(
        self, snapshot: Snapshot, *, profile: str = "balanced", reference: tuple[Lesson, ...] = ()
    ) -> SolveResult:
        if profile == "preference_first":
            return self._preference_first(snapshot, reference)
        if profile != "balanced":
            return SolveResult("MODEL_INVALID", (), 0, diagnostics=("未知优化策略",))
        started = perf_counter()
        issues = demand_diagnostics(snapshot)
        if snapshot.rules.require_full_day_coverage:
            missing = uncovered_input_slots(snapshot)
            if missing:
                return SolveResult(
                    "INFEASIBLE",
                    (),
                    perf_counter() - started,
                    diagnostics=(
                        "全天覆盖缺少合法开班资源：" + "、".join(missing),
                        *dict.fromkeys(issues.values()),
                    ),
                    unmet_requests=tuple(d.id for d in snapshot.demands),
                )
        courses = {course.id: course for course in snapshot.courses}
        students = {student.id: student for student in snapshot.students}
        for day in range(5):
            if any(
                lesson.day.weekday() == day
                and lesson.status == "SCHEDULED"
                and courses.get(lesson.course_id)
                and courses[lesson.course_id].group == "MAIN"
                for lesson in snapshot.locked
            ):
                continue
            regular_keys = {f"{day}:{slot.code}" for slot in snapshot.slots if slot.kind == "REGULAR"}
            possible = any(
                demand.course_id in courses
                and courses[demand.course_id].group == "MAIN"
                and demand.student_id in students
                and any(
                    teacher.id in demand.approved_teachers
                    and demand.course_id in teacher.qualifications
                    and regular_keys
                    & demand.allowed
                    & teacher.available
                    & students[demand.student_id].available
                    for teacher in snapshot.teachers
                )
                for demand in snapshot.demands
            )
            if not possible:
                return SolveResult(
                    "INFEASIBLE",
                    (),
                    perf_counter() - started,
                    diagnostics=(
                        f"周{'一二三四五'[day]}不存在可授课的已审核主课需求，无法满足 H18 每日至少两节主课",
                    ),
                )
        if len(snapshot.demands) > 100 and not snapshot.baseline and not snapshot.locked:
            initial_budget = min(12.0, snapshot.rules.solve_seconds * 0.3)
            initial = self._solve(
                replace(snapshot, rules=replace(snapshot.rules, solve_seconds=initial_budget)),
                grouped=True,
                require_all=True,
            )
            if initial.status in {"OPTIMAL", "FEASIBLE"}:
                initial = replace(
                    initial,
                    status="FEASIBLE",
                    objective=None,
                    bound=None,
                    diagnostics=(*initial.diagnostics, "完整需求可行性预求解通过，尚未证明优化目标最优"),
                )
            else:
                fallback_budget = min(
                    6.0, max(0.001, snapshot.rules.solve_seconds - (perf_counter() - started)) * 0.15
                )
                initial = self._solve(
                    replace(snapshot, rules=replace(snapshot.rules, solve_seconds=fallback_budget)),
                    grouped=True,
                )
            if initial.lessons:
                by_offering: dict[str, list[Lesson]] = defaultdict(list)
                for lesson in initial.lessons:
                    by_offering[lesson.offering_id].append(lesson)
                ordered_hints = []
                for offering, cohort_lessons in by_offering.items():
                    for index, lesson in enumerate(
                        sorted(cohort_lessons, key=lambda s: (s.day, s.slot_code))
                    ):
                        ordered_hints.append(
                            replace(
                                lesson, id=str(uuid5(NAMESPACE_URL, f"{snapshot.week}/{offering}/{index}"))
                            )
                        )
                initial = replace(initial, lessons=tuple(ordered_hints))
            remaining = snapshot.rules.solve_seconds - (perf_counter() - started)
            if remaining <= 0.05:
                return replace(initial, wall_seconds=perf_counter() - started)
            refined = self._solve(
                replace(snapshot, rules=replace(snapshot.rules, solve_seconds=remaining)),
                hints=initial.lessons if initial.status in {"OPTIMAL", "FEASIBLE"} else (),
            )
            if refined.status in {"OPTIMAL", "FEASIBLE"}:
                if self._quality(snapshot, refined.lessons) >= self._quality(snapshot, initial.lessons):
                    return replace(refined, wall_seconds=perf_counter() - started)
            if initial.status in {"OPTIMAL", "FEASIBLE"}:
                return replace(
                    initial,
                    status="FEASIBLE",
                    wall_seconds=perf_counter() - started,
                    diagnostics=(
                        *initial.diagnostics,
                        "后续联合求解未改善需求覆盖，保留已验证的较优可行方案；未证明全模型最优",
                    ),
                )
            return replace(refined, wall_seconds=perf_counter() - started)
        return self._solve(snapshot)

    @staticmethod
    def _quality(snapshot: Snapshot, lessons: tuple[Lesson, ...]) -> tuple[int, int, int]:
        metrics = schedule_metrics(snapshot, lessons)
        return metrics["fulfilled_count"], metrics["first_preferred_count"], metrics["preferred_count"]

    @staticmethod
    def _ordered_hints(snapshot: Snapshot, lessons: tuple[Lesson, ...]) -> tuple[Lesson, ...]:
        pools: dict[str, list[Lesson]] = defaultdict(list)
        for lesson in lessons:
            if lesson.status not in {"CANCELLED", "SUSPENDED"}:
                pools[lesson.offering_id].append(lesson)
        return tuple(
            replace(lesson, id=str(uuid5(NAMESPACE_URL, f"{snapshot.week}/{offering}/{index}")))
            for offering, rows in sorted(pools.items())
            for index, lesson in enumerate(sorted(rows, key=lambda s: (s.day, s.slot_code, s.id)))
        )

    def _preference_first(self, snapshot: Snapshot, reference: tuple[Lesson, ...]) -> SolveResult:
        started = perf_counter()
        deadline = started + self.preference_budget
        incumbent = reference if reference and not validate_schedule(snapshot, reference) else ()
        stages: list[dict[str, Any]] = []
        if not incumbent:
            seed = self._solve(
                replace(
                    snapshot, rules=replace(snapshot.rules, solve_seconds=min(20, self.preference_budget))
                ),
                grouped=True,
                require_all=True,
                deadline=deadline,
            )
            stages.append({"stage": "feasibility", "status": seed.status, "seconds": seed.wall_seconds})
            if seed.status not in {"OPTIMAL", "FEASIBLE"}:
                return replace(seed, wall_seconds=perf_counter() - started, metadata={"stages": stages})
            incumbent = seed.lessons
        baseline = self._quality(snapshot, incumbent)
        all_required = len({d.id for d in snapshot.demands if d.status == "APPROVED"})
        phases = ([("served", 0.25)] if baseline[0] < all_required else []) + [
            ("first", 0.60),
            ("any", 0.65),
            ("quality", 1.0),
        ]
        for objective, fraction in phases:
            remaining = deadline - perf_counter()
            if remaining < 0.05:
                break
            served, first, any_preferred = self._quality(snapshot, incumbent)
            allowance = max(0.01, remaining * fraction)
            candidate = self._solve(
                replace(snapshot, rules=replace(snapshot.rules, solve_seconds=min(300, allowance))),
                hints=self._ordered_hints(snapshot, incumbent),
                require_all=served == all_required,
                objective=objective,
                minimum_served=served,
                minimum_first=first,
                minimum_any=any_preferred,
                deadline=deadline,
            )
            scores = self._quality(snapshot, candidate.lessons) if candidate.lessons else None
            accepted = (
                candidate.status in {"OPTIMAL", "FEASIBLE"}
                and scores is not None
                and scores >= (served, first, any_preferred)
                and scores[2] >= any_preferred
                and not validate_schedule(snapshot, candidate.lessons)
            )
            stages.append(
                {
                    "stage": objective,
                    "status": candidate.status,
                    "seconds": round(candidate.wall_seconds, 3),
                    "objective": candidate.objective,
                    "bound": candidate.bound,
                    "scores": scores,
                    "accepted": accepted,
                }
            )
            if accepted:
                incumbent = candidate.lessons
        final = self._quality(snapshot, incumbent)
        return SolveResult(
            "FEASIBLE",
            incumbent,
            perf_counter() - started,
            diagnostics=(
                "按需求、首选、任一偏好和资源质量分阶段求解；保留独立校验通过的非退步方案，未证明全局最优。",
            ),
            unmet_requests=tuple(
                d.id for d in snapshot.demands if d.id not in {r for s in incumbent for r in s.request_ids}
            ),
            metadata={
                "profile": "preference_first",
                "budget_seconds": self.preference_budget,
                "baseline": baseline,
                "final": final,
                "improved": final > baseline,
                "stages": stages,
            },
        )

    def _solve(
        self,
        snapshot: Snapshot,
        *,
        grouped: bool = False,
        hints: tuple[Lesson, ...] = (),
        require_all: bool = False,
        objective: str = "balanced",
        minimum_served: int = 0,
        minimum_first: int = 0,
        minimum_any: int = 0,
        deadline: float | None = None,
    ) -> SolveResult:
        started = perf_counter()
        validate_templates(snapshot.slots)
        days = week_days(snapshot.week)
        courses = {c.id: c for c in snapshot.courses}
        students = {s.id: s for s in snapshot.students}
        teachers = tuple(snapshot.teachers)
        rooms = tuple(r for r in snapshot.rooms if r.status == "OPEN")
        if not teachers or not rooms:
            return SolveResult("INFEASIBLE", (), 0, diagnostics=("没有可用教师或开放教室",))
        keys = [f"{day}:{s.code}" for day in range(5) for s in snapshot.slots]
        key_index = {key: index for index, key in enumerate(keys)}
        teacher_index = {t.id: i for i, t in enumerate(teachers)}
        room_index = {r.id: i for i, r in enumerate(rooms)}
        locked = tuple(s for s in snapshot.locked if s.status not in {"CANCELLED", "SUSPENDED"})
        locked_ids = {s.id for s in snapshot.locked}
        locked_requests = {r for s in locked for r in s.request_ids}
        baseline = tuple(
            s
            for s in snapshot.baseline
            if s.id not in locked_ids and s.status not in {"CANCELLED", "SUSPENDED"}
        )
        baseline_requests = {r for s in baseline for r in s.request_ids}
        demands = tuple(
            d
            for d in snapshot.demands
            if d.status == "APPROVED"
            and d.id not in locked_requests
            and d.course_id in courses
            and d.student_id in students
            and courses[d.course_id].accepts(students[d.student_id])
        )
        demand_map = {d.id: d for d in demands}
        candidates = []
        diagnostics = []
        for lesson in baseline:
            required_members = tuple(demand_map[r] for r in lesson.request_ids if r in demand_map)
            if len(required_members) != len(lesson.students):
                return SolveResult("INFEASIBLE", (), 0, diagnostics=("拟调整课次缺少已审核选课需求",))
            additional_demands = tuple(
                d for d in demands if d.id not in baseline_requests and d.offering_id == lesson.offering_id
            )
            candidates.append(
                Candidate(
                    lesson.id,
                    lesson.offering_id,
                    lesson.course_id,
                    (*required_members, *additional_demands),
                    lesson,
                )
            )
        pools: dict[str, list[Demand]] = defaultdict(list)
        for demand in demands:
            if demand.id not in baseline_requests:
                pools[demand.offering_id].append(demand)
        counts = {}
        for offering, pool in pools.items():
            course = courses[pool[0].course_id]
            capacities = [r.capacity for r in rooms if course.features <= r.features]
            pool_capacity = min(course.max_size, max(capacities, default=course.max_size))
            counts[offering] = min(len(pool) // course.min_size, math.ceil(len(pool) / pool_capacity) + 1)
            if objective != "balanced":
                hinted = sum(s.offering_id == offering for s in hints)
                counts[offering] = min(len(pool) // course.min_size, max(counts[offering] + 2, hinted))
        if snapshot.rules.require_full_day_coverage:
            # A capacity-based class count alone can leave S8 at four classes for five days.
            # Increase only within real approved demand / minimum-size limits.
            for qa, needed in ((True, 5), (False, 35)):
                eligible = [o for o, p in pools.items() if (courses[p[0].course_id].group == "QA") == qa]
                existing = sum((courses[s.course_id].group == "QA") == qa for s in (*locked, *baseline))
                while existing + sum(counts[o] for o in eligible) < needed:
                    expandable = [
                        o
                        for o in eligible
                        if counts[o] < len(pools[o]) // courses[pools[o][0].course_id].min_size
                    ]
                    if not expandable:
                        break
                    target = min(expandable, key=lambda o: (counts[o], o))
                    counts[target] += 1
        min_main_classes = snapshot.rules.min_main_daily * 5
        existing_main = sum(courses[s.course_id].group == "MAIN" for s in (*locked, *baseline))
        missing_main = max(
            0,
            min_main_classes
            - existing_main
            - sum(
                count
                for offering, count in counts.items()
                if courses[pools[offering][0].course_id].group == "MAIN"
            ),
        )
        for offering, pool in sorted(pools.items()):
            course = courses[pool[0].course_id]
            if course.group == "MAIN" and missing_main:
                extra_classes = min(missing_main, len(pool) // course.min_size - counts[offering])
                counts[offering] += extra_classes
                missing_main -= extra_classes
            for index in range(counts[offering]):
                ordered = sorted(
                    pool,
                    key=lambda d: (
                        students[d.student_id].grade,
                        d.preferred[0] if d.preferred else "",
                        d.student_id,
                    ),
                )
                if grouped:
                    # Candidate cohort preparation only; CP-SAT still assigns every resource and time.
                    size, remainder = divmod(len(ordered), counts[offering])
                    start = index * size + min(index, remainder)
                    cohort = ordered[start : start + size + int(index < remainder)]
                else:
                    cohort = pool
                candidates.append(
                    Candidate(
                        str(uuid5(NAMESPACE_URL, f"{snapshot.week}/{offering}/{index}")),
                        offering,
                        course.id,
                        tuple(cohort),
                    )
                )
            if len(pool) < course.min_size:
                diagnostics.append(f"{course.name}：已审核人数少于最低开班人数")

        model = cp_model.CpModel()
        teacher_intervals: dict[str, list[Any]] = defaultdict(list)
        room_intervals: dict[str, list[Any]] = defaultdict(list)
        student_intervals: dict[str, list[Any]] = defaultdict(list)
        teacher_busy: dict[tuple[str, int], list[Any]] = defaultdict(list)
        teacher_course: dict[tuple[str, int, str], list[Any]] = defaultdict(list)
        main_day: dict[int, list[Any]] = defaultdict(list)
        slot_sessions: dict[int, list[Any]] = defaultdict(list)
        request_members: dict[str, list[Any]] = defaultdict(list)
        candidate_members: dict[int, dict[str, Any]] = {}
        candidate_slot: dict[int, Any] = {}
        candidate_room: dict[int, Any] = {}
        candidate_teacher: dict[int, Any] = {}
        candidate_day: dict[int, Any] = {}
        candidate_open: dict[int, Any] = {}
        secondary: list[Any] = []
        locked_metrics = schedule_metrics(snapshot, locked)
        first_matches: list[Any] = [locked_metrics["first_preferred_count"]]
        any_matches: list[Any] = [locked_metrics["preferred_count"]]
        lower_bound = 1

        def soft(expression: Any, weight: int, maximum: int = 1) -> None:
            nonlocal lower_bound
            secondary.append(expression * weight)
            lower_bound += abs(weight) * maximum

        def conjunction(values: list[Any], name: str) -> Any:
            result = model.new_bool_var(name)
            for value in values:
                model.add(result <= value)
            model.add(result >= sum(values) - len(values) + 1)
            return result

        def equal(left: Any, right: Any, name: str) -> Any:
            result = model.new_bool_var(name)
            model.add(left == right).only_enforce_if(result)
            model.add(left != right).only_enforce_if(result.Not())
            return result

        for lesson in locked:
            index = key_index[slot_key(lesson.day, lesson.slot_code)]
            interval = model.new_fixed_size_interval_var(index, 1, "locked_" + lesson.id)
            teacher_intervals[lesson.teacher_id].append(interval)
            room_intervals[lesson.room_id].append(interval)
            teacher_busy[(lesson.teacher_id, index)].append(1)
            slot_sessions[index].append(1)
            teacher_course[(lesson.teacher_id, index, lesson.course_id)].append(1)
            for student_id in lesson.students:
                student_intervals[student_id].append(interval)
            if courses[lesson.course_id].group == "MAIN":
                main_day[lesson.day.weekday()].append(1)
        siblings: dict[str, tuple[Any, Any]] = {}
        for ci, candidate in enumerate(candidates):
            if deadline is not None and perf_counter() >= deadline:
                return SolveResult("UNKNOWN", (), perf_counter() - started, diagnostics=("建模已耗尽总预算",))
            course = courses[candidate.course_id]
            opened = model.new_bool_var(f"open_{ci}")
            placement = model.new_int_var(0, 39, f"slot_{ci}")
            day = model.new_int_var(0, 4, f"day_{ci}")
            model.add_division_equality(day, placement, 8)
            teacher_var = model.new_int_var(0, len(teachers) - 1, f"teacher_{ci}")
            room_var = model.new_int_var(0, len(rooms) - 1, f"room_{ci}")
            candidate_open[ci] = opened
            candidate_slot[ci] = placement
            candidate_teacher[ci] = teacher_var
            candidate_room[ci] = room_var
            candidate_day[ci] = day
            model.add(placement == 0).only_enforce_if(opened.Not())
            model.add(teacher_var == 0).only_enforce_if(opened.Not())
            model.add(room_var == 0).only_enforce_if(opened.Not())
            accepted = set().union(*(d.approved_teachers for d in candidate.demands))
            possible_times = [
                index
                for index, key in enumerate(keys)
                if (course.group == "QA") == (snapshot.slots[index % 8].kind == "QA")
                and course.duration == snapshot.slots[index % 8].minutes
                and sum(
                    key in students[d.student_id].available and key in d.allowed for d in candidate.demands
                )
                >= course.min_size
            ]
            allowed_teachers = [
                i for i, t in enumerate(teachers) if course.id in t.qualifications and t.id in accepted
            ]
            allowed_rooms = [
                i
                for i, r in enumerate(rooms)
                if course.features <= r.features and r.capacity >= course.min_size
            ]
            teacher_table = [
                (k, t) for k in possible_times for t in allowed_teachers if keys[k] in teachers[t].available
            ]
            room_table = [
                (k, r) for k in possible_times for r in allowed_rooms if keys[k] in rooms[r].available
            ]
            if not teacher_table or not room_table:
                model.add(opened == 0)
                diagnostics.append(f"{course.name}：缺少共同可用、已审核或设备兼容的授课资源")
            else:
                model.add_allowed_assignments([placement, teacher_var], teacher_table).only_enforce_if(opened)
                model.add_allowed_assignments([placement, room_var], room_table).only_enforce_if(opened)
            places = {}
            for k in possible_times:
                placed = model.new_bool_var(f"placed_{ci}_{k}")
                model.add(placement == k).only_enforce_if(placed)
                places[k] = placed
                slot_sessions[k].append(placed)
                if course.group == "MAIN":
                    main_day[k // 8].append(placed)
                if k % 8 in (4, 5, 6):
                    soft(placed, 3)
            model.add(sum(places.values()) == opened)
            teacher_presence = []
            for t in allowed_teachers:
                present = model.new_bool_var(f"teaches_{ci}_{t}")
                teacher_presence.append(present)
                model.add(teacher_var == t).only_enforce_if(present)
                teacher_intervals[teachers[t].id].append(
                    model.new_optional_fixed_size_interval_var(
                        placement, 1, present, f"teacher_interval_{ci}_{t}"
                    )
                )
                for k, placed in places.items():
                    assigned = conjunction([present, placed], f"busy_{ci}_{t}_{k}")
                    teacher_busy[(teachers[t].id, k)].append(assigned)
                    teacher_course[(teachers[t].id, k, course.id)].append(assigned)
            model.add(sum(teacher_presence) == opened)
            room_presence = []
            for r in allowed_rooms:
                present = model.new_bool_var(f"room_presence_{ci}_{r}")
                room_presence.append(present)
                model.add(room_var == r).only_enforce_if(present)
                room_intervals[rooms[r].id].append(
                    model.new_optional_fixed_size_interval_var(
                        placement, 1, present, f"room_interval_{ci}_{r}"
                    )
                )
                if rooms[r].kind in course.preferred_rooms:
                    soft(present, 4)
            model.add(sum(room_presence) == opened)
            members: dict[str, Any] = {}
            for demand in candidate.demands:
                member = model.new_bool_var(f"student_{ci}_{demand.id}")
                members[demand.id] = member
                request_members[demand.id].append(member)
                model.add(member <= opened)
                legal_times = [
                    (k,)
                    for k in possible_times
                    if keys[k] in students[demand.student_id].available and keys[k] in demand.allowed
                ]
                legal_teachers = [
                    (t,) for t in allowed_teachers if teachers[t].id in demand.approved_teachers
                ]
                if legal_times and legal_teachers:
                    model.add_allowed_assignments([placement], legal_times).only_enforce_if(member)
                    model.add_allowed_assignments([teacher_var], legal_teachers).only_enforce_if(member)
                else:
                    model.add(member == 0)
                if demand.allowed_rooms:
                    legal_room_indices = [(r,) for r in allowed_rooms if rooms[r].id in demand.allowed_rooms]
                    if legal_room_indices:
                        model.add_allowed_assignments([room_var], legal_room_indices).only_enforce_if(member)
                    else:
                        model.add(member == 0)
                student_intervals[demand.student_id].append(
                    model.new_optional_fixed_size_interval_var(
                        placement, 1, member, f"student_interval_{ci}_{demand.id}"
                    )
                )
                scores = [
                    max(1, 10 - demand.preferred.index(k)) if k in demand.preferred else 0 for k in keys
                ]
                preference = model.new_int_var(0, 10, f"preference_{ci}_{demand.id}")
                model.add_element(placement, scores, preference)
                earned = model.new_int_var(0, 10, f"earned_{ci}_{demand.id}")
                model.add(earned == preference).only_enforce_if(member)
                model.add(earned == 0).only_enforce_if(member.Not())
                soft(earned, 100, 10)
                if objective != "balanced":
                    first_matches.append(equal(earned, 10, f"first_{ci}_{demand.id}"))
                    any_match = model.new_bool_var(f"any_{ci}_{demand.id}")
                    model.add(earned >= 1).only_enforce_if(any_match)
                    model.add(earned == 0).only_enforce_if(any_match.Not())
                    any_matches.append(any_match)
                soft(member, demand.priority * 5)
                if candidate.original and demand.id in candidate.original.request_ids:
                    model.add(member == 1)
                elif grouped:
                    model.add(member == opened)
            candidate_members[ci] = members
            model.add(sum(members.values()) >= course.min_size * opened)
            model.add(sum(members.values()) <= course.max_size * opened)
            capacity = model.new_int_var(0, max(r.capacity for r in rooms), f"capacity_{ci}")
            model.add_element(room_var, [r.capacity for r in rooms], capacity)
            model.add(sum(members.values()) <= capacity)
            wasted = model.new_int_var(0, max(r.capacity for r in rooms), f"waste_{ci}")
            model.add(wasted == capacity - sum(members.values())).only_enforce_if(opened)
            model.add(wasted == 0).only_enforce_if(opened.Not())
            soft(wasted, -1, max(r.capacity for r in rooms))
            soft(opened, -20)
            if candidate.original:
                original = candidate.original
                model.add(opened == 1)
                old_slot = key_index[slot_key(original.day, original.slot_code)]
                soft(equal(placement, old_slot, f"same_slot_{ci}"), 150)
                if original.teacher_id in teacher_index:
                    soft(equal(teacher_var, teacher_index[original.teacher_id], f"same_teacher_{ci}"), 100)
                if original.room_id in room_index:
                    soft(equal(room_var, room_index[original.room_id], f"same_room_{ci}"), 50)
            elif not grouped:
                previous = siblings.get(candidate.offering_id)
                if previous:
                    model.add(previous[0] >= opened)
                    model.add(previous[1] <= placement).only_enforce_if(opened)
                siblings[candidate.offering_id] = (opened, placement)
        hint_map = {s.id: s for s in hints}
        for ci, candidate in enumerate(candidates):
            hint = hint_map.get(candidate.id) or candidate.original
            if hint:
                model.add_hint(candidate_open[ci], 1)
                model.add_hint(candidate_slot[ci], key_index[slot_key(hint.day, hint.slot_code)])
                if hint.teacher_id in teacher_index:
                    model.add_hint(candidate_teacher[ci], teacher_index[hint.teacher_id])
                if hint.room_id in room_index:
                    model.add_hint(candidate_room[ci], room_index[hint.room_id])
                for request_id, member in candidate_members[ci].items():
                    model.add_hint(member, int(request_id in hint.request_ids))
            elif hints:
                model.add_hint(candidate_open[ci], 0)
                model.add_hint(candidate_slot[ci], 0)
                model.add_hint(candidate_teacher[ci], 0)
                model.add_hint(candidate_room[ci], 0)
                for member in candidate_members[ci].values():
                    model.add_hint(member, 0)
        for variables in request_members.values():
            model.add(sum(variables) <= 1)
        if require_all:
            for demand in demands:
                model.add(sum(request_members[demand.id]) == 1)
        for intervals in (*teacher_intervals.values(), *room_intervals.values(), *student_intervals.values()):
            model.add_no_overlap(intervals)
        for teacher in teachers:
            busy = []
            for k in range(40):
                at_slot = model.new_bool_var(f"teacher_busy_{teacher.id}_{k}")
                model.add(at_slot == sum(teacher_busy[(teacher.id, k)]))
                busy.append(at_slot)
            model.add(sum(busy) <= snapshot.rules.max_weekly)
            weekly = model.new_int_var(0, 24, f"weekly_{teacher.id}")
            model.add(weekly == sum(busy))
            square = model.new_int_var(0, 576, f"weekly_square_{teacher.id}")
            model.add_multiplication_equality(square, [weekly, weekly])
            soft(square, -2, 576)
            for day_index in range(5):
                daily = busy[day_index * 8 : day_index * 8 + 8]
                model.add(sum(daily) <= snapshot.rules.max_daily)
                model.add(sum(daily[:4]) <= 3)
                model.add(sum(daily[4:]) <= 3)
                for start in range(8 - snapshot.rules.max_consecutive):
                    model.add(
                        sum(daily[start : start + snapshot.rules.max_consecutive + 1])
                        <= snapshot.rules.max_consecutive
                    )
                for start in range(7):
                    soft(
                        conjunction(
                            [daily[start], daily[start + 1]], f"consecutive_{teacher.id}_{day_index}_{start}"
                        ),
                        -12,
                    )
                load = model.new_int_var(0, 5, f"daily_load_{teacher.id}_{day_index}")
                model.add(load == sum(daily))
                daily_square = model.new_int_var(0, 25, f"daily_square_{teacher.id}_{day_index}")
                model.add_multiplication_equality(daily_square, [load, load])
                soft(daily_square, -3, 25)
                fifth_lesson = model.new_bool_var(f"fifth_lesson_{teacher.id}_{day_index}")
                model.add(load == 5).only_enforce_if(fifth_lesson)
                model.add(load <= 4).only_enforce_if(fifth_lesson.Not())
                soft(fifth_lesson, -25)
        for day_index in range(5):
            model.add(sum(main_day[day_index]) >= snapshot.rules.min_main_daily)
        if snapshot.rules.require_full_day_coverage:
            for k in range(40):
                model.add(sum(slot_sessions[k]) >= 1)
        for (teacher_id, k, course_id), variables in tuple(teacher_course.items()):
            next_day = teacher_course.get((teacher_id, k + 8, course_id)) if k < 32 else None
            if next_day:
                soft(
                    conjunction([sum(variables), sum(next_day)], f"repeated_{teacher_id}_{k}_{course_id}"), -2
                )
        # Pairwise same-day room-change cost; shared relation literals keep this small.
        student_candidates: dict[str, list[tuple[int, Any]]] = defaultdict(list)
        for ci, members in candidate_members.items():
            for demand_id, member in members.items():
                student_candidates[demand_map[demand_id].student_id].append((ci, member))
        relation: dict[tuple[int, int], Any] = {}
        for student_id, choices in student_candidates.items():
            for offset, (left, lm) in enumerate(choices):
                for right, rm in choices[offset + 1 :]:
                    if candidates[left].course_id == candidates[right].course_id:
                        continue
                    pair = (left, right)
                    if pair not in relation:
                        same_day = equal(
                            candidate_day[left], candidate_day[right], f"same_day_{left}_{right}"
                        )
                        same_room = equal(
                            candidate_room[left], candidate_room[right], f"same_room_{left}_{right}"
                        )
                        relation[pair] = conjunction(
                            [same_day, same_room.Not()], f"room_change_{left}_{right}"
                        )
                    soft(conjunction([lm, rm, relation[pair]], f"walking_{student_id}_{left}_{right}"), -3)
        served = sum(value for values in request_members.values() for value in values)
        model.add(served + len(locked_requests) >= minimum_served)
        if objective != "balanced":
            model.add(sum(first_matches) >= minimum_first)
            model.add(sum(any_matches) >= minimum_any)
        if objective == "served":
            model.maximize(served)
        elif objective == "first":
            model.maximize(sum(first_matches))
        elif objective == "any":
            model.maximize(sum(any_matches))
        elif objective == "quality":
            model.maximize(sum(secondary))
        elif not require_all:
            model.maximize(served * (lower_bound + 1) + sum(secondary))
        invalid = model.validate()
        if invalid:
            return SolveResult("MODEL_INVALID", (), 0, diagnostics=(invalid,))
        solver = cp_model.CpSolver()
        if deadline is not None and perf_counter() >= deadline:
            return SolveResult("UNKNOWN", (), perf_counter() - started)
        solver.parameters.max_time_in_seconds = max(
            0.001,
            min(
                snapshot.rules.solve_seconds - (perf_counter() - started),
                deadline - perf_counter() if deadline is not None else snapshot.rules.solve_seconds,
            ),
        )
        solver.parameters.num_search_workers = min(8, os.cpu_count() or 1)
        solver.parameters.random_seed = 42
        status = solver.solve(model)
        name = {
            cp_model.OPTIMAL: "OPTIMAL",
            cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE",
            cp_model.UNKNOWN: "UNKNOWN",
            cp_model.MODEL_INVALID: "MODEL_INVALID",
        }.get(status, "UNKNOWN")
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            diagnostics.append(
                "求解预算内未找到可行解，不能发布"
                if status == cp_model.UNKNOWN
                else "共同可用时间、每日主课或教师与教室资源无法同时满足硬约束"
            )
            return SolveResult(name, (), solver.wall_time, diagnostics=tuple(dict.fromkeys(diagnostics)))
        lessons = list(snapshot.locked)
        fulfilled = set(locked_requests)
        for ci, candidate in enumerate(candidates):
            if not solver.boolean_value(candidate_open[ci]):
                continue
            k = solver.value(candidate_slot[ci])
            chosen = [
                demand_map[r] for r, member in candidate_members[ci].items() if solver.boolean_value(member)
            ]
            slot = snapshot.slots[k % 8]
            lessons.append(
                Lesson(
                    candidate.id,
                    candidate.offering_id,
                    candidate.course_id,
                    teachers[solver.value(candidate_teacher[ci])].id,
                    rooms[solver.value(candidate_room[ci])].id,
                    days[k // 8],
                    slot.code,
                    slot.start,
                    slot.end,
                    tuple(d.student_id for d in chosen),
                    tuple(d.id for d in chosen),
                )
            )
            fulfilled.update(d.id for d in chosen)
        violations = validate_schedule(snapshot, lessons)
        if violations:
            return SolveResult(
                "MODEL_INVALID",
                (),
                solver.wall_time,
                diagnostics=tuple(f"{v.code}: {v.message}" for v in violations),
            )
        return SolveResult(
            name,
            tuple(lessons),
            solver.wall_time,
            solver.objective_value,
            solver.best_objective_bound,
            tuple(dict.fromkeys(diagnostics)),
            tuple(d.id for d in snapshot.demands if d.id not in fulfilled),
        )
