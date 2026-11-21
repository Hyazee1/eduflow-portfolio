from typing import Any


class BusinessError(Exception):
    code = "BUSINESS_RULE"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.details = details


class NotFound(BusinessError):
    code = "NOT_FOUND"


class Conflict(BusinessError):
    code = "CONFLICT"


class Forbidden(BusinessError):
    code = "FORBIDDEN"


class Unauthenticated(BusinessError):
    code = "UNAUTHENTICATED"


class InvalidTransition(Conflict):
    code = "INVALID_TRANSITION"


class StaleVersion(Conflict):
    code = "STALE_VERSION"
