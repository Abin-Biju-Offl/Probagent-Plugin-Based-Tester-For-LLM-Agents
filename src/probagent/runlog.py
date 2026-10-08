"""Run log: one line of JSON for every action Probagent runs.

File:  .probagent/runs.jsonl   (in the folder you start Probagent from)
       One JSON object per line, oldest at the top, newest at the bottom.
       Set the PROBAGENT_LOG_DIR environment variable to use another folder.

Every line has the same envelope (`RunEntry`). The `inputs` and `outputs` of
each action have their own model, registered by the feature that owns the
action. That keeps the log well-defined as more features are added:

    register_action("dq", "check", CheckInputs, CheckOutputs)   # once, at import

    with run_log.track("dq", "check", origin="web") as run:
        run.set_inputs(...)            # checked against CheckInputs
        ...
        run.set_outputs(...)           # checked against CheckOutputs
        # or, if it went wrong:  run.fail("invalid_rules", "The rules have problems.")

See docs/run_log.md for the full schema and how to search the log.
"""

import json
import logging
import os
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

from pydantic import BaseModel, ConfigDict

from probagent import __version__

SCHEMA_VERSION = 1
LOG_DIR_ENV = "PROBAGENT_LOG_DIR"
LOG_FILE_NAME = "runs.jsonl"
MAX_AGE_DAYS = 90          # older entries are removed
MAX_ENTRIES = 10_000       # only the newest entries are kept

logger = logging.getLogger(__name__)

Origin = Literal["web", "cli", "agent"]
Status = Literal["ok", "error"]


# ---------- The envelope: the same for every feature ----------

class RunError(BaseModel):
    code: str          # short, searchable, e.g. "auth_required"
    message: str       # plain-English explanation


