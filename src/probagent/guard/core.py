"""The data guard: check data the moment an agent's tool pulls it.

    guard = DataGuard.from_config("guard.json")

    @guard.protect("orders")               # checked on every call
    def get_orders() -> list[dict]:
        return httpx.get(API).json()

    result = guard.check(data, "orders")   # or check data by hand

What the agent gets back (GuardResult.for_agent()):

    pass            the data, unchanged
    fail + block    {"blocked": true, ...}   counts and codes only, no data
    fail + warn     {"data": ..., "quality_warning": {...}}
    fail + filter   {"data": <clean rows only>, "quality_note": {...}}
    error           blocked, like fail + block. The guard fails closed.

What the agent sees never contains cell values. A bad cell could hold text
such as "ignore your instructions" (indirect prompt injection), so only
codes, counts and the column names from the rules are passed on. The full
report, with values, stays on GuardResult.report for people.
"""

import csv
import functools
import hashlib
import inspect
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional, TypeVar, Union

from probagent.dq.loader import LoadError, load_csv_bytes, records_to_dataset
from probagent.dq.log_schema import describe_dataset, ruleset_as_dict
from probagent.dq.models import Dataset, Report
from probagent.dq.validator import build_report, find_issues
from probagent.guard.config import GuardConfigError, GuardedSource, load_config
from probagent.guard.log_schema import FEATURE, MAX_ISSUES_LOGGED, IssueRef
from probagent.runlog import Run, RunLog

Verdict = Literal["pass", "fail", "error"]
Action = Literal["allow", "block", "warn", "filter"]
F = TypeVar("F", bound=Callable[..., Any])

BLOCK_MESSAGE = ("The {source} data failed its quality check, so it was not passed on. "
                 "Do not guess or make up the missing data. Tell the user the data "
                 "could not be used, and give them the run_id as a reference.")
ERROR_MESSAGE = ("The {source} data could not be checked, so it was not passed on. "
                 "Do not guess or make up the missing data. Tell the user the data "
                 "is unavailable, and give them the run_id as a reference.")
WARN_MESSAGE = ("Some rows in this data failed the quality check. Say so when you "
                "use it.")
FILTER_MESSAGE = ("{removed} of {total} rows failed the quality check and were removed. "
                  "Answers based on this data cover only the remaining rows.")


