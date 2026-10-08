"""Data guard: a pre-check that runs whenever an agent's tool pulls data.

The flow is:  tool returns data  ->  check against the source's rules  ->
              allow / block / warn / filter  ->  the agent sees the result.

    config.py       guard.json: sources, rules files, policies
    core.py         DataGuard, GuardResult, the @guard.protect decorator
    langgraph.py    LangGraph adapter (ToolNode wrap_tool_call)
    log_schema.py   what a guard check writes to the run log

The checking itself is the Data Quality Checker (probagent.dq), unchanged.
"""

from probagent.guard.config import GuardConfigError
from probagent.guard.core import DataGuard, GuardResult

__all__ = ["DataGuard", "GuardConfigError", "GuardResult"]
