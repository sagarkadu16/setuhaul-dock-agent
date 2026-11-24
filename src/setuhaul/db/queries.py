"""Read-only queries: SQL in, domain models out.

Rules for this module:
- Every function takes an open connection (the caller owns transactions).
- Parameters are always bound with '?', never formatted into SQL.
- No business decisions here (no "is this slot feasible?"). That is services/.
"""

import sqlite3
from datetime import datetime

from setuhaul.models.domain import Appointment, Driver, EtaEstimate, Shipment, SlotView, SlotRequestRecord, RequestStatus, ChatMessage

# Shipment statuses that can still need a dock.
_OPEN_SHIPMENT_STATUSES = ("PLANNED", "ASSIGNED", "IN_TRANSIT", "AT_GATE", "WAITING")


def get_driver(conn: sqlite3.Connection, driver_id: str) -> Driver | None:
    row = conn.execute("SELECT * FROM drivers WHERE driver_id = ?", (driver_id,)).fetchone()
    return Driver.model_validate(dict(row)) if row else None


def get_shipment(conn: sqlite3.Connection, shipment_id: str) -> Shipment | None:
    row = conn.execute("SELECT * FROM shipments WHERE shipment_id = ?", (shipment_id,)).fetchone()
    return Shipment.model_validate(dict(row)) if row else None


def open_shipments_for_driver(conn: sqlite3.Connection, driver_id: str) -> list[Shipment]:
    """All shipments this driver may be talking about. More than one = ask which."""
    placeholders = ",".join("?" * len(_OPEN_SHIPMENT_STATUSES))
    rows = conn.execute(
        f"SELECT * FROM shipments WHERE driver_id = ? AND current_status IN ({placeholders}) "
        "ORDER BY original_eta_ts",
        (driver_id, *_OPEN_SHIPMENT_STATUSES),
    ).fetchall()
    return [Shipment.model_validate(dict(r)) for r in rows]


def latest_eta(conn: sqlite3.Connection, shipment_id: str) -> EtaEstimate | None:
    row = conn.execute(
        "SELECT * FROM v_latest_eta WHERE shipment_id = ?", (shipment_id,)
    ).fetchone()
    return EtaEstimate.model_validate(dict(row)) if row else None


def current_appointment(conn: sqlite3.Connection, shipment_id: str) -> Appointment | None:
    """The shipment's one active appointment (guaranteed unique by a DB index)."""
    row = conn.execute(
        "SELECT * FROM appointments WHERE shipment_id = ? AND is_current = 1 "
        "AND appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')",
        (shipment_id,),
    ).fetchone()
    return Appointment.model_validate(dict(row)) if row else None


def get_slot(conn: sqlite3.Connection, slot_id: str) -> SlotView | None:
    row = conn.execute(
        "SELECT * FROM v_slot_availability WHERE slot_id = ?", (slot_id,)
    ).fetchone()
    return SlotView.model_validate(dict(row)) if row else None


def slots_starting_between(
    conn: sqlite3.Connection,
    facility_id: str,
    start_from: datetime,
    start_before: datetime,
) -> list[SlotView]:
    """Every slot (any status) at a facility whose start falls in [start_from, start_before).

    julianday() converts each '+05:30' timestamp to an absolute instant, so this is
    correct even when the bounds come from a UTC clock. (Plain string comparison
    would silently break the moment two timestamps carry different offsets.)
    """
    rows = conn.execute(
        "SELECT * FROM v_slot_availability "
        "WHERE facility_id = ? "
        "  AND julianday(slot_start_ts) >= julianday(?) "
        "  AND julianday(slot_start_ts) <  julianday(?) "
        "ORDER BY julianday(slot_start_ts), dock_code",
        (facility_id, start_from.isoformat(), start_before.isoformat()),
    ).fetchall()
    return [SlotView.model_validate(dict(r)) for r in rows]

def get_request(conn: sqlite3.Connection, request_seq: int) -> SlotRequestRecord | None:
    row = conn.execute(
        "SELECT * FROM slot_requests WHERE request_seq = ?", (request_seq,)
    ).fetchone()
    return SlotRequestRecord.model_validate(dict(row)) if row else None


def pending_request_for_shipment(
    conn: sqlite3.Connection, shipment_id: str
) -> SlotRequestRecord | None:
    row = conn.execute(
        "SELECT * FROM slot_requests WHERE shipment_id = ? AND status = 'PENDING'",
        (shipment_id,),
    ).fetchone()
    return SlotRequestRecord.model_validate(dict(row)) if row else None


def open_window_for_slot(
    conn: sqlite3.Connection, slot_id: str, wall_now: datetime
) -> datetime | None:
    """decide_after of a still-open window on this slot, if one exists."""
    row = conn.execute(
        "SELECT decide_after FROM slot_requests "
        "WHERE slot_id = ? AND status = 'PENDING' AND julianday(decide_after) > julianday(?) "
        "ORDER BY request_seq LIMIT 1",
        (slot_id, wall_now.isoformat()),
    ).fetchone()
    return datetime.fromisoformat(row["decide_after"]) if row else None

def due_pending_requests(
    conn: sqlite3.Connection, slot_id: str, wall_now: datetime
) -> list[SlotRequestRecord]:
    """PENDING requests on this slot whose window has closed, in arrival order."""
    rows = conn.execute(
        "SELECT * FROM slot_requests WHERE slot_id = ? AND status = 'PENDING' "
        "AND julianday(decide_after) <= julianday(?) ORDER BY request_seq",
        (slot_id, wall_now.isoformat()),
    ).fetchall()
    return [SlotRequestRecord.model_validate(dict(r)) for r in rows]


def driver_for_email(conn: sqlite3.Connection, email: str) -> Driver | None:
    row = conn.execute(
        "SELECT d.* FROM app_users u JOIN drivers d ON d.driver_id = u.driver_id "
        "WHERE u.email = ?",
        (email.strip(),),
    ).fetchone()
    return Driver.model_validate(dict(row)) if row else None


def thread_owner(conn: sqlite3.Connection, thread_id: str) -> str | None:
    """driver_id that owns this thread, or None if no such thread."""
    row = conn.execute(
        "SELECT driver_id FROM chat_threads WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    return row["driver_id"] if row else None


def messages_for_thread(
    conn: sqlite3.Connection, thread_id: str, limit: int = 50
) -> list[ChatMessage]:
    """The last `limit` messages, oldest first (chat display order)."""
    rows = conn.execute(
        "SELECT * FROM ("
        "  SELECT rowid AS _pos, * FROM chat_messages WHERE thread_id = ? "
        "  ORDER BY rowid DESC LIMIT ?"
        ") ORDER BY _pos",
        (thread_id, limit),
    ).fetchall()
    return [ChatMessage.model_validate(dict(r)) for r in rows]

def latest_request_for_shipment(
    conn: sqlite3.Connection, shipment_id: str
) -> SlotRequestRecord | None:
    row = conn.execute(
        "SELECT * FROM slot_requests WHERE shipment_id = ? ORDER BY request_seq DESC LIMIT 1",
        (shipment_id,),
    ).fetchone()
    return SlotRequestRecord.model_validate(dict(row)) if row else None


def last_agent_message(conn: sqlite3.Connection, thread_id: str) -> str | None:
    row = conn.execute(
        "SELECT message_text FROM chat_messages WHERE thread_id = ? AND sender_type = 'AGENT' "
        "ORDER BY rowid DESC LIMIT 1",
        (thread_id,),
    ).fetchone()
    return row["message_text"] if row else None
