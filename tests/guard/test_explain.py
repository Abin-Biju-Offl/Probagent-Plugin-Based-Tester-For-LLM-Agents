"""`probagent explain`: reading a guard check back from the run log."""

from guard_helpers import log_entries

from probagent.cli import main
from probagent.guard import core


def test_block_message_gives_the_user_a_reference(make_guard, dirty_records):
    result = make_guard().check(dirty_records, "orders")
    view = result.for_agent()
    assert view["run_id"] == result.run_id
    assert "run_id as a reference" in view["message"]


def test_issue_positions_are_logged_without_values(make_guard, dirty_records):
    make_guard().check(dirty_records, "orders")
    entry = log_entries()[-1]
    assert len(entry.outputs["issues"]) == 13
    assert {"row": 12, "column": "email", "code": "pattern_mismatch"} in entry.outputs["issues"]
    assert entry.outputs["issues_truncated"] is False
    assert entry.inputs["ruleset"]["fields"]["email"]["pattern"] == "email"


def test_explain_a_blocked_check_by_run_id(make_guard, dirty_records, capsys):
    run_id = make_guard().check(dirty_records, "orders").run_id
    assert main(["explain", run_id]) == 0
    out = capsys.readouterr().out
    assert f"Guard check {run_id}" in out
    assert "FAIL, blocked. The agent did not get this data." in out
    assert "13 issues in 13 of 201 rows." in out
    assert 'email           pattern_mismatch: does not match "pattern": email.' in out
    assert 'not one of "allowed": pending, shipped, cancelled.' in out
    assert "@example.com" not in out                     # no values: they aren't logged


def test_explain_without_run_id_skips_clean_passes(make_guard, clean_records, dirty_records,
                                                   capsys):
    guard = make_guard(on_fail="filter")
    flagged = guard.check(dirty_records, "orders").run_id
    guard.check(clean_records, "orders")                  # newer, but a clean pass
    assert main(["explain"]) == 0
    out = capsys.readouterr().out
    assert flagged in out
    assert "FAIL, filtered" in out and "13 rows removed." in out


def test_explain_a_clean_pass(make_guard, clean_records, capsys):
    run_id = make_guard().check(clean_records, "orders").run_id
    assert main(["explain", run_id]) == 0
    assert "PASS. The agent got the data unchanged." in capsys.readouterr().out


def test_explain_an_error(make_guard, clean_records, capsys):
    run_id = make_guard().check("{not json", "orders").run_id
    assert main(["explain", run_id]) == 0
    out = capsys.readouterr().out
    assert "ERROR, blocked" in out
    assert "unreadable_data" in out


def test_explain_only_logs_the_first_issues(make_guard, dirty_records, monkeypatch, capsys):
    monkeypatch.setattr(core, "MAX_ISSUES_LOGGED", 3)
    run_id = make_guard().check(dirty_records, "orders").run_id
    main(["explain", run_id])
    assert "only the first 3 of 13 issues are logged." in capsys.readouterr().out


def test_explain_unknown_run_id(capsys):
    assert main(["explain", "20990101-000000-abcdef"]) == 3
    assert "No guard check with run id" in capsys.readouterr().err


def test_explain_with_nothing_flagged(capsys):
    assert main(["explain"]) == 3
    assert "No blocked or flagged guard checks" in capsys.readouterr().err
