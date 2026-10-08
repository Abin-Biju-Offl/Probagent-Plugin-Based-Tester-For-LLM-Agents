"""Helpers for the data guard tests (fixtures are in conftest.py)."""

import csv
import json
from pathlib import Path

from probagent.runlog import RunLog

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "dq"
RULES_FILE = FIXTURES / "orders_rules.json"


def load_records(name: str) -> list[dict]:
    """A fixture CSV as a list of records, the way an API would return it."""
    with (FIXTURES / name).open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_config(folder: Path, **policy) -> Path:
    """Write a guard.json with one "orders" source. `policy` overrides the defaults."""
    config = {"version": 1, "sources": {"orders": {"rules": str(RULES_FILE), **policy}}}
    path = folder / "guard.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def log_entries() -> list:
    return RunLog().read()
