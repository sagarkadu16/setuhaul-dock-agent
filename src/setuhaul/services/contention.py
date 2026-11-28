"""Contested slots: collect requests for a short window, then let POLICY decide.

Two clocks on purpose:
- business_now: scenario time, used for slot rules (same as booking).
- wall_now: real UTC time, used only for the decision window, which must actually pass.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from langsmith import traceable

from setuhaul.config import get_settings
from setuhaul.db import queries, writes
from setuhaul.db.connection import immediate_transaction, savepoint
from setuhaul.models.domain import NotificationKind, RequestStatus, SlotRequestRecord, SlotView
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
            return  # window still open, or already decided (polled often: not traced)
        _decide(conn, slot_id, due, business_now=business_now, wall_now=wall_now)


def _without_conn(inputs: dict[str, Any]) -> dict[str, Any]:
    """Trace inputs without the DB connection or raw records (ids only, no PII)."""
    return {
        "slot_id": inputs["slot_id"],
        "requests": [
            {"request_seq": r.request_seq, "shipment_id": r.shipment_id, "driver_id": r.driver_id}
            for r in inputs["due"]
        ],
        "business_now": str(inputs["business_now"]),
    }


@traceable(name="resolve_slot", run_type="chain", tags=["slot-decision"],
           process_inputs=_without_conn)
def _decide(
    conn: sqlite3.Connection,
    slot_id: str,
    due: list[SlotRequestRecord],
    *,
    business_now: datetime,
    wall_now: datetime,
) -> dict[str, Any]:
    """Rank the due requests and give the slot to the best one that can still book it.

    Runs inside the caller's transaction. Returns a summary of the decision; it is
    only used for tracing (who won, who lost, and on which criterion).
    """
    records = {r.request_seq: r for r in due}
    ranked = rank_requests([_ranking_input(conn, r) for r in due])
    outcomes: list[dict[str, Any]] = []

    winner: SlotRequest | None = None
    for cand in ranked:
        rec = records[cand.request_seq]
        facts = {
            "shipment_id": cand.shipment_id, "driver_id": rec.driver_id,
            "priority": str(cand.priority), "at_facility": cand.at_facility,
            "eta": str(cand.eta), "request_seq": cand.request_seq,
        }
        if winner is not None:
            why = f"another truck was ranked ahead on: {deciding_criterion(winner, cand)}"
            _lose(conn, rec, RequestStatus.LOST, why, business_now, wall_now)
            outcomes.append({**facts, "status": "LOST", "reason": why})
            continue
        try:
            with savepoint(conn):
                appt = book_slot_in_tx(conn, rec.driver_id, rec.shipment_id, slot_id, business_now)
        except BookingError as exc:
            _lose(conn, rec, RequestStatus.FAILED, str(exc), business_now, wall_now)
            outcomes.append({**facts, "status": "FAILED", "reason": str(exc)})
            continue  # next in line gets the chance
        writes.decide_request(
            conn, rec.request_seq, RequestStatus.WON, wall_now, "ranked first",
            appointment_id=appt.appointment_id,
        )
        _notify(conn, rec, NotificationKind.SLOT_CONFIRMED, None, None, wall_now)
        winner = cand
        outcomes.append({**facts, "status": "WON", "appointment_id": appt.appointment_id})

    return {"slot_id": slot_id, "winner": winner.shipment_id if winner else None,
            "outcomes": outcomes}


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
    offered = options[0] if options else None
    writes.decide_request(
        conn, rec.request_seq, status, wall_now, note,
        offered_slot_id=offered.slot_id if offered else None,
    )
    _notify(conn, rec, NotificationKind.SLOT_NOT_BOOKED, note, offered, wall_now)


def _slot_text(slot: SlotView) -> str:
    """'dock D2, 12:00-13:00' in the facility's local time (the stored offset)."""
    return f"dock {slot.dock_code}, {slot.slot_start_ts:%H:%M}–{slot.slot_end_ts:%H:%M}"


def _notify(
    conn: sqlite3.Connection,
    rec: SlotRequestRecord,
    kind: NotificationKind,
    reason: str | None,
    offered: SlotView | None,
    wall_now: datetime,
) -> None:
    """Outbox row for this decision, in the caller's transaction (decision + message, or neither).

    The text is a fixed template from DB facts, never LLM output.
    """
    slot = queries.get_slot(conn, rec.slot_id)
    if kind == NotificationKind.SLOT_CONFIRMED:
        text = f"✅ Confirmed: {_slot_text(slot)} is booked for shipment {rec.shipment_id}."
    else:
        text = f"❌ Not booked: {_slot_text(slot)}. Reason: {reason}."
        text += (
            f" Next valid slot: {_slot_text(offered)}. Tap it below or reply 'yes' to request it."
            if offered else " No other slot is open right now. Reply here and I'll help."
        )
    writes.insert_notification(conn, rec.driver_id, rec.request_seq, kind, text, wall_now)

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

