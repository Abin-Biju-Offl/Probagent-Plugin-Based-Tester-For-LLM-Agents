"""Tests for the shared run log (probagent/runlog.py)."""

import json
import re
from datetime import timedelta

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from probagent import runlog
from probagent.runlog import LOG_DIR_ENV, RunEntry, RunLog, register_action


class DemoInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class DemoOutputs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int


register_action("test", "demo", DemoInputs, DemoOutputs)


def lines_in(log: RunLog) -> list[dict]:
    return [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]


def test_successful_run_writes_one_complete_line(tmp_path):
    log = RunLog(tmp_path)
    with log.track("test", "demo", origin="cli") as run:
        run.set_inputs(name="x")
        run.set_outputs(count=3)

    [entry] = lines_in(log)
    assert entry["schema_version"] == 1
    assert entry["feature"] == "test" and entry["action"] == "demo"
    assert entry["origin"] == "cli" and entry["status"] == "ok"
    assert entry["inputs"] == {"name": "x"} and entry["outputs"] == {"count": 3}
    assert entry["error"] is None
    assert entry["run_id"] == run.run_id
    assert entry["duration_ms"] >= 0
    RunEntry.model_validate(entry)            # every line matches the schema


def test_failed_run_is_logged_as_error(tmp_path):
    log = RunLog(tmp_path)
    with log.track("test", "demo", origin="web") as run:
        run.fail("bad_input", "Something was wrong.")
    [entry] = lines_in(log)
    assert entry["status"] == "error"
    assert entry["error"] == {"code": "bad_input", "message": "Something was wrong."}


def test_crash_is_logged_and_still_raised(tmp_path):
    log = RunLog(tmp_path)
    with pytest.raises(KeyError):
        with log.track("test", "demo", origin="cli"):
            raise KeyError("secret-value")
    [entry] = lines_in(log)
    assert entry["error"] == {"code": "unexpected_error", "message": "KeyError"}
    assert "secret-value" not in log.path.read_text()   # only the error type is logged


def test_parent_run_id_links_runs(tmp_path):
    log = RunLog(tmp_path)
    with log.track("test", "demo", origin="web") as first:
        pass
    with log.track("test", "demo", origin="web", parent_run_id=first.run_id):
        pass
    assert lines_in(log)[1]["parent_run_id"] == first.run_id


def test_unknown_action_is_refused(tmp_path):
    with pytest.raises(ValueError, match="register_action"):
        with RunLog(tmp_path).track("test", "nope", origin="cli"):
            pass


def test_fields_outside_the_schema_are_refused(tmp_path):
    with pytest.raises(ValidationError):
        with RunLog(tmp_path).track("test", "demo", origin="cli") as run:
            run.set_inputs(name="x", token="should-never-be-logged")


def test_run_ids_are_unique_and_start_with_the_date(tmp_path):
    log = RunLog(tmp_path)
    ids = []
    for _ in range(3):
        with log.track("test", "demo", origin="cli") as run:
            ids.append(run.run_id)
    assert len(set(ids)) == 3
    assert all(re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", run_id) for run_id in ids)
    assert ids[0].startswith(f"{runlog.now():%Y%m%d}")


def test_default_folder_comes_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(LOG_DIR_ENV, str(tmp_path / "custom"))
    assert RunLog().path == tmp_path / "custom" / "runs.jsonl"


def test_default_folder_is_dot_probagent_in_current_folder(tmp_path, monkeypatch):
    monkeypatch.delenv(LOG_DIR_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    assert RunLog().path == tmp_path / ".probagent" / "runs.jsonl"


def test_write_problem_does_not_break_the_action(tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("a file where the log folder should be")
    with RunLog(blocker).track("test", "demo", origin="cli") as run:
        run.set_inputs(name="still works")       # no exception reaches the caller


def test_old_entries_are_pruned(tmp_path):
    log = RunLog(tmp_path)
    old = (runlog.now() - timedelta(days=runlog.MAX_AGE_DAYS + 1)).isoformat()
    recent = runlog.now().isoformat()
    tmp_path.joinpath("runs.jsonl").write_text(
        json.dumps({"started_at": old, "run_id": "old"}) + "\n"
        + json.dumps({"started_at": recent, "run_id": "recent"}) + "\n"
        + "not json, kept as is\n")
    with log.track("test", "demo", origin="cli"):
        pass
    lines = log.path.read_text().splitlines()
    assert '"old"' not in lines[0]
    assert len(lines) == 3          # recent + unreadable line + the new entry


def test_only_newest_entries_are_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(runlog, "MAX_ENTRIES", 3)
    log_file = tmp_path / "runs.jsonl"
    log_file.write_text("".join(
        json.dumps({"started_at": runlog.now().isoformat(), "n": n}) + "\n" for n in range(5)))
    with RunLog(tmp_path).track("test", "demo", origin="cli"):
        pass
    lines = log_file.read_text().splitlines()
    assert len(lines) == 4          # 3 kept + the new entry
    assert json.loads(lines[0])["n"] == 2


def test_read_returns_entries(tmp_path):
    log = RunLog(tmp_path)
    with log.track("test", "demo", origin="cli"):
        pass
    assert [entry.action for entry in log.read()] == ["demo"]
