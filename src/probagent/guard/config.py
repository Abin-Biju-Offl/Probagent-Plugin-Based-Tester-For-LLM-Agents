"""guard.json: which data sources are guarded, with which rules and policy.

    {
      "version": 1,
      "sources": {
        "orders": {
          "rules": "rules/orders_rules.json",   relative to guard.json
          "format": "json",                     "json" (default) or "csv"
          "data_path": "data",                  json only: where the list of records is
          "on_fail": "block",                   "block" (default) | "warn" | "filter"
          "max_bad_rows_pct": 10,               filter only: block instead if more rows are bad
          "columns": ["email", "quantity"]      optional: only check these columns
        }
      }
    }

The rules files use the same format as the Data Quality Checker.

Everything is checked when the guard starts: the config, every rules file,
and that the rules make sense. A mistake stops the guard before the agent
runs, instead of surfacing in the middle of a conversation.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from probagent.dq.log_schema import ruleset_sha256
from probagent.dq.models import Ruleset
from probagent.dq.rules import RulesetError, parse_ruleset

OnFail = Literal["block", "warn", "filter"]


class GuardConfigError(Exception):
    """The guard config or a rules file is invalid. `errors` lists every problem found."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class SourcePolicy(BaseModel):
    """How one data source is checked, and what happens when it fails."""

    model_config = ConfigDict(extra="forbid")

    rules: str                                   # path to a rules .json file
    format: Literal["json", "csv"] = "json"
    data_path: str = ""                          # e.g. "data.items"
    on_fail: OnFail = "block"
    max_bad_rows_pct: float = Field(default=10, ge=0, le=100)
    columns: Optional[list[str]] = None          # None = every column in the rules


class GuardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    sources: dict[str, SourcePolicy]


@dataclass
class GuardedSource:
    """One source, ready to use: its policy plus its parsed rules."""

    name: str
    policy: SourcePolicy
    ruleset: Ruleset
    ruleset_sha256: str


def load_config(path: Path) -> dict[str, GuardedSource]:
    """Read guard.json and every rules file it names. Raises GuardConfigError."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise GuardConfigError([f"Could not read the guard config {path}."]) from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise GuardConfigError([f"{path.name} is not valid JSON: {error}"]) from None
    return build_sources(data, base_dir=path.parent)


def build_sources(data: Any, base_dir: Path) -> dict[str, GuardedSource]:
    """Turn config JSON into sources. Rules paths are relative to base_dir."""
    if not isinstance(data, dict):
        raise GuardConfigError(["The guard config must be a JSON object: { ... }"])
    try:
        config = GuardConfig.model_validate(data)
    except ValidationError as error:
        raise GuardConfigError([_friendly_message(e) for e in error.errors()]) from None

    if not config.sources:
        raise GuardConfigError(['"sources" is empty. Add at least one data source.'])

    # Collect the problems of every source, so they can all be fixed in one go.
    errors: list[str] = []
    sources = {}
    for name, policy in config.sources.items():
        try:
            sources[name] = load_source(name, policy, base_dir)
        except GuardConfigError as error:
            errors.extend(error.errors)
    if errors:
        raise GuardConfigError(errors)
    return sources


def load_source(name: str, policy: SourcePolicy, base_dir: Path) -> GuardedSource:
    prefix = f'Source "{name}": '
    rules_file = base_dir / policy.rules
    try:
        rules_data = json.loads(rules_file.read_text(encoding="utf-8"))
    except OSError:
        raise GuardConfigError([prefix + f"could not read the rules file {policy.rules}."]) \
            from None
    except json.JSONDecodeError as error:
        raise GuardConfigError([prefix + f"{policy.rules} is not valid JSON: {error}"]) \
            from None
    try:
        ruleset = parse_ruleset(rules_data)
    except RulesetError as error:
        raise GuardConfigError([prefix + f"{policy.rules}: {problem}"
                                for problem in error.errors]) from None

    problems = []
    if policy.format == "csv" and policy.data_path:
        problems.append('data_path only works with format "json".')
    if policy.columns is not None:
        if not policy.columns:
            problems.append('"columns" is an empty list, so nothing would be checked.')
        # A typo here would silently skip a column, so it is an error, not a warning.
        unknown = [column for column in policy.columns if column not in ruleset.fields]
        if unknown:
            problems.append("these columns have no rules in " + policy.rules + ": "
                            + ", ".join(unknown))
    if problems:
        raise GuardConfigError([prefix + problem for problem in problems])

    return GuardedSource(name=name, policy=policy, ruleset=ruleset,
                         ruleset_sha256=ruleset_sha256(ruleset))


def _friendly_message(error: dict) -> str:
    location = [str(part) for part in error["loc"]]
    where = f'Source "{location[1]}": ' if len(location) >= 2 and location[0] == "sources" \
        else ""
    if error["type"] == "extra_forbidden":
        known = SourcePolicy.model_fields if where else GuardConfig.model_fields
        return f'{where}unknown setting "{location[-1]}". Known settings: ' \
               + ", ".join(known) + "."
    if location == ["sources"] and error["type"] == "missing":
        return '"sources" is missing. Add a "sources" object with one entry per data source.'
    field = location[-1] if where else ".".join(location) or "config"
    return f'{where}"{field}": {error["msg"]}.'
