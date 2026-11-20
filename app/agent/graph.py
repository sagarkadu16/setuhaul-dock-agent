"""The agent loop (plan §7.1, §7.4, task T4.4): agent node ⇄ tool node, checkpointed per conversation.

    START → agent ──(tool calls?)──► tools ──► agent … ──(no tool calls)──► END
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agent.prompt import build_system_prompt
from app.agent.tools import TOOLS
from app.db import connect_readonly

log = logging.getLogger(__name__)

SORRY = "Sorry, I can't respond right now, please try again."
RECURSION_LIMIT = 12   # agent+tool steps per turn; a normal turn uses 2-6


@dataclass
class TurnResult:
    reply: str
    tool_calls: list[dict] = field(default_factory=list)   # [{"name", "args"}] made this turn
    failed: bool = False


def open_checkpointer(path: Path | str) -> SqliteSaver:
    """Agent memory in its own SQLite file (plan §2). One per process; shared across threads."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))


class Agent:
    def __init__(self, model: BaseChatModel, checkpointer: SqliteSaver, db_path: Path | str | None = None):
        self._model = model.bind_tools(TOOLS)
        self._db_path = str(db_path) if db_path else None

        graph = StateGraph(MessagesState)
        graph.add_node("agent", self._call_model)
        graph.add_node("tools", ToolNode(TOOLS))
        graph.add_edge(START, "agent")
        graph.add_conditional_edges("agent", tools_condition)
        graph.add_edge("tools", "agent")
        self._graph = graph.compile(checkpointer=checkpointer)

    def _call_model(self, state: MessagesState, config: RunnableConfig) -> dict:
        driver_id = config["configurable"]["driver_id"]
        with closing(connect_readonly(self._db_path)) as conn:
            row = conn.execute("SELECT driver_name FROM drivers WHERE driver_id = ?", (driver_id,)).fetchone()
            prompt = build_system_prompt(conn, driver_id, row["driver_name"] if row else "")
        # The system prompt is not stored in memory: it is rebuilt from fresh data every turn.
        response = self._model.invoke([SystemMessage(prompt), *state["messages"]], config)
        return {"messages": [response]}

    def _config(self, thread_id: str, driver_id: str) -> RunnableConfig:
        return {
            "configurable": {"thread_id": thread_id, "driver_id": driver_id, "db_path": self._db_path},
            "recursion_limit": RECURSION_LIMIT,
        }

    def run_turn(self, thread_id: str, driver_id: str, text: str) -> TurnResult:
        """Run one driver message through the agent. Never raises: on any failure the driver gets
        SORRY (spec §8) and the conversation memory is left in a state the next turn can use."""
        config = self._config(thread_id, driver_id)
        try:
            state = self._graph.invoke({"messages": [HumanMessage(text)]}, config)
        except Exception:
            log.exception("agent turn failed (thread %s)", thread_id)
            self._repair(config)
            return TurnResult(reply=SORRY, failed=True)

        messages = state["messages"]
        last_human = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
        this_turn = messages[last_human + 1:]
        calls = [{"name": c["name"], "args": c["args"]}
                 for m in this_turn if isinstance(m, AIMessage) for c in m.tool_calls]
        reply = this_turn[-1].text.strip() if this_turn and isinstance(this_turn[-1], AIMessage) else ""
        if not reply:
            return TurnResult(reply=SORRY, tool_calls=calls, failed=True)
        return TurnResult(reply=reply, tool_calls=calls)

    def _repair(self, config: RunnableConfig) -> None:
        """After a failure, answer any unanswered tool calls and record the SORRY reply, so the
        saved history stays valid (a tool call must always be followed by its result)."""
        try:
            messages = self._graph.get_state(config).values.get("messages", [])
            answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
            patch = [
                ToolMessage(content='{"error": "interrupted"}', tool_call_id=c["id"])
                for m in messages if isinstance(m, AIMessage)
                for c in m.tool_calls if c["id"] not in answered
            ]
            patch.append(AIMessage(SORRY))
            self._graph.update_state(config, {"messages": patch}, as_node="agent")
        except Exception:
            log.exception("could not repair agent memory (thread %s)", config["configurable"]["thread_id"])
