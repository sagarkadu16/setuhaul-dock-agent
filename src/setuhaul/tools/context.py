import sqlite3
from dataclasses import dataclass, field

from setuhaul.clock import Clock
from setuhaul.models.domain import SlotView


@dataclass(frozen=True)
class ToolContext:
    """Built by the server per request from the verified session. Never from LLM output."""

    conn: sqlite3.Connection
    driver_id: str
    business_clock: Clock   # scenario time: slot rules
    wall_clock: Clock       # real UTC: decision window
    # Slots the UI should offer as buttons after this turn. Tools set it from DB data,
    # so the buttons can never show a slot the LLM invented. Lives for one turn only.
    shown_slots: list[SlotView] = field(default_factory=list)