class RunEntry(BaseModel):
    """One line in runs.jsonl."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    run_id: str                         # e.g. "20261007-143022-a1b2c3" (date-time + random)
    parent_run_id: Optional[str] = None # the run this one builds on, e.g. the upload
    feature: str                        # e.g. "dq"
    action: str                         # e.g. "check"
    origin: Origin                      # the web page, the terminal, or an agent's tool call
    status: Status                      # "ok" = it ran; "error" = it could not finish
    started_at: str                     # local time with UTC offset
    finished_at: str
    duration_ms: int
    app_version: str
    inputs: dict[str, Any]              # what went in (shape depends on feature/action)
    outputs: dict[str, Any]             # what came out (shape depends on feature/action)
    error: Optional[RunError] = None


# ---------- Registry: which actions exist, and their inputs/outputs ----------

@dataclass
class ActionSchema:
    inputs: type[BaseModel]
    outputs: type[BaseModel]


_ACTIONS: dict[tuple[str, str], ActionSchema] = {}


def register_action(feature: str, action: str,
                    inputs: type[BaseModel], outputs: type[BaseModel]) -> None:
    """Declare an action and the shape of its inputs and outputs."""
    _ACTIONS[(feature, action)] = ActionSchema(inputs, outputs)


def registered_actions() -> dict[tuple[str, str], ActionSchema]:
    return dict(_ACTIONS)


# ---------- One action being tracked ----------

class Run:
    """Collects what happened during one action. Created by `RunLog.track`."""

    def __init__(self, feature: str, action: str, origin: Origin,
                 parent_run_id: Optional[str]):
        if (feature, action) not in _ACTIONS:
            raise ValueError(f'Unknown action "{feature}/{action}". '
                             "Declare it first with register_action().")
        self.schema = _ACTIONS[(feature, action)]
        self.run_id = new_run_id()
        self.parent_run_id = parent_run_id
        self.feature = feature
        self.action = action
        self.origin = origin
        self.inputs: Optional[BaseModel] = None
        self.outputs: Optional[BaseModel] = None
        self.error: Optional[RunError] = None

    def set_inputs(self, **values: Any) -> None:
        """Record what went in. Unknown fields raise an error right away."""
        self.inputs = self.schema.inputs(**values)

    def set_outputs(self, **values: Any) -> None:
        """Record what came out. Unknown fields raise an error right away."""
        self.outputs = self.schema.outputs(**values)

    def fail(self, code: str, message: str) -> None:
        """Mark the run as an error, without raising an exception."""
        self.error = RunError(code=code, message=message)


# ---------- The log file ----------

class RunLog:
    """Appends RunEntry lines to runs.jsonl. Safe to use from several threads."""

    def __init__(self, log_dir: Optional[Path] = None):
        self.path = (log_dir or default_log_dir()) / LOG_FILE_NAME
        self._lock = threading.Lock()
        self._pruned = False

    @contextmanager
    def track(self, feature: str, action: str, origin: Origin,
              parent_run_id: Optional[str] = None) -> Iterator[Run]:
        """Time an action and write one log line when it ends, even if it crashes."""
        run = Run(feature, action, origin, parent_run_id)
        started_at = now()
        timer = time.perf_counter()
        try:
            yield run
        except Exception as error:
            # Only the error type is logged: the message could contain data values.
            run.fail("unexpected_error", type(error).__name__)
            raise
        finally:
            duration_ms = round((time.perf_counter() - timer) * 1000)
            self.write(RunEntry(
                run_id=run.run_id,
                parent_run_id=run.parent_run_id,
                feature=run.feature,
                action=run.action,
                origin=run.origin,
                status="error" if run.error else "ok",
                started_at=started_at.isoformat(timespec="seconds"),
                finished_at=now().isoformat(timespec="seconds"),
                duration_ms=duration_ms,
                app_version=__version__,
                inputs=run.inputs.model_dump() if run.inputs else {},
                outputs=run.outputs.model_dump() if run.outputs else {},
                error=run.error,
            ))

    def write(self, entry: RunEntry) -> None:
        """Append one line. A logging problem never stops the action itself."""
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if not self._pruned:
                    self._prune()
                    self._pruned = True
                with self.path.open("a", encoding="utf-8") as file:
                    file.write(entry.model_dump_json() + "\n")
        except OSError as error:
            logger.warning("Could not write the run log at %s: %s", self.path, error)

    def read(self) -> list[RunEntry]:
        """All entries in the file, oldest first. Lines that don't parse are skipped."""
        if not self.path.exists():
            return []
        entries = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                entries.append(RunEntry.model_validate_json(line))
            except ValueError:
                continue
        return entries

    def _prune(self) -> None:
        """Remove entries older than MAX_AGE_DAYS, then keep only the newest MAX_ENTRIES.

        Runs once per RunLog, before its first write. The file is only rewritten
        when something actually needs removing.
        """
        if not self.path.exists():
            return
        lines = self.path.read_text(encoding="utf-8").splitlines()
        cutoff = now() - timedelta(days=MAX_AGE_DAYS)
        keep = [line for line in lines if not _is_older_than(line, cutoff)]
        keep = keep[-MAX_ENTRIES:]
        if len(keep) == len(lines):
            return
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text("".join(line + "\n" for line in keep), encoding="utf-8")
        os.replace(temporary, self.path)   # swap in the new file in one step


# ---------- Helpers ----------

def default_log_dir() -> Path:
    return Path(os.environ.get(LOG_DIR_ENV) or Path.cwd() / ".probagent")


def now() -> datetime:
    """Local time, including the UTC offset (e.g. +05:30)."""
    return datetime.now(timezone.utc).astimezone()


def new_run_id() -> str:
    """Date and time first (easy to read and search by day), then 6 random characters.

    The file itself is in time order, because entries are only ever appended.
    """
    return f"{now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def _is_older_than(line: str, cutoff: datetime) -> bool:
    """True if the line's started_at is before cutoff. Unreadable lines are kept."""
    try:
        return datetime.fromisoformat(json.loads(line)["started_at"]) < cutoff
    except (ValueError, KeyError, TypeError):
        return False
