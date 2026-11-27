"""Chat history: one thread per login. Every driver message is cleaned before storage."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from setuhaul.db.connection import immediate_transaction
from setuhaul.db.queries import messages_for_thread, thread_owner
from setuhaul.db.writes import insert_chat_message, insert_thread
from setuhaul.models.domain import ChatMessage
from setuhaul.services.sanitize import clean_message, needs_review


class NotYourThread(Exception):
    """Thread missing or owned by another driver. Same error for both (no probing)."""


@dataclass(frozen=True)
class StoredMessage:
    text: str              # the cleaned text, which is what the agent must see
    flagged: bool
    is_retry: bool         # True = we already had this external_id, do not run the agent again


def start_thread(conn: sqlite3.Connection, driver_id: str, now: datetime) -> str:
    with immediate_transaction(conn):
        return insert_thread(conn, driver_id, None, now)


def _require_owner(conn: sqlite3.Connection, driver_id: str, thread_id: str) -> None:
    if thread_owner(conn, thread_id) != driver_id:
        raise NotYourThread("Chat not found")


def record_driver_message(
    conn: sqlite3.Connection, driver_id: str, thread_id: str, raw_text: str,
    external_id: str, now: datetime,
) -> StoredMessage:
    text = clean_message(raw_text)          # raises InvalidMessage, before any DB work
    flagged = needs_review(text)
    with immediate_transaction(conn):
        _require_owner(conn, driver_id, thread_id)
        new_id = insert_chat_message(
            conn, thread_id, "DRIVER", text, now,
            external_id=external_id, requires_review=flagged,
        )
    return StoredMessage(text=text, flagged=flagged, is_retry=new_id is None)


def record_agent_message(
    conn: sqlite3.Connection, thread_id: str, text: str, now: datetime
) -> None:
    with immediate_transaction(conn):
        insert_chat_message(conn, thread_id, "AGENT", text, now)


def history(
    conn: sqlite3.Connection, driver_id: str, thread_id: str, limit: int = 50
) -> list[ChatMessage]:
    _require_owner(conn, driver_id, thread_id)
    return messages_for_thread(conn, thread_id, limit)
