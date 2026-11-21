import json
import os
from dataclasses import asdict, replace
from pathlib import Path
from time import perf_counter

import pytest

from eduflow.adapters.cp_sat import CpSatScheduler
from eduflow.adapters.demo import demo_snapshot
from eduflow.domain.validator import validate_schedule


def test_preference_first_preserves_valid_incumbent_when_budget_exhausted(legal_week):
    snapshot, lessons = legal_week
    result = CpSatScheduler(preference_budget=0.01).solve(
        snapshot, profile="preference_first", reference=lessons
    )
    assert result.status == "FEASIBLE"
    assert not validate_schedule(snapshot, result.lessons)
    assert result.metadata["final"] >= result.metadata["baseline"]
    assert len(result.metadata["final"]) == 3


def test_preference_stages_do_not_sacrifice_fulfilment_or_matches(legal_week):
    snapshot, lessons = legal_week
    result = CpSatScheduler(preference_budget=2).solve(
        snapshot, profile="preference_first", reference=lessons
    )
    assert result.status == "FEASIBLE"
    assert not validate_schedule(snapshot, result.lessons)
    assert result.metadata["final"] == (21, 21, 21)
    assert [s["stage"] for s in result.metadata["stages"]][0] == "first"


def test_T30_actual_solver_without_llm(legal_week):
    snapshot, _ = legal_week
    result = CpSatScheduler().solve(snapshot)
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.diagnostics
    assert validate_schedule(snapshot, result.lessons) == ()
    assert len({r for lesson in result.lessons for r in lesson.request_ids}) == len(snapshot.demands)


def test_T32_infeasible_is_not_success(legal_week):
    snapshot, _ = legal_week
    result = CpSatScheduler().solve(replace(snapshot, teachers=()))
    assert result.status == "INFEASIBLE"
    assert not result.lessons
    assert result.diagnostics


def test_local_reschedule_keeps_unrelated_sessions(legal_week):
    snapshot, lessons = legal_week
    snapshot = replace(
        snapshot,
        locked=lessons[1:],
        baseline=(lessons[0],),
        teachers=tuple(
            replace(t, available=t.available - {"0:S1"}) if t.id == "teacher-a" else t
            for t in snapshot.teachers
        ),
    )
    result = CpSatScheduler().solve(snapshot)
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.diagnostics
    assert validate_schedule(snapshot, result.lessons) == ()
    assert {s.id: s for s in result.lessons if s.id != lessons[0].id} == {s.id: s for s in lessons[1:]}


def test_T32_native_timeout_is_unknown(legal_week):
    snapshot, _ = legal_week
    result = CpSatScheduler().solve(replace(snapshot, rules=replace(snapshot.rules, solve_seconds=0.000001)))
    assert result.status == "UNKNOWN"
    assert result.lessons == ()


@pytest.mark.parametrize("kind", ["conflict", "resource_shortage"])
def test_seeded_failure_scenarios_do_not_relax_hard_rules(kind):
    from eduflow.adapters.demo import scenario_snapshot

    result = CpSatScheduler().solve(scenario_snapshot(kind, seconds=2))
    assert result.status == "INFEASIBLE"
    assert not result.lessons


def test_explicit_room_requirement_survives_solver_and_independent_scan(legal_week):
    snapshot, lessons = legal_week
    restricted = replace(
        snapshot, demands=tuple(replace(d, allowed_rooms=frozenset({"room-b"})) for d in snapshot.demands)
    )
    assert "H06" in {v.code for v in validate_schedule(restricted, lessons)}
    result = CpSatScheduler().solve(restricted)
    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert all(lesson.room_id == "room-b" for lesson in result.lessons)
    assert not validate_schedule(restricted, result.lessons)


@pytest.mark.performance
def test_T33_real_220_student_workload():
    started = perf_counter()
    snapshot = demo_snapshot(seconds=60)
    assert len(snapshot.students) == 220
    assert len(snapshot.teachers) == 16
    assert len(snapshot.rooms) == 10
    assert len(snapshot.demands) == 668
    result = CpSatScheduler().solve(snapshot)
    from eduflow.domain.metrics import schedule_metrics

    artifact = Path(
        os.environ.get("EDUFLOW_SOLVER_ARTIFACT")
        or Path(__file__).resolve().parents[2] / ".runtime" / "private" / "evidence" / "solver-220.json"
    )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "solver_status": result.status,
                "solver_seconds": result.wall_seconds,
                "total_seconds": round(perf_counter() - started, 3),
                "objective": result.objective,
                "bound": result.bound,
                "diagnostics": result.diagnostics,
                "metrics": schedule_metrics(snapshot, result.lessons),
                "violations": [asdict(v) for v in validate_schedule(snapshot, result.lessons)]
                if result.lessons
                else [],
                "lessons": [s.to_dict() for s in result.lessons],
            },
            default=str,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.diagnostics
    assert validate_schedule(snapshot, result.lessons) == ()
    assert sum(len(s.students) for s in result.lessons) > 0
    assert not result.unmet_requests, "Known feasible 220-student fixture must not lose demand coverage"
    assert schedule_metrics(snapshot, result.lessons)["covered_slots"] == 40
