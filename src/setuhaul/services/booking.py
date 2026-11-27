"""Booking: the only code path that commits an appointment.

Inside one BEGIN IMMEDIATE transaction we re-read everything, re-run the same
feasibility rules the driver was shown, retire the old appointment and insert
the new one. The unique index ux_active_appointment_per_slot is the backstop.
"""

import sqlite3
import uuid
from datetime import datetime

from setuhaul.db import queries, writes
from setuhaul.db.connection import immediate_transaction
from setuhaul.models.domain import Appointment, Shipment, SlotView
from setuhaul.services.slots import check_slot


class BookingError(Exception):
    """Booking refused. str(error) is safe to show to the driver."""


class NotYourShipment(BookingError):
    pass


class SlotNotAvailable(BookingError):
    def __init__(self, slot_id: str, reasons: tuple[str, ...]) -> None:
        self.slot_id = slot_id
        self.reasons = reasons
        super().__init__(f"{slot_id} can't be booked: {'; '.join(reasons)}")


def owned_shipment(conn: sqlite3.Connection, driver_id: str, shipment_id: str) -> Shipment:
    shipment = queries.get_shipment(conn, shipment_id)
    if shipment is None or shipment.driver_id != driver_id:
        raise NotYourShipment(f"Shipment {shipment_id} is not assigned to you")
    return shipment


def require_feasible(
    conn: sqlite3.Connection, shipment: Shipment, slot_id: str, now: datetime
) -> SlotView:
    """Raise SlotNotAvailable unless this shipment could take this slot right now."""
    slot = queries.get_slot(conn, slot_id)
    if slot is None:
        raise SlotNotAvailable(slot_id, ("no such slot",))
    eta = queries.latest_eta(conn, shipment.shipment_id)
    earliest_start = max(eta.effective_eta_ts, now) if eta else now
    verdict = check_slot(slot, shipment, earliest_start)
    if not verdict.feasible:
        raise SlotNotAvailable(slot_id, verdict.reasons)
    return slot


def book_slot_in_tx(
    conn: sqlite3.Connection, driver_id: str, shipment_id: str, slot_id: str, now: datetime
) -> Appointment:
    """Booking rules + writes. The caller must already hold immediate_transaction(conn)."""
    if not conn.in_transaction:
        raise RuntimeError("book_slot_in_tx() must run inside immediate_transaction()")

    shipment = owned_shipment(conn, driver_id, shipment_id)
    current = queries.current_appointment(conn, shipment_id)
    if current is not None and current.slot_id == slot_id:
        return current  # already booked: a retry, not a new request

    require_feasible(conn, shipment, slot_id, now)

    if current is not None:
        writes.retire_appointment(
            conn, current.appointment_id, now, f"Rebooked to {slot_id} via driver chat"
        )
    try:
        writes.insert_appointment(
            conn, f"APT-{uuid.uuid4().hex[:10].upper()}", shipment_id, slot_id, now,
            replaces=current.appointment_id if current else None,
        )
    except sqlite3.IntegrityError as exc:
        # Backstop: the unique index caught a double booking our re-check missed.
        raise SlotNotAvailable(slot_id, ("just taken by another booking",)) from exc

    booked = queries.current_appointment(conn, shipment_id)
    assert booked is not None
    return booked


def book_slot(
    conn: sqlite3.Connection, driver_id: str, shipment_id: str, slot_id: str, now: datetime
) -> Appointment:
    """Book in its own transaction. Use this unless you are already inside one."""
    with immediate_transaction(conn):
        return book_slot_in_tx(conn, driver_id, shipment_id, slot_id, now)
