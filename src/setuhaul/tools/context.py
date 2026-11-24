import sqlite3
from dataclasses import dataclass

from setuhaul.clock import Clock


@dataclass(frozen=True)
class ToolContext:
    """Built by the server per request from the verified session. Never from LLM output."""

    conn: sqlite3.Connection
    driver_id: str
    business_clock: Clock   # scenario time: slot rules
    wall_clock: Clock       # real UTC: decision window
