from conftest import seeded_issues

from probagent.dq import settings
from probagent.dq.infer import SAMPLE_KEY, infer_field, infer_ruleset
from probagent.dq.loader import load_csv_bytes
from probagent.dq.validator import check_dataset


# ---------- Phase 2 acceptance ----------

def test_clean_sample_passes_its_own_inferred_rules(clean_orders):
    ruleset = infer_ruleset(clean_orders).ruleset
    assert check_dataset(clean_orders, ruleset).issues == []


def test_inferred_rules_catch_most_seeded_issues(clean_orders, dirty_orders):
    ruleset = infer_ruleset(clean_orders).ruleset
    report = check_dataset(dirty_orders, ruleset)
    found_cells = {(issue.row, issue.column or "-") for issue in report.issues}
    expected_cells = {(row, column) for row, column, _ in seeded_issues()}
    caught = len(expected_cells & found_cells) / len(expected_cells)
    assert caught >= 0.8


def test_inferred_rules_for_the_orders_columns(clean_orders):
    fields = infer_ruleset(clean_orders).ruleset.fields
    assert fields["order_id"].type == "integer" and fields["order_id"].unique
    assert fields["email"].pattern == "email" and fields["email"].required
    assert fields["price"].type == "number" and not fields["price"].unique
    assert fields["status"].allowed == ["cancelled", "pending", "shipped"]
    assert fields["country"].min_length == fields["country"].max_length == 2
    assert fields["order_date"].type == "date" and fields["order_date"].format == "YYYY-MM-DD"
    assert fields["order_date"].min is None and fields["order_date"].max is None
    assert not fields["notes"].required


# ---------- Single columns ----------

def test_ambiguous_day_month_gets_a_note():
    rule, notes = infer_field(["03/04/2026", "05/06/2026", "01/02/2026"])
    assert rule.type == "date" and rule.format == "DD/MM/YYYY"
    assert any("day/month or month/day" in note for note in notes)


def test_clear_month_day_format_is_detected():
    rule, notes = infer_field(["12/31/2026", "01/15/2026"])
    assert rule.format == "MM/DD/YYYY"
    assert not any("day/month" in note for note in notes)


def test_type_order_and_threshold():
    assert infer_field(["true", "false", "yes"])[0].type == "boolean"
    assert infer_field(["1", "22", "333"])[0].type == "integer"
    assert infer_field(["1.5", "2", "3"])[0].type == "number"
    assert infer_field(["2026-10-07T09:30:00", "2026-10-08T10:00:00"])[0].type == "datetime"
    # 19 numbers and 1 word: 95% fit, so still "integer", with a note.
    rule, notes = infer_field([str(n) for n in range(19)] + ["oops"])
    assert rule.type == "integer"
    assert any("didn't look like" in note for note in notes)


def test_required_and_mostly_empty():
    assert infer_field(["a", "b"])[0].required
    rule, notes = infer_field(["x", "", "", ""])
    assert not rule.required
    assert any("Mostly empty" in note for note in notes)


def test_empty_column():
    rule, notes = infer_field(["", " "])
    assert rule.type == "string" and not rule.required


def test_unique_needs_at_least_10_rows():
    assert not infer_field(["a1", "a2", "a3"])[0].unique
    assert infer_field([f"id{n}" for n in range(10)])[0].unique


def test_only_first_rows_are_used(monkeypatch):
    monkeypatch.setattr(settings, "INFER_SAMPLE_ROWS", 3)
    dataset = load_csv_bytes(b"n\n1\n2\n3\nnot a number\n", "x.csv")
    result = infer_ruleset(dataset)
    assert result.ruleset.fields["n"].type == "integer"
    assert SAMPLE_KEY in result.notes


def test_dates_get_type_and_format_only():
    """No min/max for dates: newer data must not fail just for being newer."""
    rule, notes = infer_field(["18/06/2026", "01/06/2026", "25/06/2026"])
    assert (rule.type, rule.format) == ("date", "DD/MM/YYYY")
    assert rule.min is None and rule.max is None
    assert not any("min/max" in note for note in notes)
