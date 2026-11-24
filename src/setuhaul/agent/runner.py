"""One chat turn, end to end. The only function the API calls for chat."""

import logging
import sqlite3

from langchain_core.messages import HumanMessage

from setuhaul.agent.graph import build_graph
from setuhaul.agent.memory import get_checkpointer
from setuhaul.agent.prompts import system_prompt
from setuhaul.clock import Clock
from setuhaul.db import queries
from setuhaul.services import chat
from setuhaul.tools.context import ToolContext

log = logging.getLogger(__name__)

MAX_GRAPH_STEPS = 12   # about 5 tool calls per turn, then stop (runaway-loop guard)
FALLBACK_REPLY = "Sorry, I'm having trouble right now. Please try again in a minute."


def run_turn(
    conn: sqlite3.Connection, driver_id: str, thread_id: str, text: str, external_id: str,
    *, business_clock: Clock, wall_clock: Clock,
) -> str:
    stored = chat.record_driver_message(
        conn, driver_id, thread_id, text, external_id, wall_clock.now()
    )   # raises InvalidMessage / NotYourThread: the API maps these to 4xx
    if stored.is_retry:
        return queries.last_agent_message(conn, thread_id) or "Still working on your last message."

    driver = queries.get_driver(conn, driver_id)
    ctx = ToolContext(conn, driver_id, business_clock, wall_clock)
    graph = build_graph(ctx, system_prompt(driver.driver_name, business_clock.now()),
                        get_checkpointer())
    try:
        out = graph.invoke(
            {"messages": [HumanMessage(stored.text)]},
            {"configurable": {"thread_id": thread_id}, "recursion_limit": MAX_GRAPH_STEPS},
        )
        reply = out["messages"][-1].content or FALLBACK_REPLY
    except Exception:
        log.exception("agent turn failed thread=%s driver=%s", thread_id, driver_id)
        reply = FALLBACK_REPLY

    chat.record_agent_message(conn, thread_id, reply, wall_clock.now())
    return reply
