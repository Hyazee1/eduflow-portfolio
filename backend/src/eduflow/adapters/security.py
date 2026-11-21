"""HTTP-only safeguards. No machine identifiers or credential values enter diagnostics."""

import hashlib
import re
import threading
import time
from collections import OrderedDict, deque
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from eduflow.domain.errors import BusinessError


class BodyLimitMiddleware:
    """Bound chunked bodies before calling any parser; never trust Content-Length alone."""

    def __init__(self, app: ASGIApp, maximum: int = 1_048_576) -> None:
        self.app = app
        self.maximum = maximum

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.maximum:
                await JSONResponse(
                    {
                        "code": "BODY_TOO_LARGE",
                        "title": "请求内容过大",
                        "status": 413,
                        "request_id": scope.get("state", {}).get("request_id", ""),
                    },
                    status_code=413,
                )(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay() -> Any:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class RateLimited(BusinessError):
    code = "RATE_LIMITED"


class LoginLimiter:
    def __init__(self, limit: int, seconds: int) -> None:
        self.limit = limit
        self.seconds = seconds
        self.entries: OrderedDict[str, deque[float]] = OrderedDict()
        self.lock = threading.Lock()

    def check(self, username: str, address: str) -> None:
        now = time.monotonic()
        keys = [("account:" + username.casefold(), self.limit), ("peer:" + address, self.limit * 50)]
        with self.lock:
            for value, limit in keys:
                key = hashlib.sha256(value.encode()).hexdigest()
                times = self.entries.setdefault(key, deque())
                self.entries.move_to_end(key)
                while times and times[0] < now - self.seconds:
                    times.popleft()
                if len(times) >= limit:
                    raise RateLimited("尝试过于频繁，请稍后再试")
                times.append(now)
            while len(self.entries) > 10000:
                self.entries.popitem(last=False)


def public_details(value: Any) -> Any:
    """Validation diagnostics may contain attacker-supplied paths; redact them recursively."""
    if isinstance(value, dict):
        return {
            key: public_details(item)
            for key, item in value.items()
            if key.lower() not in {"password", "token", "secret", "input", "ctx"}
        }
    if isinstance(value, list):
        return [public_details(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"[A-Za-z]:[\\/][^\s\"<>]+", "[private-path]", value)
        return re.sub(r"(?i)(?:postgresql[^\s]*://|redis://)[^\s]+", "[private-connection]", value)
    return value
