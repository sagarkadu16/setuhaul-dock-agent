"""Contested slots: collect requests for a short window, then let POLICY decide.

Two clocks on purpose:
- business_now: scenario time, used for slot rules (same as booking).
- wall_now: real UTC time, used only for the decision window, which must actually pass.
"""

import sqlite3
from datetime import datetime, timedelta

from setuhaul.config import get_settings
from setuhaul.db import queries, writes
from setuhaul.db.connection import immediate_transaction, savepoint
from setuhaul.models.domain import SlotRequestRecord, RequestStatus
from setuhaul.services.booking import (
    BookingError, owned_shipment, require_feasible, book_slot_in_tx
)
from setuhaul.services.slots import feasible_slots
from setuhaul.services.priority import SlotRequest, deciding_criterion, rank_requests

AT_FACILITY_STATUSES = {"AT_GATE", "WAITING"}

class AlreadyRequested(BookingError):
    pass

class NotYourRequest(BookingError):
    pass


def submit_request(
    conn: sqlite3.Connection,
    driver_id: str,
    shipment_id: str,
    slot_id: str,
    *,
    business_now: datetime,
    wall_now: datetime,
) -> SlotRequestRecord:
    window = timedelta(seconds=get_settings().decision_window_seconds)

    with immediate_transaction(conn):
        shipment = owned_shipment(conn, driver_id, shipment_id)

        pending = queries.pending_request_for_shipment(conn, shipment_id)
        if pending is not None:
            if pending.slot_id == slot_id:
                return pending  # retry of the same request
            raise AlreadyRequested(
                f"You already have a pending request for {pending.slot_id}; "
                "wait for that decision first"
            )

        require_feasible(conn, shipment, slot_id, business_now)

        decide_after = queries.open_window_for_slot(conn, slot_id, wall_now) or wall_now + window
        seq = writes.insert_request(conn, slot_id, shipment_id, driver_id, wall_now, decide_after)

    return queries.get_request(conn, seq)


def resolve_slot(
    conn: sqlite3.Connection, slot_id: str, *, business_now: datetime, wall_now: datetime
) -> None:
    """Decide every PENDING request on this slot whose window has closed.

    Safe to call any number of times, from any process: the first caller after the
    window closes decides, later callers find nothing PENDING and do nothing.
    """
    with immediate_transaction(conn):
        due = queries.due_pending_requests(conn, slot_id, wall_now)
        if not due:
            return  # window still open, or already decided
        records = {r.request_seq: r for r in due}
        ranked = rank_requests([_ranking_input(conn, r) for r in due])

        winner: SlotRequest | None = None
        for cand in ranked:
            rec = records[cand.request_seq]
            if winner is not None:
                why = f"another truck was ranked ahead on: {deciding_criterion(winner, cand)}"
                _lose(conn, rec, RequestStatus.LOST, why, business_now, wall_now)
                continue
            try:
                with savepoint(conn):
                    appt = book_slot_in_tx(conn, rec.driver_id, rec.shipment_id, slot_id, business_now)
            except BookingError as exc:
                _lose(conn, rec, RequestStatus.FAILED, str(exc), business_now, wall_now)
                continue  # next in line gets the chance
            writes.decide_request(
                conn, rec.request_seq, RequestStatus.WON, wall_now, "ranked first",
                appointment_id=appt.appointment_id,
            )
            winner = cand


def _ranking_input(conn: sqlite3.Connection, rec: SlotRequestRecord) -> SlotRequest:
    """Turn a stored request into the facts POLICY ranks on, read fresh at decision time."""
    shipment = queries.get_shipment(conn, rec.shipment_id)
    eta = queries.latest_eta(conn, rec.shipment_id)
    current = queries.current_appointment(conn, rec.shipment_id)
    current_slot = queries.get_slot(conn, current.slot_id) if current else None
    return SlotRequest(
        request_seq=rec.request_seq,
        shipment_id=rec.shipment_id,
        priority=shipment.priority_code,
        eta=eta.effective_eta_ts if eta else shipment.original_eta_ts,
        original_slot_start=current_slot.slot_start_ts if current_slot else None,
        at_facility=shipment.current_status in AT_FACILITY_STATUSES,
    )


def _lose(
    conn: sqlite3.Connection,
    rec: SlotRequestRecord,
    status: RequestStatus,
    note: str,
    business_now: datetime,
    wall_now: datetime,
) -> None:
    """Mark a request as not winning, with the next valid slot to offer (if any)."""
    shipment = queries.get_shipment(conn, rec.shipment_id)
    eta = queries.latest_eta(conn, rec.shipment_id)
    options = feasible_slots(
        conn, shipment, eta.effective_eta_ts if eta else shipment.original_eta_ts, business_now
    )
    writes.decide_request(
        conn, rec.request_seq, status, wall_now, note,
        offered_slot_id=options[0].slot_id if options else None,
    )

def check_request(
    conn: sqlite3.Connection,
    driver_id: str,
    request_seq: int,
    *,
    business_now: datetime,
    wall_now: datetime,
) -> SlotRequestRecord:
    """A driver's view of their request. Decides the window first if it is due."""
    rec = queries.get_request(conn, request_seq)
    if rec is None or rec.driver_id != driver_id:
        raise NotYourRequest(f"No request {request_seq} found for you")

    if rec.status == RequestStatus.PENDING and wall_now >= rec.decide_after:
        resolve_slot(conn, rec.slot_id, business_now=business_now, wall_now=wall_now)
        rec = queries.get_request(conn, request_seq)

    return rec

