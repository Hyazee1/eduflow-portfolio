"""Export a single ACTIVE version with grain checks; excludes names, contacts and credentials."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from sqlalchemy import create_engine, text  # noqa: E402

from eduflow.bootstrap.settings import Settings  # noqa: E402


def data_type(values: list[Any]) -> str:
    sample = next((v for v in values if v is not None), None)
    if isinstance(sample, bool):
        return "boolean"
    if isinstance(sample, datetime):
        return "dateTime"
    if isinstance(sample, date):
        return "date"
    if isinstance(sample, int):
        return "int64"
    if isinstance(sample, float):
        return "double"
    return "string"


def export(version_id: str) -> dict[str, Any]:
    version_id = str(UUID(version_id))
    folder = ROOT / "analytics"
    output = folder / "data"
    output.mkdir(exist_ok=True)
    engine = create_engine(
        Settings().database_url, connect_args={"options": "-c default_transaction_read_only=on"}
    )
    tables: dict[str, Any] = {}
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        for file in sorted((folder / "sql").glob("*.sql")):
            query = file.read_text(encoding="utf-8").replace("__VERSION_ID__", version_id)
            cursor = connection.execute(text(query))
            columns = list(cursor.keys())
            rows = [dict(row) for row in cursor.mappings()]
            if not rows:
                raise RuntimeError("Expected a nonempty analytics table: " + file.stem)
            tables[file.stem] = {
                "columns": {c: data_type([r[c] for r in rows]) for c in columns},
                "rows": rows,
            }
        connection.rollback()
    engine.dispose()
    version = tables["DimVersion"]["rows"][0]
    if version["version_status"] != "ACTIVE":
        raise RuntimeError("Final analytics export requires an ACTIVE version")
    demands, sessions, tasks, room_slots = (
        tables[n]["rows"] for n in ("FactDemand", "FactSession", "FactConfirmation", "FactRoomSlot")
    )
    assert len({r["request_id"] for r in demands}) == len(demands) == 668
    assert len({r["session_id"] for r in sessions}) == len(sessions)
    assert sum(r["student_count"] for r in sessions) == 668
    assert len({r["confirmation_task_id"] for r in tasks}) == len(tasks) == 668 + len(sessions)
    assert all(r["decision"] == "ACCEPTED" for r in tasks)
    assert len({(r["day"], r["slot_code"]) for r in sessions}) == 40
    assert len({(r["room_id"], r["day"], r["slot_code"]) for r in room_slots}) == len(room_slots) == 400
    key_columns = {
        "DimStudent": "student_id",
        "DimTeacher": "teacher_id",
        "DimRoom": "room_id",
        "DimCourse": "course_id",
        "DimDay": "day",
        "DimSlot": "slot_code",
        "DimVersion": "version_id",
    }
    for name, key in key_columns.items():
        assert len({r[key] for r in tables[name]["rows"]}) == len(tables[name]["rows"]), name
    for fact in ("FactDemand", "FactSession", "FactConfirmation"):
        for dimension, key in [
            ("DimCourse", "course_id"),
            ("DimTeacher", "teacher_id"),
            ("DimRoom", "room_id"),
        ]:
            ids = {r[key] for r in tables[dimension]["rows"]}
            assert all(r[key] in ids for r in tables[fact]["rows"]), (fact, key)
    metadata: dict[str, Any] = {
        "version_id": version_id,
        "week": str(version["week"]),
        "classification": "synthetic demonstration data",
        "extracted_at": str(version["extracted_at"]),
        "tables": {},
    }
    for name, table in tables.items():
        file = output / (name + ".csv")
        with file.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(table["columns"]))
            writer.writeheader()
            writer.writerows(table["rows"])
        metadata["tables"][name] = {
            "rows": len(table["rows"]),
            "columns": table["columns"],
            "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
        }
    metrics = {
        "approved_demands": len(demands),
        "fulfilled_demands": sum(r["is_scheduled"] for r in demands),
        "sessions": len(sessions),
        "first_preferred": sum(r["in_first_preferred_slot"] for r in demands),
        "any_preferred": sum(r["in_any_preferred_slot"] for r in demands),
        "confirmation_tasks": len(tasks),
        "accepted_tasks": sum(r["decision"] == "ACCEPTED" for r in tasks),
        "planned_minutes": sum(r["duration_minutes"] for r in sessions),
        "available_room_minutes": sum(r["available_minutes"] for r in room_slots),
        "allocated_seats": sum(r["room_capacity"] for r in sessions),
        "effective_seats": sum(r["effective_class_capacity"] for r in sessions),
        "hard_conflicts": version["hard_conflicts"],
        "covered_slots": 40,
    }
    metadata["metrics"] = metrics
    (folder / "dataset-manifest.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps({"version_id": version_id, "tables": len(tables), "metrics": metrics}, ensure_ascii=False)
    )
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--version-id", required=True)
    export(parser.parse_args().version_id)
