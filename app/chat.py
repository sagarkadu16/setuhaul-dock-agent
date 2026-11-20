"""One driver message, end to end (plan §5.2): clean → store → agent → store reply.

Used by the web layer and by tests and scripts, so they all follow exactly the same steps.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from app.agent.graph import Agent
from app.conversations import Message, append_message, require_owner
from app.input_clean import clean_message


@dataclass(frozen=True)
class ChatTurn:
    driver_message: Message
    agent_message: Message
    tool_calls: list[dict] = field(default_factory=list)


def handle_driver_message(
    conn: sqlite3.Connection, agent: Agent, thread_id: str, driver_id: str, raw_text: str
) -> ChatTurn:
    """Raises InvalidMessage (nothing stored, agent not called; AC6) or NotFound (R5)."""
    text = clean_message(raw_text)
    require_owner(conn, thread_id, driver_id)
    driver_message = append_message(conn, thread_id, driver_id, "DRIVER", text)   # saved first (R9)
    result = agent.run_turn(thread_id, driver_id, text)
    agent_message = append_message(conn, thread_id, driver_id, "AGENT", result.reply)
    return ChatTurn(driver_message, agent_message, result.tool_calls)
