"""Shared test helpers."""

import json
from pathlib import Path

import pytest

from probagent.dq.loader import load_csv_bytes
from probagent.dq.rules import parse_ruleset
from probagent.runlog import LOG_DIR_ENV

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "dq"


@pytest.fixture(autouse=True)
def log_dir(tmp_path, monkeypatch):
    """Every test writes its run log to a temporary folder, not the project."""
    folder = tmp_path / "logs"
    monkeypatch.setenv(LOG_DIR_ENV, str(folder))
    return folder


def load_fixture(name: str):
    return load_csv_bytes((FIXTURES / name).read_bytes(), source=name)


@pytest.fixture
def orders_rules():
    return parse_ruleset(json.loads((FIXTURES / "orders_rules.json").read_text()))


@pytest.fixture
def clean_orders():
    return load_fixture("orders_clean.csv")


@pytest.fixture
def dirty_orders():
    return load_fixture("orders_dirty.csv")


def seeded_issues() -> set[tuple[int, str, str]]:
    """Read the expected issues from the table in DQ_ISSUES.md: {(row, column, code)}."""
    expected = set()
    for line in (FIXTURES / "DQ_ISSUES.md").read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if line.startswith("|") and cells[0].isdigit():
            expected.add((int(cells[0]), cells[1], cells[2]))
    return expected
