from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from eduflow.domain.errors import BusinessError

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class Slot:
    code: str
    start: str
    end: str
    kind: str
    period: str
    ordinal: int

    @property
    def minutes(self) -> int:
        return to_minutes(self.end) - to_minutes(self.start)

    def at(self, day: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(day, time.fromisoformat(self.start), SHANGHAI),
            datetime.combine(day, time.fromisoformat(self.end), SHANGHAI),
        )


# Only source for initial configuration; runtime uses persisted versioned templates.
DEFAULT_SLOTS = (
    Slot("S1", "08:00", "08:45", "REGULAR", "AM", 1),
    Slot("S2", "08:55", "09:40", "REGULAR", "AM", 2),
    Slot("S3", "10:00", "10:45", "REGULAR", "AM", 3),
    Slot("S4", "10:55", "11:40", "REGULAR", "AM", 4),
    Slot("S5", "14:00", "14:45", "REGULAR", "PM", 5),
    Slot("S6", "14:55", "15:40", "REGULAR", "PM", 6),
    Slot("S7", "16:00", "16:45", "REGULAR", "PM", 7),
    Slot("S8", "16:55", "17:30", "QA", "PM", 8),
)

BREAKS = (
    {"start": "08:45", "end": "08:55", "kind": "BREAK"},
    {"start": "09:40", "end": "10:00", "kind": "BREAK"},
    {"start": "10:45", "end": "10:55", "kind": "BREAK"},
    {"start": "11:40", "end": "12:00", "kind": "BUFFER"},
    {"start": "12:00", "end": "14:00", "kind": "LUNCH"},
    {"start": "14:45", "end": "14:55", "kind": "BREAK"},
    {"start": "15:40", "end": "16:00", "kind": "BREAK"},
    {"start": "16:45", "end": "16:55", "kind": "BREAK"},
    {"start": "17:30", "end": "17:40", "kind": "BUFFER"},
)


def to_minutes(value: str) -> int:
    parsed = time.fromisoformat(value)
    return parsed.hour * 60 + parsed.minute


def slot_key(day: date, code: str) -> str:
    return f"{day.weekday()}:{code}"


def week_days(week: date) -> tuple[date, ...]:
    if week.weekday() != 0:
        raise BusinessError("排课周必须从周一开始")
    return tuple(week + timedelta(days=i) for i in range(5))


def all_keys() -> frozenset[str]:
    return frozenset(f"{d}:{s.code}" for d in range(5) for s in DEFAULT_SLOTS)


def validate_templates(slots: tuple[Slot, ...]) -> None:
    if slots != DEFAULT_SLOTS:
        raise BusinessError("教学时段违反 V2.0 安全基线；请通过受控配置迁移修改")


@dataclass(frozen=True, slots=True)
class Rules:
    max_daily: int = 5
    max_weekly: int = 24
    max_consecutive: int = 3
    min_main_daily: int = 2
    solve_seconds: float = 60
    require_full_day_coverage: bool = True

    def __post_init__(self) -> None:
        if not (1 <= self.max_daily <= 5 and 1 <= self.max_weekly <= 24):
            raise BusinessError("教师负载不得超过每日五节、每周二十四节")
        if not 1 <= self.max_consecutive <= 3 or self.min_main_daily < 2:
            raise BusinessError("连续授课或机构主课规则低于安全基线")
        if not 0 < self.solve_seconds <= 300:
            raise BusinessError("求解预算必须在 0 到 300 秒之间")
