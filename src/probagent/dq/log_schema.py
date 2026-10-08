"""What the data quality feature ("dq") writes to the run log.

Each action has an Inputs model and an Outputs model. They use
extra="forbid", so a field that isn't listed here can't be logged by
accident. There is deliberately no field for a token or for data values.

    Action    What it records
    upload    a CSV was uploaded on the web page
    fetch     data was fetched from a REST API
    infer     rules were generated from sample data
    check     data was checked against rules (web page or `lint-data`)
"""

import hashlib
import json
from typing import Optional

from pydantic import BaseModel, ConfigDict

from probagent.dq.models import Dataset, Ruleset
from probagent.runlog import register_action

FEATURE = "dq"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetRef(_Strict):
    """Describes data without storing any of its values."""

    name: str          # file name, or URL without its query string
    sha256: str        # fingerprint of the exact bytes loaded
    rows: int
    columns: int


# ---------- upload / fetch ----------

class UploadInputs(_Strict):
    file_name: str
    size_bytes: int
    sha256: str


class FetchInputs(_Strict):
    url: str           # without query string or password
    data_path: str
    token_sent: bool   # only whether a token was used, never the token


class LoadOutputs(_Strict):
    dataset_id: str    # the id the web page uses for this data
    dataset: DatasetRef
    warnings: int


# ---------- infer ----------

class InferInputs(_Strict):
    sample: DatasetRef
    rows_used: int
    columns: Optional[list[str]] = None   # ticked columns; None = all columns


class InferOutputs(_Strict):
    ruleset_sha256: str
    fields: int
    notes: int


# ---------- check ----------

class CheckInputs(_Strict):
    dataset: DatasetRef
    ruleset_name: str
    ruleset_sha256: str
    ruleset: dict      # the full rules, so the check can be repeated exactly
    columns: Optional[list[str]] = None   # ticked columns; None = all columns


class CheckOutputs(_Strict):
    issue_count: int
    rows_with_issues: int
    clean_rows_pct: float
    by_code: dict[str, int]
    truncated: bool


register_action(FEATURE, "upload", UploadInputs, LoadOutputs)
register_action(FEATURE, "fetch", FetchInputs, LoadOutputs)
register_action(FEATURE, "infer", InferInputs, InferOutputs)
register_action(FEATURE, "check", CheckInputs, CheckOutputs)


# ---------- Helpers ----------

def describe_dataset(dataset: Dataset) -> DatasetRef:
    return DatasetRef(name=dataset.source, sha256=dataset.sha256,
                      rows=len(dataset.rows), columns=len(dataset.columns))


def ruleset_as_dict(ruleset: Ruleset) -> dict:
    return ruleset.model_dump(exclude_none=True)


def ruleset_sha256(ruleset: Ruleset) -> str:
    """Same rules = same fingerprint, however the JSON was formatted."""
    canonical = json.dumps(ruleset_as_dict(ruleset), sort_keys=True, separators=(",", ":"))
    return sha256_of(canonical.encode("utf-8"))


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def check_inputs(dataset: Dataset, ruleset: Ruleset,
                 columns: Optional[list[str]] = None) -> dict:
    """The inputs of a check, shared by the web page and `lint-data`."""
    return dict(dataset=describe_dataset(dataset), ruleset_name=ruleset.name,
                ruleset_sha256=ruleset_sha256(ruleset), ruleset=ruleset_as_dict(ruleset),
                columns=columns)


def check_outputs(report) -> dict:
    s = report.summary
    return dict(issue_count=s.issue_count, rows_with_issues=s.rows_with_issues,
                clean_rows_pct=s.clean_rows_pct, by_code=report.by_code,
                truncated=report.truncated)


def load_outputs(dataset_id: str, dataset: Dataset) -> dict:
    return dict(dataset_id=dataset_id, dataset=describe_dataset(dataset),
                warnings=len(dataset.warnings))


def rules_error_message(errors: list[str]) -> str:
    """Summarise ruleset problems for the log, e.g. '2 problem(s): Column "a": ...'."""
    return f"{len(errors)} problem(s): " + "; ".join(errors)
