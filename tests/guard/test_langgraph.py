"""The LangGraph adapter, run through a real ToolNode and a real graph."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langgraph.graph import START, MessagesState, StateGraph  # noqa: E402
from langgraph.prebuilt import ToolNode  # noqa: E402

from probagent.guard import GuardConfigError  # noqa: E402
from probagent.guard.langgraph import aguard_tool_calls, guard_tool_calls  # noqa: E402

from guard_helpers import load_records  # noqa: E402


@tool
def get_orders(which: str) -> list[dict]:
    """Get the orders."""
    return load_records(f"orders_{which}.csv")


@tool
def get_orders_text(which: str) -> str:
    """Get the orders as JSON text."""
    return json.dumps({"data": load_records(f"orders_{which}.csv")})


@tool
def get_time() -> str:
    """Not a guarded tool."""
    return "not json at all"


@tool
def broken_orders() -> list[dict]:
    """Always fails."""
    raise ValueError("API down")


TOOLS = [get_orders, get_orders_text, get_time, broken_orders]
SOURCES = {"get_orders": "orders", "broken_orders": "orders"}


def call(node: ToolNode, name: str, args: dict | None = None, use_async=False) -> ToolMessage:
    """Run one tool call through the ToolNode, inside a one-node graph."""
    graph = StateGraph(MessagesState)
    graph.add_node("tools", node)
    graph.add_edge(START, "tools")
    app = graph.compile()
    message = AIMessage(content="", tool_calls=[{"name": name, "args": args or {}, "id": "c1"}])
    state = {"messages": [message]}
    out = asyncio.run(app.ainvoke(state)) if use_async else app.invoke(state)
    return out["messages"][-1]


@pytest.fixture
def node(make_guard):
    return ToolNode(TOOLS, wrap_tool_call=guard_tool_calls(make_guard(), SOURCES),
                    handle_tool_errors=True)


def test_clean_data_reaches_the_model_unchanged(node):
    message = call(node, "get_orders", {"which": "clean"})
    assert message.status == "success"
    assert len(json.loads(message.content)) == 200


def test_dirty_data_is_replaced_by_a_block_message(node):
    message = call(node, "get_orders", {"which": "dirty"})
    assert (message.status, message.name, message.tool_call_id) == ("error", "get_orders", "c1")
    content = json.loads(message.content)
    assert content["blocked"] is True
    assert content["issue_count"] == 13
    assert "@example" not in message.content


def test_unmapped_tools_are_untouched(node):
    assert call(node, "get_time").content == "not json at all"


def test_tool_errors_pass_through(node):
    message = call(node, "broken_orders")
    assert message.status == "error"
    assert "API down" in message.content


def test_warn_and_data_path_through_tool_node(make_guard):
    guard = make_guard(on_fail="warn", data_path="data")
    node = ToolNode(TOOLS, wrap_tool_call=guard_tool_calls(guard, {"get_orders_text": "orders"}))
    message = call(node, "get_orders_text", {"which": "dirty"})
    content = json.loads(message.content)
    assert message.status == "success"
    assert content["quality_warning"]["issue_count"] == 13
    assert len(content["data"]["data"]) == 201


def test_async_tool_node(make_guard):
    guard = make_guard()
    node = ToolNode(TOOLS, wrap_tool_call=guard_tool_calls(guard, SOURCES),
                    awrap_tool_call=aguard_tool_calls(guard, SOURCES))
    assert json.loads(call(node, "get_orders", {"which": "dirty"}, use_async=True).content)[
        "blocked"] is True


def test_unknown_source_fails_when_the_graph_is_built(make_guard):
    with pytest.raises(GuardConfigError, match="customers"):
        guard_tool_calls(make_guard(), {"get_orders": "customers"})


def test_example_agent(tmp_path, monkeypatch):
    """examples/langgraph_guard runs: clean data answered, dirty data refused."""
    example = Path(__file__).resolve().parents[2] / "examples" / "langgraph_guard"
    monkeypatch.syspath_prepend(str(example))
    sys.modules.pop("agent", None)
    import agent

    app = agent.build_graph(agent.DataGuard.from_config(example / "guard.json"))
    clean = app.invoke({"messages": [HumanMessage(content="clean")]})["messages"][-1]
    dirty = app.invoke({"messages": [HumanMessage(content="dirty")]})["messages"][-1]
    assert clean.content == "There are 200 orders."
    assert "failed its quality check (13 issues)" in dirty.content
