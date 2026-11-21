from eduflow.domain.errors import InvalidTransition

VERSION_TRANSITIONS = {
    "DRAFT": {"VALIDATED", "CANCELLED"},
    "VALIDATED": {"DRAFT", "PUBLISHED_PENDING_CONFIRMATION", "CANCELLED"},
    "PUBLISHED_PENDING_CONFIRMATION": {"ACTIVE", "CANCELLED"},
    "ACTIVE": {"SUPERSEDED"},
    "SUPERSEDED": set(),
    "CANCELLED": set(),
}
CHANGE_TRANSITIONS = {
    "SUBMITTED": {"VALIDATING", "CANCELLED"},
    "VALIDATING": {"PENDING_ADMIN", "FAILED", "CANCELLED"},
    "PENDING_ADMIN": {"APPROVED", "REJECTED", "CANCELLED"},
    "APPROVED": {"WAITING_CONFIRMATION", "APPLIED", "FAILED", "CANCELLED"},
    "WAITING_CONFIRMATION": {"APPLIED", "FAILED", "CANCELLED"},
    "APPLIED": set(),
    "FAILED": set(),
    "REJECTED": set(),
    "CANCELLED": set(),
}


def transition(current: str, target: str, *, change: bool = False) -> str:
    transitions = CHANGE_TRANSITIONS if change else VERSION_TRANSITIONS
    if target not in transitions.get(current, set()):
        raise InvalidTransition(f"不允许从 {current} 变更到 {target}")
    return target
