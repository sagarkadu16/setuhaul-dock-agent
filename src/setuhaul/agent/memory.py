"""LangGraph checkpoint store: the agent's conversation memory, keyed by thread_id.

Separate DB from app.db so frequent checkpoint writes never hold the booking write lock.
Bookings are never stored here, only conversation state.
"""

import sqlite3
from functools import lru_cache

from langgraph.checkpoint.sqlite import SqliteSaver

from setuhaul.config import get_settings


@lru_cache
def get_checkpointer() -> SqliteSaver:
    path = get_settings().checkpoint_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL")
    saver = SqliteSaver(conn)
    saver.setup()            # creates its tables on first use, safe to call again
    return saver
