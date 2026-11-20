"""Conversations stored in the seed chat tables (spec R6-R9, plan §4.3, §5.2, decision D1).

The owner of a conversation is `chat_threads.driver_id`; a user is permanently linked to one
driver, so "my conversations" = "my driver's threads" (R5).
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime

from app.config import format_ts, parse_ts, wall_now

SENDERS = ("DRIVER", "AGENT")


class NotFound(Exception):
    """No such conversation for this driver. Used for other drivers' threads too, so IDs don't leak."""


@dataclass(frozen=True)
class Message:
    message_id: str
    sender: str            # DRIVER, AGENT (or OPERATIONS / WAREHOUSE / SYSTEM in seed history)
    text: str
    sent_at: datetime


@dataclass(frozen=True)
class Conversation:
    thread_id: str
    opened_at: datetime
    shipment_id: str | None
    preview: str | None    # last message, for the list


def _chat_ts() -> str:
    # Real clock (decision D2), with microseconds so a quick reply still sorts after the question.
    return wall_now().isoformat(timespec="microseconds")


def list_conversations(conn: sqlite3.Connection, driver_id: str) -> list[Conversation]:
    """The driver's conversations, newest first (R6)."""
    rows = conn.execute(
        """SELECT t.rowid AS seq, t.thread_id, t.opened_at, t.shipment_id,
                  (SELECT m.message_text FROM chat_messages m WHERE m.thread_id = t.thread_id
                   ORDER BY m.message_ts DESC, m.rowid DESC LIMIT 1) AS preview
           FROM chat_threads t WHERE t.driver_id = ?""",
        (driver_id,),
    ).fetchall()
    rows = sorted(rows, key=lambda r: (parse_ts(r["opened_at"]), r["seq"]), reverse=True)
    return [Conversation(r["thread_id"], parse_ts(r["opened_at"]), r["shipment_id"], r["preview"])
            for r in rows]


def create_conversation(conn: sqlite3.Connection, driver_id: str) -> Conversation:
    """Start a new conversation (R8)."""
    thread_id = f"THR-{uuid.uuid4().hex[:8]}"
    opened_at = _chat_ts()
    conn.execute(
        """INSERT INTO chat_threads (thread_id, driver_id, opened_at, thread_status, thread_intent)
           VALUES (?, ?, ?, 'OPEN', 'UNKNOWN')""",
        (thread_id, driver_id, opened_at),
    )
    return Conversation(thread_id, parse_ts(opened_at), None, None)


def require_owner(conn: sqlite3.Connection, thread_id: str, driver_id: str) -> None:
    """Raise NotFound unless this conversation belongs to this driver (R5)."""
    row = conn.execute(
        "SELECT 1 FROM chat_threads WHERE thread_id = ? AND driver_id = ?", (thread_id, driver_id)
    ).fetchone()
    if row is None:
        raise NotFound("Conversation not found.")


def get_messages(conn: sqlite3.Connection, thread_id: str, driver_id: str) -> list[Message]:
    """All messages of one of the driver's conversations, in order (R7)."""
    require_owner(conn, thread_id, driver_id)
    rows = conn.execute(
        """SELECT rowid AS seq, chat_message_id, sender_type, message_text, message_ts
           FROM chat_messages WHERE thread_id = ?""",
        (thread_id,),
    ).fetchall()
    rows = sorted(rows, key=lambda r: (parse_ts(r["message_ts"]), r["seq"]))
    return [Message(r["chat_message_id"], r["sender_type"], r["message_text"], parse_ts(r["message_ts"]))
            for r in rows]


def append_message(
    conn: sqlite3.Connection, thread_id: str, driver_id: str, sender: str, text: str
) -> Message:
    """Store one message (R9). Each call is its own short write, so the driver's message is saved
    before the agent runs (plan §5.2)."""
    if sender not in SENDERS:
        raise ValueError(f"sender must be one of {SENDERS}")
    require_owner(conn, thread_id, driver_id)
    message_id = f"MSG-{uuid.uuid4().hex[:8]}"
    sent_at = _chat_ts()
    conn.execute(
        """INSERT INTO chat_messages
               (chat_message_id, thread_id, sender_type, sender_reference, message_text, message_ts)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (message_id, thread_id, sender, driver_id if sender == "DRIVER" else "AGENT", text, sent_at),
    )
    return Message(message_id, sender, text, parse_ts(sent_at))


def set_shipment(conn: sqlite3.Connection, thread_id: str, driver_id: str, shipment_id: str) -> None:
    """Remember which shipment a conversation is about, once the agent knows (plan §4.3)."""
    require_owner(conn, thread_id, driver_id)
    conn.execute(
        "UPDATE chat_threads SET shipment_id = ? WHERE thread_id = ? AND driver_id = ?",
        (shipment_id, thread_id, driver_id),
    )
