"""A LangGraph agent whose data tool is guarded by Probagent.

    pip install -e ".[langgraph]"
    python examples/langgraph_guard/agent.py

The agent asks for the orders twice: once for clean data (passed on as it
is) and once for dirty data (blocked before the model sees it).

The "model" here is scripted, so the example runs offline with no API key.
In your agent, keep the ToolNode line as it is and use your real chat model
(bound to the tools) in place of `scripted_model`.
"""

import csv
import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from probagent.guard import DataGuard
from probagent.guard.langgraph import guard_tool_calls

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parents[1] / "fixtures" / "dq"


@tool
def get_orders(which: str = "clean") -> list[dict]:
    """Get the list of orders. `which` is "clean" or "dirty" (demo data)."""
    with (FIXTURES / f"orders_{which}.csv").open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def scripted_model(state: MessagesState) -> dict:
    """Stands in for an LLM: call the tool first, then answer from its result."""
    last = state["messages"][-1]
    if isinstance(last, HumanMessage):
        call = {"name": "get_orders", "args": {"which": last.content}, "id": "call_1"}
        return {"messages": [AIMessage(content="", tool_calls=[call])]}

    result = json.loads(last.content)
    if isinstance(result, dict) and result.get("blocked"):
        answer = (f"I can't answer from the orders data: it failed its quality check "
                  f"({result.get('issue_count', 0)} issues). Reference: {result['run_id']} "
                  f"(look it up with: probagent explain {result['run_id']})")
    else:
        answer = f"There are {len(result)} orders."
    return {"messages": [AIMessage(content=answer)]}


def build_graph(guard: DataGuard):
    tool_node = ToolNode(
        [get_orders],
        wrap_tool_call=guard_tool_calls(guard, {"get_orders": "orders"}),   # the guard
    )
    graph = StateGraph(MessagesState)
    graph.add_node("agent", scripted_model)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile()


def main() -> None:
    guard = DataGuard.from_config(HERE / "guard.json")
    app = build_graph(guard)
    for which in ("clean", "dirty"):
        messages = app.invoke({"messages": [HumanMessage(content=which)]})["messages"]
        tool_message = next(m for m in messages if isinstance(m, ToolMessage))
        print(f"--- {which} data ---")
        print(f"Tool message status: {tool_message.status}")
        print(f"Tool message (first 300 chars): {tool_message.content[:300]}")
        print(f"Agent answer: {messages[-1].content}\n")


if __name__ == "__main__":
    main()
