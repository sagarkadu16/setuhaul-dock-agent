"""Write statements. Callers must run these inside immediate_transaction()."""

import sqlite3
from datetime import datetime
import uuid


def retire_appointment(
    conn: sqlite3.Connection, appointment_id: str, now: datetime, reason: str
) -> None:
    conn.execute(
        "UPDATE appointments SET appointment_status = 'CANCELLED', is_current = 0, "
        "cancelled_at = ?, cancellation_reason = ?, updated_at = ? "
        "WHERE appointment_id = ?",
        (now.isoformat(), reason, now.isoformat(), appointment_id),
    )


def insert_appointment(
    conn: sqlite3.Connection,
    appointment_id: str,
    shipment_id: str,
    slot_id: str,
    now: datetime,
    replaces: str | None,
) -> None:
    conn.execute(
        "INSERT INTO appointments (appointment_id, shipment_id, slot_id, appointment_status, "
        "booking_source, is_current, booked_at, confirmed_at, replaced_appointment_id, updated_at) "
        "VALUES (?, ?, ?, 'CONFIRMED', 'DRIVER_CHAT', 1, ?, ?, ?, ?)",
        (appointment_id, shipment_id, slot_id, now.isoformat(), now.isoformat(), replaces, now.isoformat()),
    )

def insert_request(
    conn: sqlite3.Connection,
    slot_id: str,
    shipment_id: str,
    driver_id: str,
    requested_at: datetime,
    decide_after: datetime,
) -> int:
    cur = conn.execute(
        "INSERT INTO slot_requests (slot_id, shipment_id, driver_id, requested_at, decide_after) "
        "VALUES (?, ?, ?, ?, ?)",
        (slot_id, shipment_id, driver_id, requested_at.isoformat(), decide_after.isoformat()),
    )
    return cur.lastrowid  # the AUTOINCREMENT request_seq


def decide_request(
    conn: sqlite3.Connection,
    request_seq: int,
    status: str,
    decided_at: datetime,
    note: str,
    *,
    appointment_id: str | None = None,
    offered_slot_id: str | None = None,
) -> None:
    conn.execute(
        "UPDATE slot_requests SET status = ?, decided_at = ?, decision_note = ?, "
        "appointment_id = ?, offered_slot_id = ? "
        "WHERE request_seq = ? AND status = 'PENDING'",
        (status, decided_at.isoformat(), note, appointment_id, offered_slot_id, request_seq),
    )


def insert_thread(
    conn: sqlite3.Connection, driver_id: str, shipment_id: str | None, now: datetime
) -> str:
    thread_id = f"THR-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO chat_threads (thread_id, driver_id, shipment_id, opened_at, "
        "thread_status, thread_intent) VALUES (?, ?, ?, ?, 'OPEN', 'UNKNOWN')",
        (thread_id, driver_id, shipment_id, now.isoformat()),
    )
    return thread_id


def insert_chat_message(
    conn: sqlite3.Connection,
    thread_id: str,
    sender_type: str,          # 'DRIVER' | 'AGENT' | 'SYSTEM'
    text: str,
    now: datetime,
    *,
    external_id: str | None = None,
    requires_review: bool = False,
) -> str | None:
    """Return the new message id, or None if this external_id was already stored."""
    message_id = f"MSG-{uuid.uuid4().hex[:12]}"
    cur = conn.execute(
        "INSERT INTO chat_messages (chat_message_id, thread_id, sender_type, message_text, "
        "message_ts, external_message_id, requires_human_review) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (thread_id, external_message_id) WHERE external_message_id IS NOT NULL "
        "DO NOTHING",
        (message_id, thread_id, sender_type, text, now.isoformat(), external_id, int(requires_review)),
    )
    return message_id if cur.rowcount == 1 else None
