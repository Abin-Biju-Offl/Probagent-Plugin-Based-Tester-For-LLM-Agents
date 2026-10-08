from conftest import seeded_issues

from probagent.dq import settings
from probagent.dq.loader import load_csv_bytes
from probagent.dq.rules import parse_ruleset
from probagent.dq.validator import check_dataset


def check(csv_text: str, rules: dict):
    dataset = load_csv_bytes(csv_text.encode(), "test.csv")
    return check_dataset(dataset, parse_ruleset(rules))


def codes(report) -> list[str]:
    return [issue.code for issue in report.issues]


# ---------- Phase 1 acceptance: the fixtures ----------

def test_dirty_fixture_reports_exactly_the_seeded_issues(dirty_orders, orders_rules):
    report = check_dataset(dirty_orders, orders_rules)
    found = {(issue.row, issue.column or "-", issue.code) for issue in report.issues}
    assert found == seeded_issues()
    assert report.summary.issue_count == len(seeded_issues())


def test_clean_fixture_has_no_issues(clean_orders, orders_rules):
    report = check_dataset(clean_orders, orders_rules)
    assert report.issues == []
    assert report.summary.clean_rows_pct == 100.0


# ---------- Single rules ----------

def test_required_and_optional_missing_values():
    report = check("a,b\n,\n", {"fields": {"a": {"required": True}, "b": {}}})
    assert codes(report) == ["missing_value"]


def test_spaces_only_counts_as_missing():
    report = check('a\n"   "\n', {"fields": {"a": {"required": True}}})
    assert codes(report) == ["missing_value"]


def test_wrong_type_stops_other_checks_for_that_cell():
    report = check("n\nfive\n", {"fields": {"n": {"type": "integer", "min": 1, "allowed": ["1"]}}})
    assert codes(report) == ["wrong_type"]


def test_min_max_for_numbers_and_dates():
    rules = {"fields": {
        "n": {"type": "number", "min": 0, "max": 10},
        "d": {"type": "date", "min": "2026-01-01", "max": "2026-12-31"},
    }}
    report = check("n,d\n-1,2025-12-31\n11,2027-01-01\n5,2026-06-01\n", rules)
    assert codes(report) == ["below_min", "below_min", "above_max", "above_max"]


def test_lengths_allowed_and_pattern():
    rules = {"fields": {
        "c": {"min_length": 2, "max_length": 2},
        "s": {"allowed": ["open", "closed"]},
        "e": {"pattern": "email"},
    }}
    report = check("c,s,e\nI,open,a@b.com\nIND,open,a@b.com\nIN,opn,a@b.com\nIN,open,nope\n", rules)
    assert codes(report) == ["too_short", "too_long", "not_allowed", "pattern_mismatch"]


def test_allowed_ignores_surrounding_spaces():
    report = check('s\n" open "\n', {"fields": {"s": {"allowed": ["open"]}}})
    assert report.issues == []


def test_bad_date_vs_wrong_type():
    report = check("d\n2026-02-30\n30/01/2026\n", {"fields": {"d": {"type": "date"}}})
    assert codes(report) == ["bad_date", "wrong_type"]


def test_unique_names_the_first_row():
    report = check("id,name\n1,a\n2,b\n1,c\n", {"fields": {"id": {"unique": True}}})
    assert codes(report) == ["duplicate_value"]
    assert report.issues[0].row == 3
    assert "row 1" in report.issues[0].message


def test_duplicate_row_is_one_issue():
    rules = {"fields": {"id": {"unique": True}, "n": {"type": "integer"}}}
    report = check("id,n\n1,x\n1,x\n", rules)
    # Row 1: wrong type. Row 2: only "duplicate row", not repeated cell issues.
    assert codes(report) == ["wrong_type", "duplicate_row"]


def test_duplicate_rows_check_can_be_turned_off():
    report = check("a\n1\n1\n", {"check_duplicate_rows": False, "fields": {"a": {}}})
    assert report.issues == []


def test_missing_and_unexpected_columns():
    report = check("a,extra\n1,2\n", {"allow_extra_columns": False,
                                      "fields": {"a": {}, "b": {}}})
    assert sorted(codes(report)) == ["missing_column", "unexpected_column"]
    assert all(issue.row is None for issue in report.issues)


def test_long_values_are_shortened():
    report = check("e\n" + "x" * 300 + "\n", {"fields": {"e": {"pattern": "email"}}})
    assert len(report.issues[0].value) == settings.MAX_VALUE_CHARS_IN_REPORT + 1   # + "…"


def test_issue_list_is_truncated_but_counts_cover_everything(monkeypatch):
    monkeypatch.setattr(settings, "MAX_ISSUES_SHOWN", 3)
    report = check("n\n" + "x\n" * 10, {"check_duplicate_rows": False,
                                        "fields": {"n": {"type": "integer"}}})
    assert len(report.issues) == 3
    assert report.truncated
    assert report.summary.issue_count == 10
    assert report.by_code == {"wrong_type": 10}


def test_summary_numbers():
    report = check("a\n1\nx\n", {"fields": {"a": {"type": "integer"}}})
    assert report.summary.rows_checked == 2
    assert report.summary.rows_with_issues == 1
    assert report.summary.clean_rows_pct == 50.0
    assert report.by_column == {"a": 1}
