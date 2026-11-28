"""One chat turn, end to end. The only function the API calls for chat."""

import logging
import sqlite3
from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from setuhaul.agent.graph import build_graph
from setuhaul.agent.memory import get_checkpointer
from setuhaul.agent.prompts import system_prompt
from setuhaul.clock import Clock
from setuhaul.db import queries
from setuhaul.models.domain import SlotView
from setuhaul.services import chat
from setuhaul.services.guardrails import check_booking_claim
from setuhaul.tools.context import ToolContext

log = logging.getLogger(__name__)

MAX_GRAPH_STEPS = 12   # about 5 tool calls per turn, then stop (runaway-loop guard)
FALLBACK_REPLY = "Sorry, I'm having trouble right now. Please try again in a minute."


@dataclass(frozen=True)
class TurnResult:
    reply: str
    slots: list[SlotView] = field(default_factory=list)   # offer as buttons (from tools, not LLM)


def _turn_input(conn: sqlite3.Connection, thread_id: str, text: str) -> list[BaseMessage]:
    """The driver's message, preceded by any notifications delivered since the last reply.

    Notifications reach the chat outside the agent (UI polling), so its memory doesn't
    have them. Our own fixed templates, so safe to pass as system text.
    """
    notices = queries.system_notices_since_last_agent_reply(conn, thread_id)
    return [
        *(SystemMessage(f"SetuHaul update, already shown to the driver: {n}") for n in notices),
        HumanMessage(text),
    ]


def run_turn(
    conn: sqlite3.Connection, driver_id: str, thread_id: str, text: str, external_id: str,
    *, business_clock: Clock, wall_clock: Clock,
) -> TurnResult:
    stored = chat.record_driver_message(
        conn, driver_id, thread_id, text, external_id, wall_clock.now()
    )   # raises InvalidMessage / NotYourThread: the API maps these to 4xx
    if stored.is_retry:
        return TurnResult(queries.last_agent_message(conn, thread_id)
                          or "Still working on your last message.")

    driver = queries.get_driver(conn, driver_id)
    ctx = ToolContext(conn, driver_id, business_clock, wall_clock)
    graph = build_graph(ctx, system_prompt(driver.driver_name, business_clock.now()),
                        get_checkpointer())
    try:
        out = graph.invoke(
            {"messages": _turn_input(conn, thread_id, stored.text)},
            {
                "configurable": {"thread_id": thread_id},
                "recursion_limit": MAX_GRAPH_STEPS,
                # LangSmith: one trace per turn, filterable by driver and grouped by
                # conversation. IDs only, never email or phone.
                "run_name": "driver_turn",
                "tags": ["driver-chat"],
                "metadata": {"driver_id": driver_id, "thread_id": thread_id,
                             "flagged_for_review": stored.flagged},
            },
        )
        reply = out["messages"][-1].content or FALLBACK_REPLY
        slots = list(ctx.shown_slots)
        guard = check_booking_claim(conn, driver_id, reply)
        if guard["blocked"]:
            log.warning("guardrail blocked a booking claim thread=%s driver=%s",
                        thread_id, driver_id)
            reply, slots = guard["reply"], []
    except Exception:
        log.exception("agent turn failed thread=%s driver=%s", thread_id, driver_id)
        reply, slots = FALLBACK_REPLY, []

    chat.record_agent_message(conn, thread_id, reply, wall_clock.now())
    return TurnResult(reply, slots)
