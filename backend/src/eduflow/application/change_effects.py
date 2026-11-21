from datetime import datetime

from eduflow.application.ports import Record, RecordStore


def apply_change_effects(store: RecordStore, change: Record) -> None:
    """Executed only in the final activation transaction; any failure rolls back all effects."""
    payload = change["payload"]
    if change["kind"] in {"TEACHER_LEAVE", "STUDENT_LEAVE", "ROOM_FAULT", "EQUIPMENT_FAULT"}:
        owner_id = payload.get("owner_id", change["requester_id"])
        owner_kind = (
            "ROOM"
            if change["kind"] in {"ROOM_FAULT", "EQUIPMENT_FAULT"}
            else ("TEACHER" if change["kind"] == "TEACHER_LEAVE" else "STUDENT")
        )
        store.add(
            "availability_exceptions",
            {
                "owner_id": owner_id,
                "owner_kind": owner_kind,
                "starts_at": datetime.fromisoformat(payload["starts_at"]),
                "ends_at": datetime.fromisoformat(payload["ends_at"]),
                "reason": change["reason"],
                "change_request_id": change["id"],
            },
        )
    if change["kind"] in {"STUDENT_LEAVE", "CANCEL_SESSION", "ENROLLMENT_CHANGE"}:
        for request_id in payload.get("removed_request_ids", []):
            request = store.get("enrollment_requests", request_id)
            store.update(
                "enrollment_requests",
                request_id,
                {
                    "status": "EXCUSED" if change["kind"] == "STUDENT_LEAVE" else "CANCELLED",
                },
                request["version"],
            )
