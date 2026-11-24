"""Write statements. Callers must run these inside immediate_transaction()."""

import sqlite3
from datetime import datetime


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
