"""Loading data into a Dataset (spec Section 5).

Two ways in, one shape out:

    load_csv_bytes(file_bytes, "orders.csv")   # from an uploaded CSV
    records_to_dataset(json_list, "https://…")  # from a REST API response

Every value is kept as text. Types are decided later by the validator.
"""

import csv
import hashlib
import io
import json
from typing import Any

from probagent.dq import settings
from probagent.dq.models import Dataset

MB = 1024 * 1024


class LoadError(Exception):
    """The data could not be loaded. The message is shown to the user."""


# ---------- CSV ----------

def load_csv_bytes(content: bytes, source: str) -> Dataset:
    """Read CSV file bytes into a Dataset, applying the rules in spec 5.1."""
    if len(content) > settings.MAX_FILE_BYTES:
        raise LoadError(f"This file is larger than the {settings.MAX_FILE_BYTES // MB} MB "
                        "demo limit.")

    warnings: list[str] = []
    text = _decode(content, warnings)
    if text.strip() == "":
        raise LoadError("The file is empty.")

    delimiter = _detect_delimiter(text)
    # newline="" lets the csv module handle quoted cells that contain line breaks.
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    all_rows = [row for row in reader if row != []]   # skip blank lines

    header = [name.strip() for name in all_rows[0]]
    data_rows = all_rows[1:]
    _check_header(header)
    if not data_rows:
        raise LoadError("The file has a header row but no data rows.")
    _check_row_count(len(data_rows))

    rows = []
    for row_number, row in enumerate(data_rows, start=1):
        if len(row) != len(header):
            warnings.append(f"Row {row_number} has {len(row)} values but the header has "
                            f"{len(header)} columns.")
            row = (row + [""] * len(header))[: len(header)]   # pad or cut to fit
        rows.append(row)

    return Dataset(columns=header, rows=rows, source=source, warnings=warnings,
                   sha256=hashlib.sha256(content).hexdigest())


def _decode(content: bytes, warnings: list[str]) -> str:
    """Try UTF-8 (with or without BOM) first, then the common Windows encoding."""
    try:
        return content.decode("utf-8-sig")   # "-sig" removes a BOM if there is one
    except UnicodeDecodeError:
        warnings.append("The file is not UTF-8. It was read as Windows-1252, so some "
                        "special characters may look wrong.")
        return content.decode("cp1252", errors="replace")


def _detect_delimiter(text: str) -> str:
    """Guess comma, semicolon or tab from the start of the file. Default: comma."""
    try:
        return csv.Sniffer().sniff(text[:5000], delimiters=",;\t").delimiter
    except csv.Error:
        return ","


def _check_header(header: list[str]) -> None:
    if len(header) > settings.MAX_COLUMNS:
        raise LoadError(f"The data has {len(header)} columns. The demo limit is "
                        f"{settings.MAX_COLUMNS}.")
    if any(name == "" for name in header):
        raise LoadError("A column in the header row has no name.")
    duplicates = sorted({name for name in header if header.count(name) > 1})
    if duplicates:
        raise LoadError("Column names must be unique. Repeated: " + ", ".join(duplicates))


def _check_row_count(count: int) -> None:
    if count > settings.MAX_ROWS:
        raise LoadError(f"The data has {count:,} rows. The demo limit is "
                        f"{settings.MAX_ROWS:,} rows.")


# ---------- JSON records (from a REST API) ----------

def records_to_dataset(records: Any, source: str) -> Dataset:
    """Turn a JSON list of objects into a Dataset (spec 5.2).

    Nested objects become dot names: {"customer": {"email": ...}} -> "customer.email".
    Lists stay as JSON text, and null becomes a missing value.
    """
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise LoadError("The data is not a list of records. It must look like "
                        "[{...}, {...}]. Use the data path to point at that list.")
    if not records:
        raise LoadError("The API returned an empty list, so there is nothing to check.")
    _check_row_count(len(records))

    flat_records = [_flatten(record) for record in records]

    # Columns in the order they are first seen.
    columns: list[str] = []
    for record in flat_records:
        for name in record:
            if name not in columns:
                columns.append(name)
    _check_header(columns)

    rows = [[record.get(name, "") for name in columns] for record in flat_records]
    return Dataset(columns=columns, rows=rows, source=source)


def _flatten(obj: dict, prefix: str = "") -> dict[str, str]:
    """{"a": {"b": 1}} -> {"a.b": "1"}. All values become text."""
    flat = {}
    for key, value in obj.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, prefix=name + "."))
        else:
            flat[name] = _to_text(value)
    return flat


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):          # check bool before numbers: True is also 1
        return "true" if value else "false"
    if isinstance(value, list):
        return json.dumps(value)
    return str(value)
