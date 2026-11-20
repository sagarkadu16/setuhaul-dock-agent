"""Check the configured LLM can make a tool call (task T4.1). Costs a fraction of a cent.

Usage: .venv/bin/python scripts/check_llm.py
"""

from __future__ import annotations

import sys

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from app.agent.model import build_chat_model
from app.config import settings


@tool
def find_slots(shipment_id: str, earliest_start: str) -> str:
    """Find unloading slots for a shipment starting at or after earliest_start (24-hour HH:MM)."""
    return "[]"


def main() -> int:
    print(f"Model: {settings.llm_model} via {settings.llm_base_url}")
    model = build_chat_model().bind_tools([find_slots])
    reply = model.invoke([HumanMessage("Find slots for shipment SHP1006 from 11:20. Use the tool.")])
    if not reply.tool_calls:
        print(f"FAIL: no tool call. Reply was: {reply.content!r}")
        return 1
    call = reply.tool_calls[0]
    print(f"OK: tool call {call['name']}({call['args']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
