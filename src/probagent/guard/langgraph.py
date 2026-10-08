"""LangGraph adapter: guard the data your tools return, inside a ToolNode.

    from langgraph.prebuilt import ToolNode
    from probagent.guard import DataGuard
    from probagent.guard.langgraph import guard_tool_calls

    guard = DataGuard.from_config("guard.json")
    tool_node = ToolNode(
        [get_orders, get_weather],
        wrap_tool_call=guard_tool_calls(guard, {"get_orders": "orders"}),
    )

The map says which tool's output is which guarded source. Tools that aren't
in the map run as usual. For an async graph, pass the same map to
`aguard_tool_calls` as `awrap_tool_call` as well.

This uses ToolNode's `wrap_tool_call` hook (langgraph-prebuilt 1.0 and later).
For agents built another way, put `@guard.protect("orders")` on the tool function.
"""

import json
from typing import Any, Awaitable, Callable

from langchain_core.messages import ToolMessage

from probagent.guard.config import GuardConfigError
from probagent.guard.core import DataGuard


def guard_tool_calls(guard: DataGuard, tool_sources: dict[str, str]) -> Callable:
    """A ToolNode `wrap_tool_call` that checks the output of the mapped tools."""
    check_tool_sources(guard, tool_sources)

    def wrap_tool_call(request: Any, execute: Callable[[Any], Any]) -> Any:
        return guard_message(guard, tool_sources, request, execute(request))

    return wrap_tool_call


def aguard_tool_calls(guard: DataGuard, tool_sources: dict[str, str]) -> Callable:
    """The async version, for ToolNode's `awrap_tool_call`."""
    check_tool_sources(guard, tool_sources)

    async def awrap_tool_call(request: Any, execute: Callable[[Any], Awaitable[Any]]) -> Any:
        return guard_message(guard, tool_sources, request, await execute(request))

    return awrap_tool_call


def check_tool_sources(guard: DataGuard, tool_sources: dict[str, str]) -> None:
    """Fail when the graph is built, not on the first tool call."""
    unknown = sorted(set(tool_sources.values()) - set(guard.sources))
    if unknown:
        raise GuardConfigError(["These sources are not in the guard config: "
                                + ", ".join(unknown)])


def guard_message(guard: DataGuard, tool_sources: dict[str, str], request: Any,
                  result: Any) -> Any:
    """Check one tool result and return the message the model should see."""
    tool_call = request.tool_call
    source = tool_sources.get(tool_call["name"])
    if source is None:
        return result

    if isinstance(result, ToolMessage) and result.status == "error":
        return result          # the tool itself failed: there is no data to check

    if isinstance(result, ToolMessage) and isinstance(result.content, str):
        checked = guard.check(result.content, source)
        if checked.action == "allow":
            return result
    else:
        # A Command or content blocks: the guard can't read it, so it fails closed.
        checked = guard.fail_closed(source, "unsupported_tool_result",
                                    f"The tool returned {type(result).__name__}, which the "
                                    "guard cannot check.")

    return ToolMessage(
        content=json.dumps(checked.for_agent(), ensure_ascii=False, default=str),
        tool_call_id=tool_call["id"],
        name=tool_call["name"],
        status="success" if checked.allowed else "error",
    )
