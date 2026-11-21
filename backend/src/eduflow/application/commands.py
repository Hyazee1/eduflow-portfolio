from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, ClassVar

from eduflow.application.ports import Clock, Record, RecordStore, UnitOfWorkFactory
from eduflow.domain.entities import Principal
from eduflow.domain.errors import BusinessError, Conflict


def json_record(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def audit(
    store: RecordStore,
    actor: Principal,
    action: str,
    entity: str,
    entity_id: str,
    detail: Record | None = None,
) -> None:
    store.add(
        "audit_logs",
        {
            "actor_id": actor.id,
            "action": action,
            "entity_type": entity,
            "entity_id": entity_id,
            "detail": json_record(detail or {}),
        },
    )


def notify(store: RecordStore, user_ids: list[str], title: str, content: str, link: str = "") -> None:
    if user_ids:
        store.add(
            "outbox_events",
            {
                "event_type": "NOTIFY",
                "payload": {
                    "user_ids": sorted(set(user_ids)),
                    "title": title,
                    "content": content,
                    "link": link,
                },
            },
        )


@dataclass(frozen=True)
class CommandHandler:
    locks_resources: ClassVar[bool] = True
    uow: UnitOfWorkFactory
    clock: Clock

    def execute(self, actor: Principal, data: Record, key: str) -> Record:
        if not 8 <= len(key) <= 128:
            raise BusinessError("关键操作需要 8—128 字符的 Idempotency-Key")
        operation = type(self).__name__
        fingerprint = hashlib.sha256(json.dumps(data, default=str, sort_keys=True).encode()).hexdigest()
        with self.uow(actor.organization_id) as transaction:
            store = transaction.records
            store.lock(f"idempotency:{actor.id}:{operation}:{key}")
            # Serialize institution business writes with final resource validation.
            # This also prevents a concurrent resource edit from invalidating a just-activated timetable.
            if self.locks_resources:
                store.lock("resources")
            previous = store.list(
                "idempotency_records",
                {
                    "actor_id": actor.id,
                    "operation": operation,
                    "key": key,
                },
            )
            if previous:
                if previous[0]["fingerprint"] != fingerprint:
                    raise Conflict("相同幂等键不能用于不同的请求内容")
                return previous[0]["response"]
            result = json_record(self.apply(store, actor, data))
            store.add(
                "idempotency_records",
                {
                    "actor_id": actor.id,
                    "operation": operation,
                    "key": key,
                    "fingerprint": fingerprint,
                    "response": result,
                },
            )
            transaction.commit()
            return result

    def apply(self, store: RecordStore, actor: Principal, data: Record) -> Record:
        raise NotImplementedError
