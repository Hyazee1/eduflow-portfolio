from dataclasses import replace
from datetime import timedelta

import pytest

from eduflow.domain.authorization import require_owner, require_role
from eduflow.domain.entities import Principal
from eduflow.domain.errors import BusinessError, Forbidden, InvalidTransition
from eduflow.domain.states import transition
from eduflow.domain.time_rules import DEFAULT_SLOTS, Rules, to_minutes, validate_templates
from eduflow.domain.validator import validate_schedule


@pytest.mark.parametrize(
    "code,start,end,duration",
    [
        ("S3", "10:00", "10:45", 45),
        ("S4", "10:55", "11:40", 45),
        ("S7", "16:00", "16:45", 45),
        ("S8", "16:55", "17:30", 35),
    ],
)
def test_T01_T02_T05_T06_T07_canonical_slots(code, start, end, duration):
    slot = next(s for s in DEFAULT_SLOTS if s.code == code)
    assert (slot.start, slot.end, slot.minutes) == (start, end, duration)


def test_T03_T04_twenty_minute_breaks():
    slots = {s.code: s for s in DEFAULT_SLOTS}
    assert to_minutes(slots["S3"].start) - to_minutes(slots["S2"].end) == 20
    assert to_minutes(slots["S7"].start) - to_minutes(slots["S6"].end) == 20


def test_legal_reference_week(legal_week):
    snapshot, lessons = legal_week
    assert validate_schedule(snapshot, lessons) == ()


@pytest.mark.parametrize(
    "index,changes,expected",
    [
        (0, {"slot_code": "S8", "start": "16:55", "end": "17:30"}, "H11"),
        (-1, {"slot_code": "S1", "start": "08:00", "end": "08:45"}, "H10"),
        (0, {"start": "09:50", "end": "10:35"}, "H12"),
        (0, {"start": "17:30", "end": "17:40"}, "H12"),
    ],
)
def test_T08_T09_T10_T21_T22_reject_illegal_times(legal_week, index, changes, expected):
    snapshot, lessons = legal_week
    altered = list(lessons)
    altered[index] = replace(altered[index], **changes)
    assert expected in {v.code for v in validate_schedule(snapshot, altered)}


def test_T15_T16_teacher_and_room_overlap(legal_week):
    snapshot, lessons = legal_week
    duplicate = replace(
        lessons[1], teacher_id=lessons[0].teacher_id, slot_code="S1", start="08:00", end="08:45"
    )
    codes = {v.code for v in validate_schedule(snapshot, (lessons[0], duplicate, *lessons[2:]))}
    assert {"H01", "H02"} <= codes


def test_T17_student_overlap(legal_week):
    snapshot, lessons = legal_week
    duplicate = replace(
        lessons[1], students=lessons[0].students, slot_code="S1", room_id="room-b", start="08:00", end="08:45"
    )
    assert "H03" in {v.code for v in validate_schedule(snapshot, (lessons[0], duplicate, *lessons[2:]))}


def test_T18_room_capacity(legal_week):
    snapshot, lessons = legal_week
    snapshot = replace(snapshot, rooms=(replace(snapshot.rooms[0], capacity=1), snapshot.rooms[1]))
    assert "H04" in {v.code for v in validate_schedule(snapshot, lessons)}


def test_T19_teacher_qualifications(legal_week):
    snapshot, lessons = legal_week
    snapshot = replace(
        snapshot, teachers=(replace(snapshot.teachers[0], qualifications=frozenset()), snapshot.teachers[1])
    )
    assert "H05" in {v.code for v in validate_schedule(snapshot, lessons)}


def test_equipment_eligibility_and_closed_room(legal_week):
    snapshot, lessons = legal_week
    snapshot = replace(
        snapshot, courses=(replace(snapshot.courses[0], features=frozenset({"drone"})), snapshot.courses[1])
    )
    assert "H06" in {v.code for v in validate_schedule(snapshot, lessons)}
    snapshot = replace(snapshot, rooms=(replace(snapshot.rooms[0], status="MAINTENANCE"), snapshot.rooms[1]))
    assert "H08" in {v.code for v in validate_schedule(snapshot, lessons)}


def test_age_and_grade_are_hard_rules(legal_week):
    snapshot, lessons = legal_week
    snapshot = replace(snapshot, students=(replace(snapshot.students[0], age=3), *snapshot.students[1:]))
    assert "H22" in {v.code for v in validate_schedule(snapshot, lessons)}


def test_T20_main_courses_every_workday(legal_week):
    snapshot, lessons = legal_week
    assert "H18" in {v.code for v in validate_schedule(snapshot, lessons[1:])}


def test_T11_T13_T14_daily_load_rest_and_consecutive(legal_week):
    snapshot, lessons = legal_week
    changed = []
    for index, slot in enumerate(DEFAULT_SLOTS[:6]):
        changed.append(
            replace(
                lessons[index],
                teacher_id="teacher-a",
                day=snapshot.week,
                slot_code=slot.code,
                start=slot.start,
                end=slot.end,
            )
        )
    changed.append(replace(lessons[-1], teacher_id="teacher-a"))
    codes = {v.code for v in validate_schedule(snapshot, changed)}
    assert {"H14", "H16", "H17"} <= codes


def test_T12_weekly_limit(legal_week):
    snapshot, lessons = legal_week
    changed = tuple(
        replace(
            lessons[0],
            id=f"load-{day}-{slot.code}",
            day=snapshot.week + timedelta(days=day),
            slot_code=slot.code,
            start=slot.start,
            end=slot.end,
        )
        for day in range(5)
        for slot in DEFAULT_SLOTS[:5]
    )
    assert "H15" in {v.code for v in validate_schedule(snapshot, changed)}


def test_H19_lock_unrelated_lessons(legal_week):
    snapshot, lessons = legal_week
    snapshot = replace(snapshot, locked=(lessons[0],))
    changed = (replace(lessons[0], room_id="room-b"), *lessons[1:])
    assert "H19" in {v.code for v in validate_schedule(snapshot, changed)}


def test_T23_T24_T25_permission_policy():
    student = Principal("alice", "org-a", "STUDENT")
    with pytest.raises(Forbidden):
        require_owner(student, "bob", "org-a")
    with pytest.raises(Forbidden):
        require_owner(student, "alice", "org-b")
    with pytest.raises(Forbidden):
        require_role(student, "ADMIN")
    require_owner(Principal("admin", "org-a", "ADMIN"), "bob", "org-a")


def test_H20_no_unconfirmed_activation():
    with pytest.raises(InvalidTransition):
        transition("VALIDATED", "ACTIVE")
    assert transition("PUBLISHED_PENDING_CONFIRMATION", "ACTIVE") == "ACTIVE"


@pytest.mark.parametrize(
    "values", [{"max_daily": 6}, {"max_weekly": 25}, {"max_consecutive": 4}, {"min_main_daily": 1}]
)
def test_configuration_cannot_relax_safety(values):
    with pytest.raises(BusinessError):
        Rules(**values)


def test_templates_reject_old_rules():
    with pytest.raises(BusinessError):
        validate_templates((replace(DEFAULT_SLOTS[0], start="07:00"), *DEFAULT_SLOTS[1:]))


def test_snapshot_round_trip(legal_week):
    from eduflow.domain.entities import Snapshot

    snapshot, _ = legal_week
    assert Snapshot.from_dict(snapshot.to_dict()) == snapshot