class GuardDataError(Exception):
    """The data could not be read, so it could not be checked."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class GuardResult:
    source: str
    verdict: Verdict
    action: Action
    data: Any = None                       # what may be handed on; None when blocked
    report: Optional[Report] = None        # full report for people. Never send it to a model.
    summary: dict[str, Any] = field(default_factory=dict)   # safe to show the agent
    run_id: Optional[str] = None

    @property
    def allowed(self) -> bool:
        return self.action != "block"

    def for_agent(self) -> Any:
        """What the agent should receive instead of the raw tool output."""
        summary = {**self.summary, "run_id": self.run_id}
        if self.action == "allow":
            return self.data
        if self.action == "warn":
            return {"data": self.data, "quality_warning": summary}
        if self.action == "filter":
            return {"data": self.data, "quality_note": summary}
        return {"blocked": True, **summary}


class DataGuard:
    """Checks data from named sources against their rules before an agent uses it."""

    def __init__(self, sources: dict[str, GuardedSource], run_log: Optional[RunLog] = None,
                 config_path: Optional[Path] = None):
        self.sources = sources
        self.run_log = run_log or RunLog()
        self.config_path = config_path

    @classmethod
    def from_config(cls, path: Union[str, Path], run_log: Optional[RunLog] = None) -> "DataGuard":
        """Load guard.json and its rules files. Raises GuardConfigError if anything is wrong."""
        path = Path(path)
        return cls(load_config(path), run_log, config_path=path)

    def reload(self) -> None:
        """Read the config and rules files again, e.g. after editing a rules file.

        If the new files have a problem, GuardConfigError is raised and the
        guard keeps using the rules it already had.
        """
        if self.config_path is None:
            raise GuardConfigError(["This guard was not loaded from a config file."])
        self.sources = load_config(self.config_path)

    # ---------- Checking ----------

    def check(self, data: Any, source: str) -> GuardResult:
        """Check data from one source. Never raises for bad data: it blocks instead."""
        with self.run_log.track(FEATURE, "check", origin="agent") as run:
            result = self._check(data, source, run)
            result.run_id = run.run_id
        return result

    def fail_closed(self, source: str, code: str, message: str) -> GuardResult:
        """Block a source's data without checking it, and log why.

        Used by adapters when a tool's output is in a shape the guard can't read.
        """
        with self.run_log.track(FEATURE, "check", origin="agent") as run:
            run.set_inputs(source=source)
            result = self._error(source, run, code, message)
            result.run_id = run.run_id
        return result

    def _check(self, data: Any, source: str, run: Run) -> GuardResult:
        guarded = self.sources.get(source)
        if guarded is None:
            run.set_inputs(source=source)
            return self._error(source, run, "unknown_source",
                               f'"{source}" is not a source in the guard config.')
        policy = guarded.policy
        inputs = dict(source=source, ruleset_name=guarded.ruleset.name,
                      ruleset_sha256=guarded.ruleset_sha256,
                      ruleset=ruleset_as_dict(guarded.ruleset), on_fail=policy.on_fail,
                      columns=policy.columns)

        try:
            loaded = read_data(data, guarded)
        except GuardDataError as error:
            run.set_inputs(**inputs)
            return self._error(source, run, error.code, error.message)
        run.set_inputs(dataset=describe_dataset(loaded.dataset), **inputs)

        # An empty list is a normal answer ("no orders today"), and has no bad rows.
        if not loaded.dataset.rows:
            run.set_outputs(verdict="pass", action="allow", issue_count=0, rows_with_issues=0,
                            rows_removed=0, by_code={})
            return GuardResult(source, "pass", "allow", data=data)

        issues, columns_checked, warnings = find_issues(loaded.dataset, guarded.ruleset,
                                                        policy.columns)
        bad_rows = {issue.row for issue in issues if issue.row is not None}
        dataset_issue = any(issue.row is None for issue in issues)
        report = build_report(loaded.dataset, guarded.ruleset, issues, columns_checked, warnings)
        report.columns = policy.columns

        total = len(loaded.dataset.rows)
        bad_pct = len(bad_rows) / total * 100
        rows_removed = 0
        if not issues:
            verdict, action, out = "pass", "allow", data
        elif policy.on_fail == "warn":
            # Parsed JSON, so JSON text isn't wrapped as a string inside {"data": ...}.
            verdict, action = "fail", "warn"
            out = loaded.parsed if loaded.records is not None else data
        elif (policy.on_fail == "filter" and not dataset_issue
              and bad_pct <= policy.max_bad_rows_pct):
            verdict, action = "fail", "filter"
            out = loaded.keep_rows(bad_rows)
            rows_removed = len(bad_rows)
        else:
            # block, or a filter that can't be done safely: a missing column
            # can't be fixed by removing rows, and removing most rows would
            # leave answers that look complete but aren't.
            verdict, action, out = "fail", "block", None

        run.set_outputs(verdict=verdict, action=action, issue_count=report.summary.issue_count,
                        rows_with_issues=report.summary.rows_with_issues,
                        rows_removed=rows_removed, by_code=report.by_code,
                        issues=[IssueRef(row=i.row, column=i.column, code=i.code)
                                for i in issues[:MAX_ISSUES_LOGGED]],
                        issues_truncated=len(issues) > MAX_ISSUES_LOGGED)
        summary = {} if action == "allow" else safe_summary(
            source, action, report, guarded, rows_removed)
        return GuardResult(source, verdict, action, data=out, report=report, summary=summary)

    def _error(self, source: str, run: Run, code: str, message: str) -> GuardResult:
        run.fail(code, message)
        summary = {"source": source, "reason": "guard_error", "error_code": code,
                   "message": ERROR_MESSAGE.format(source=source)}
        return GuardResult(source, "error", "block", summary=summary)

    # ---------- Decorator ----------

    def protect(self, source: str) -> Callable[[F], F]:
        """Check what the decorated function returns, and return GuardResult.for_agent().

        Works on sync and async functions. Put it under @tool, so the tool's
        name, arguments and docstring stay the same:

            @tool
            @guard.protect("orders")
            def get_orders(day: str) -> list[dict]: ...
        """
        if source not in self.sources:
            raise GuardConfigError([f'"{source}" is not a source in the guard config. '
                                    "Known sources: " + ", ".join(self.sources)])

        def decorate(function: F) -> F:
            if inspect.iscoroutinefunction(function):
                @functools.wraps(function)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    return self.check(await function(*args, **kwargs), source).for_agent()
                return async_wrapper  # type: ignore[return-value]

            @functools.wraps(function)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                return self.check(function(*args, **kwargs), source).for_agent()
            return wrapper  # type: ignore[return-value]

        return decorate


# ---------- Reading the data ----------

@dataclass
class LoadedData:
    dataset: Dataset
    parsed: Any = None          # json only: the whole parsed value
    data_keys: tuple = ()       # json only: the path to the list of records
    records: Optional[list] = None

    def keep_rows(self, bad_rows: set[int]) -> Any:
        """The data in its original shape, minus the bad rows (row numbers start at 1)."""
        if self.records is None:                 # csv: rebuild the text
            out = io.StringIO()
            writer = csv.writer(out, lineterminator="\n")
            writer.writerow(self.dataset.columns)
            writer.writerows(row for number, row in enumerate(self.dataset.rows, start=1)
                             if number not in bad_rows)
            return out.getvalue()
        kept = [record for number, record in enumerate(self.records, start=1)
                if number not in bad_rows]
        return replace_at_path(self.parsed, self.data_keys, kept)


def read_data(data: Any, guarded: GuardedSource) -> LoadedData:
    policy = guarded.policy
    if policy.format == "csv":
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not isinstance(data, bytes):
            raise GuardDataError("unreadable_data", "CSV data must be text or bytes, got "
                                 f"{type(data).__name__}.")
        try:
            return LoadedData(load_csv_bytes(data, source=guarded.name))
        except LoadError as error:
            raise GuardDataError("load_failed", str(error)) from None

    parsed = data
    if isinstance(data, (str, bytes)):
        try:
            parsed = json.loads(data)
        except ValueError:
            raise GuardDataError("unreadable_data", "The data is not valid JSON.") from None

    keys = tuple(key for key in policy.data_path.strip().split(".") if key)
    records = parsed
    for key in keys:
        if not isinstance(records, dict) or key not in records:
            raise GuardDataError("data_path_not_found", f'The data path "{policy.data_path}" '
                                 f'was not found (stopped at "{key}").')
        records = records[key]

    if records == []:
        return LoadedData(Dataset(columns=[], rows=[], source=guarded.name))
    try:
        dataset = records_to_dataset(records, source=guarded.name)
    except LoadError as error:
        raise GuardDataError("load_failed", str(error)) from None
    dataset.sha256 = fingerprint(data)
    return LoadedData(dataset, parsed=parsed, data_keys=keys, records=records)


def replace_at_path(value: Any, keys: tuple, new: Any) -> Any:
    """A copy of value with value[k1][k2]... replaced. The original is not changed."""
    if not keys:
        return new
    return {**value, keys[0]: replace_at_path(value[keys[0]], keys[1:], new)}


def fingerprint(data: Any) -> str:
    """sha256 of the raw bytes, or of a stable JSON form for Python objects."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not isinstance(data, bytes):
        data = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def safe_summary(source: str, action: Action, report: Report, guarded: GuardedSource,
                 rows_removed: int) -> dict[str, Any]:
    """Counts, codes and rule column names only: nothing taken from the data itself.

    Column names not in the rules come from the data, so they are left out
    (they show up only in the unexpected_column count).
    """
    s = report.summary
    summary: dict[str, Any] = {
        "source": source,
        "rows_checked": s.rows_checked,
        "issue_count": s.issue_count,
        "rows_with_issues": s.rows_with_issues,
        "by_code": report.by_code,
        "by_column": {column: count for column, count in report.by_column.items()
                      if column in guarded.ruleset.fields},
    }
    if action == "block":
        summary = {"source": source, "reason": "data_quality",
                   "message": BLOCK_MESSAGE.format(source=source), **summary}
    elif action == "warn":
        summary["message"] = WARN_MESSAGE
    elif action == "filter":
        summary["rows_removed"] = rows_removed
        summary["message"] = FILTER_MESSAGE.format(removed=rows_removed, total=s.rows_checked)
    return summary
