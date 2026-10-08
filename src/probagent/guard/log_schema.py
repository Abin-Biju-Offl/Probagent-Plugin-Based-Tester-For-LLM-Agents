"""What the data guard ("guard") writes to the run log.

    Action    What it records
    check     data an agent's tool pulled was checked before the agent saw it

Like the dq feature, there is deliberately no field for data values. Issues
are logged by position only (row, column, code), which is what
`probagent explain` reads back.
A check that could not run (unknown source, unreadable data) is logged with
status "error" and no outputs. The data was blocked: the guard fails closed.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from probagent.dq.log_schema import DatasetRef
from probagent.runlog import register_action

FEATURE = "guard"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


MAX_ISSUES_LOGGED = 100    # keeps one blocked call to a few KB of log


class GuardCheckInputs(_Strict):
    source: str
    dataset: Optional[DatasetRef] = None       # None when the data could not be read
    ruleset_name: Optional[str] = None         # None when the source is unknown
    ruleset_sha256: Optional[str] = None
    ruleset: Optional[dict] = None             # the rules used, so `explain` can show them
    on_fail: Optional[str] = None
    columns: Optional[list[str]] = None        # None = every column in the rules


class IssueRef(_Strict):
    """Where an issue is, without the value: row, column and issue code."""

    row: Optional[int] = None                  # None = the whole dataset
    column: Optional[str] = None
    code: str


class GuardCheckOutputs(_Strict):
    verdict: Literal["pass", "fail"]
    action: Literal["allow", "block", "warn", "filter"]
    issue_count: int
    rows_with_issues: int
    rows_removed: int
    by_code: dict[str, int]
    issues: list[IssueRef] = []                # the first MAX_ISSUES_LOGGED issues
    issues_truncated: bool = False             # True when there were more


register_action(FEATURE, "check", GuardCheckInputs, GuardCheckOutputs)
