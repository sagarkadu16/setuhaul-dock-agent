"""Run one eval case through the real agent (run_turn), on a fresh copy of the scenario DB.

Each case gets its own temporary databases, so cases never see each other's requests or
chat memory. Runs one case at a time (settings and the checkpointer are process-wide).
"""

import os
import tempfile
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from setuhaul.agent.memory import get_checkpointer
from setuhaul.agent.runner import run_turn
from setuhaul.clock import SystemClock, get_clock
from setuhaul.config import get_settings
from setuhaul.db.connection import open_db
from setuhaul.db.scenario import load_scenario
from setuhaul.services import chat
from setuhaul.services.guardrails import SAFE_PENDING_REPLY


_DB_VARS = ("APP_DB_PATH", "CHECKPOINT_DB_PATH")


def _fresh_databases(workdir: Path, scenario: str) -> None:
    os.environ["APP_DB_PATH"] = str(workdir / "app.db")
    os.environ["CHECKPOINT_DB_PATH"] = str(workdir / "checkpoints.db")
    get_settings.cache_clear()
    get_checkpointer.cache_clear()
    load_scenario(scenario)


def _tool_calls(thread_id: str) -> list[dict[str, Any]]:
    """Every tool call the LLM made in this conversation, read from the agent's memory."""
    saved = get_checkpointer().get({"configurable": {"thread_id": thread_id}})
    messages = (saved or {}).get("channel_values", {}).get("messages", [])
    return [
        {"name": call["name"], "args": call["args"]}
        for m in messages if isinstance(m, AIMessage)
        for call in m.tool_calls
    ]


def run_case(inputs: dict[str, Any]) -> dict[str, Any]:
    """LangSmith target: inputs = {driver_id, turns, scenario}. Returns what the driver saw."""
    wall_clock = SystemClock()
    saved_env = {k: os.environ.get(k) for k in _DB_VARS}
    try:
        return _run(inputs, wall_clock)
    finally:   # put the real database paths back, even if the case crashed
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
        get_checkpointer.cache_clear()


def _run(inputs: dict[str, Any], wall_clock: SystemClock) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="setuhaul-eval-") as tmp:
        _fresh_databases(Path(tmp), inputs["scenario"])
        with open_db() as conn:
            thread_id = chat.start_thread(conn, inputs["driver_id"], wall_clock.now())
            replies = []
            for n, text in enumerate(inputs["turns"]):
                turn = run_turn(
                    conn, inputs["driver_id"], thread_id, text, external_id=f"eval-{n}",
                    business_clock=get_clock(), wall_clock=wall_clock,
                )
                replies.append(turn.reply)
            tool_calls = _tool_calls(thread_id)
        get_checkpointer().conn.close()
        get_checkpointer.cache_clear()
    return {
        "replies": replies,
        "final_reply": replies[-1],
        "tool_calls": tool_calls,
        "guardrail_blocked": SAFE_PENDING_REPLY in replies,
    }
