import pytest

from probagent.dq import settings
from probagent.dq.loader import LoadError, load_csv_bytes, records_to_dataset


def test_bom_semicolons_and_quoted_newlines():
    content = '﻿id;note\n1;"line one\nline two"\n2;plain\n'.encode("utf-8")
    dataset = load_csv_bytes(content, "x.csv")
    assert dataset.columns == ["id", "note"]          # BOM removed from "id"
    assert dataset.rows == [["1", "line one\nline two"], ["2", "plain"]]
    assert dataset.warnings == []


def test_tab_delimiter():
    dataset = load_csv_bytes(b"a\tb\n1\t2\n", "x.tsv")
    assert dataset.columns == ["a", "b"]


def test_values_stay_as_text():
    dataset = load_csv_bytes(b"code,amount\n007,1.50\n", "x.csv")
    assert dataset.rows == [["007", "1.50"]]


def test_windows_encoding_falls_back_with_warning():
    dataset = load_csv_bytes("name\nCafé\n".encode("cp1252"), "x.csv")
    assert dataset.rows == [["Café"]]
    assert "Windows-1252" in dataset.warnings[0]


def test_short_and_long_rows_are_fixed_with_warning():
    dataset = load_csv_bytes(b"a,b\n1\n1,2,3\n", "x.csv")
    assert dataset.rows == [["1", ""], ["1", "2"]]
    assert len(dataset.warnings) == 2


@pytest.mark.parametrize("content, message", [
    (b"", "empty"),
    (b"a,b\n", "no data rows"),
    (b"a,a\n1,2\n", "Repeated: a"),
    (b"a,\n1,2\n", "has no name"),
])
def test_bad_files_are_rejected(content, message):
    with pytest.raises(LoadError, match=message):
        load_csv_bytes(content, "x.csv")


def test_size_limit(monkeypatch):
    monkeypatch.setattr(settings, "MAX_FILE_BYTES", 10)
    with pytest.raises(LoadError, match="larger than"):
        load_csv_bytes(b"a\n" + b"1\n" * 10, "x.csv")


def test_row_limit(monkeypatch):
    monkeypatch.setattr(settings, "MAX_ROWS", 2)
    with pytest.raises(LoadError, match="demo limit is 2 rows"):
        load_csv_bytes(b"a\n1\n2\n3\n", "x.csv")


def test_column_limit(monkeypatch):
    monkeypatch.setattr(settings, "MAX_COLUMNS", 2)
    with pytest.raises(LoadError, match="demo limit is 2"):
        load_csv_bytes(b"a,b,c\n1,2,3\n", "x.csv")


def test_json_records_are_flattened():
    records = [
        {"id": 1, "customer": {"email": "a@b.com"}, "tags": ["x", "y"], "paid": True, "note": None},
        {"id": 2, "extra": "only here"},
    ]
    dataset = records_to_dataset(records, "api")
    assert dataset.columns == ["id", "customer.email", "tags", "paid", "note", "extra"]
    assert dataset.rows[0] == ["1", "a@b.com", '["x", "y"]', "true", "", ""]
    assert dataset.rows[1] == ["2", "", "", "", "", "only here"]


@pytest.mark.parametrize("records", [{"a": 1}, [1, 2], "text"])
def test_json_must_be_list_of_objects(records):
    with pytest.raises(LoadError, match="not a list of records"):
        records_to_dataset(records, "api")
