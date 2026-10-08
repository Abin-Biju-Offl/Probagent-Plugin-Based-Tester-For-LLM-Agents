"""Shared fixtures for the data guard tests."""

import pytest

from guard_helpers import load_records, write_config
from probagent.guard import DataGuard
from probagent.runlog import LOG_DIR_ENV


@pytest.fixture(autouse=True)
def log_dir(tmp_path, monkeypatch):
    """Every test writes its run log to a temporary folder, not the project."""
    folder = tmp_path / "logs"
    monkeypatch.setenv(LOG_DIR_ENV, str(folder))
    return folder


@pytest.fixture
def clean_records():
    return load_records("orders_clean.csv")


@pytest.fixture
def dirty_records():
    return load_records("orders_dirty.csv")


@pytest.fixture
def make_guard(tmp_path):
    """make_guard(on_fail="filter") -> a DataGuard for the orders source."""
    def make(**policy) -> DataGuard:
        return DataGuard.from_config(write_config(tmp_path, **policy))
    return make
