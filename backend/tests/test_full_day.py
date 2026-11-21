from dataclasses import replace

import pytest

from eduflow.adapters.cp_sat import CpSatScheduler
from eduflow.adapters.demo import demo_snapshot
from eduflow.domain.diagnostics import demand_diagnostics
from eduflow.domain.entities import Snapshot
from eduflow.domain.metrics import schedule_metrics
from eduflow.domain.validator import validate_schedule
from support.full_day import full_week


def test_demo_keeps_668_requests_with_course_specific_preferences():
    snapshot = demo_snapshot()
    assert len(snapshot.demands) == 668
    courses = {course.id: course for course in snapshot.courses}
    for demand in snapshot.demands:
        assert all(key in demand.allowed for key in demand.preferred)
        assert all(
            key.endswith("S8") == (courses[demand.course_id].group == "QA") for key in demand.preferred
        )


def test_full_day_independent_scan_and_rest_metrics():
    snapshot, lessons = full_week()
    assert not validate_schedule(snapshot, lessons)
    metrics = schedule_metrics(snapshot, lessons)
    assert metrics["covered_slots"] == metrics["required_slots"] == 40
    assert metrics["minimum_adjacent_day_rest"] == 8
    assert all(r["rest_slots"] == 4 for r in metrics["teacher_daily_rest"])
    assert metrics["satisfaction_rate"] == 1
    assert "H23" in {v.code for v in validate_schedule(snapshot, lessons[1:])}
    paused = (replace(lessons[0], status="SUSPENDED"), *lessons[1:])
    assert "H23" in {v.code for v in validate_schedule(snapshot, paused)}


def test_solver_expands_real_classes_for_40_slots_and_five_qa_days():
    snapshot, _ = full_week()
    result = CpSatScheduler().solve(snapshot)
    assert result.status in {"FEASIBLE", "OPTIMAL"}, result.diagnostics
    assert not validate_schedule(snapshot, result.lessons)
    assert not result.unmet_requests
    assert len(result.lessons) == 40
    assert len({s.day for s in result.lessons if s.slot_code == "S8"}) == 5


@pytest.mark.parametrize("resource", ["qa", "teachers", "rooms"])
def test_full_day_missing_supply_has_no_publishable_solution(resource):
    snapshot, _ = full_week()
    if resource == "qa":
        snapshot = replace(snapshot, demands=tuple(d for d in snapshot.demands if d.course_id != "qa"))
    else:
        snapshot = replace(snapshot, **{resource: ()})
    result = CpSatScheduler().solve(snapshot)
    assert result.status == "INFEASIBLE" and not result.lessons
    assert "全天覆盖" in result.diagnostics[0]


def test_inconsistent_slots_stay_in_demand_denominator():
    snapshot, _ = full_week()
    bad = replace(snapshot.demands[0], allowed=frozenset({"0:S8"}))
    snapshot = replace(snapshot, demands=(bad, *snapshot.demands[1:]))
    assert "课程类型不一致" in demand_diagnostics(snapshot)[bad.id]
    assert schedule_metrics(snapshot, ())["request_count"] == 120


def test_historical_snapshot_does_not_acquire_new_rules():
    snapshot, lessons = full_week()
    raw = snapshot.to_dict()
    del raw["rules"]["require_full_day_coverage"]
    historical = Snapshot.from_dict(raw)
    assert not historical.rules.require_full_day_coverage
    assert "H23" not in {v.code for v in validate_schedule(historical, lessons[1:])}
