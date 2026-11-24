"""The agent loop: LLM node <-> tool node, until the LLM replies without tool calls."""

from langchain_core.messages import SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from setuhaul.agent.llm import make_llm
from setuhaul.agent.tools import build_tools
from setuhaul.tools.context import ToolContext


def build_graph(
    ctx: ToolContext, system_text: str, checkpointer: BaseCheckpointSaver
) -> CompiledStateGraph:
    tools = build_tools(ctx)
    llm = make_llm().bind_tools(tools, parallel_tool_calls=False)

    def agent(state: MessagesState) -> dict:
        # The system prompt is prepended per call and never saved in state,
        # so the "now" time and prompt wording are always fresh.
        reply = llm.invoke([SystemMessage(system_text), *state["messages"]])
        return {"messages": [reply]}

    g = StateGraph(MessagesState)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode(tools))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)   # -> "tools" or END
    g.add_edge("tools", "agent")
    return g.compile(checkpointer=checkpointer)
