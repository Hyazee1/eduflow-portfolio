"""Export deterministic non-personal domain fixtures for feasibility and failure diagnostics."""
# ruff: noqa: E402 -- project imports intentionally follow the standalone path bootstrap.

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
from eduflow.adapters.demo import scenario_snapshot

destination = ROOT / "data"
destination.mkdir(exist_ok=True)
for kind in ("feasible", "conflict", "resource_shortage"):
    (destination / (kind + ".json")).write_text(
        json.dumps(scenario_snapshot(kind).to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
print("Exported 3 fixed-seed scenarios to data/; no database was modified.")
