"""The shapes of our data (spec Section 8.3).

Pydantic models check their own fields. For example, `FieldRule` uses
`extra="forbid"`, so a typo like "requird" is rejected instead of ignored.
"""

from dataclasses import dataclass, field
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

FieldType = Literal["string", "integer", "number", "boolean", "date", "datetime"]


# ---------- Data ----------

@dataclass
class Dataset:
    """A table where every value is kept as text.

    The loader never converts types. The validator decides later whether
    "007" is a valid integer, so no value is silently changed.
    An empty string ("") means the cell is missing.
    """

    columns: list[str]
    rows: list[list[str]]
    source: str                                 # file name or URL (never a token)
    warnings: list[str] = field(default_factory=list)
    sha256: str = ""                            # fingerprint of the raw bytes loaded
    run_id: Optional[str] = None                # the run-log entry that loaded it


# ---------- Rules ----------

class FieldRule(BaseModel):
    """The rules for one column. Every rule is optional except `type`."""

    model_config = ConfigDict(extra="forbid")

    type: FieldType = "string"
    required: bool = False
    unique: bool = False
    min: Optional[Union[int, float, str]] = None    # str is used for dates
    max: Optional[Union[int, float, str]] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    allowed: Optional[list[str]] = None
    pattern: Optional[Union[str, dict[str, str]]] = None   # "email" or {"regex": "..."}
    format: Optional[str] = None                    # dates only, e.g. "YYYY-MM-DD"


class Ruleset(BaseModel):
    """A whole ruleset: one FieldRule per column, plus dataset-level options."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    name: str = "ruleset"
    allow_extra_columns: bool = True
    check_duplicate_rows: bool = True
    fields: dict[str, FieldRule]


# ---------- Results ----------

class Issue(BaseModel):
    """One problem found in the data."""

    row: Optional[int] = None          # data row, starting at 1; None = whole dataset
    column: Optional[str] = None
    value: Optional[str] = None        # the bad value, cut to 100 characters
    code: str                          # e.g. "missing_value", "wrong_type"
    message: str                       # plain-English explanation


class ReportSummary(BaseModel):
    rows_checked: int
    columns_checked: int
    issue_count: int
    rows_with_issues: int
    clean_rows_pct: float


class Report(BaseModel):
    source: str
    ruleset_name: str
    checked_at: str
    summary: ReportSummary
    by_column: dict[str, int]          # issue count per column (all issues)
    by_code: dict[str, int]            # issue count per issue code (all issues)
    issues: list[Issue]                # first 500 only
    truncated: bool                    # True when more issues exist than are listed
    columns: Optional[list[str]] = None   # the ticked columns; None = all columns
    warnings: list[str] = Field(default_factory=list)


class InferResult(BaseModel):
    ruleset: Ruleset
    notes: dict[str, list[str]]        # advice per column, shown next to the editor
