"""Tools the driver's agent may call. Each returns a JSON-safe dict for the LLM."""

from typing import Any

from setuhaul.db import queries
from setuhaul.services.booking import BookingError
from setuhaul.services.shipments import ShipmentChoice, choose_shipment
from setuhaul.services.slots import feasible_slots
from setuhaul.tools.context import ToolContext
from setuhaul.models.domain import RequestStatus
from setuhaul.services.contention import check_request, submit_request


MAX_OPTIONS = 3


def _ask_which(choice: ShipmentChoice) -> dict[str, Any]:
    if not choice.options:
        return {"error": "You have no open shipments."}
    return {
        "needs_choice": True,
        "message": "Ask the driver which shipment this is about.",
        "options": [s.shipment_id for s in choice.options],
    }


def my_shipment_status(ctx: ToolContext, shipment_id: str | None = None) -> dict[str, Any]:
    try:
        choice = choose_shipment(ctx.conn, ctx.driver_id, shipment_id)
    except BookingError as e:
        return {"error": str(e)}
    if choice.shipment is None:
        return _ask_which(choice)

    s = choice.shipment
    eta = queries.latest_eta(ctx.conn, s.shipment_id)
    appt = queries.current_appointment(ctx.conn, s.shipment_id)
    slot = queries.get_slot(ctx.conn, appt.slot_id) if appt else None
    return {
        "shipment_id": s.shipment_id,
        "priority": s.priority_code.value,
        "status": s.current_status,
        "eta": (eta.effective_eta_ts if eta else s.original_eta_ts).isoformat(),
        "current_slot": None if slot is None else {
            "slot_id": slot.slot_id, "dock": slot.dock_code,
            "start": slot.slot_start_ts.isoformat(), "end": slot.slot_end_ts.isoformat(),
        },
    }


def slot_options(ctx: ToolContext, shipment_id: str | None = None) -> dict[str, Any]:
    try:
        choice = choose_shipment(ctx.conn, ctx.driver_id, shipment_id)
    except BookingError as e:
        return {"error": str(e)}
    if choice.shipment is None:
        return _ask_which(choice)

    s = choice.shipment
    eta = queries.latest_eta(ctx.conn, s.shipment_id)
    eta_ts = eta.effective_eta_ts if eta else s.original_eta_ts
    slots = feasible_slots(ctx.conn, s, eta_ts, ctx.business_clock.now())[:MAX_OPTIONS]
    return {
        "shipment_id": s.shipment_id,
        "options": [
            {"slot_id": v.slot_id, "dock": v.dock_code,
             "start": v.slot_start_ts.isoformat(), "end": v.slot_end_ts.isoformat()}
            for v in slots
        ],
    }

def _slot_brief(ctx: ToolContext, slot_id: str | None) -> dict[str, Any] | None:
    slot = queries.get_slot(ctx.conn, slot_id) if slot_id else None
    return None if slot is None else {
        "slot_id": slot.slot_id, "dock": slot.dock_code,
        "start": slot.slot_start_ts.isoformat(), "end": slot.slot_end_ts.isoformat(),
    }


def request_slot(
    ctx: ToolContext, slot_id: str, shipment_id: str | None = None
) -> dict[str, Any]:
    try:
        choice = choose_shipment(ctx.conn, ctx.driver_id, shipment_id)
        if choice.shipment is None:
            return _ask_which(choice)
        rec = submit_request(
            ctx.conn, ctx.driver_id, choice.shipment.shipment_id, slot_id,
            business_now=ctx.business_clock.now(), wall_now=ctx.wall_clock.now(),
        )
    except BookingError as e:
        return {"error": str(e)}
    return {
        "status": rec.status.value,
        "slot": _slot_brief(ctx, rec.slot_id),
        "message": "Request received. Other trucks may want this slot. "
                   "Tell the driver the decision comes in a few seconds, then call check_my_request.",
    }


def check_my_request(ctx: ToolContext, shipment_id: str | None = None) -> dict[str, Any]:
    try:
        choice = choose_shipment(ctx.conn, ctx.driver_id, shipment_id)
        if choice.shipment is None:
            return _ask_which(choice)
        latest = queries.latest_request_for_shipment(ctx.conn, choice.shipment.shipment_id)
        if latest is None:
            return {"error": "No slot request found. Offer slot options first."}
        rec = check_request(
            ctx.conn, ctx.driver_id, latest.request_seq,
            business_now=ctx.business_clock.now(), wall_now=ctx.wall_clock.now(),
        )
    except BookingError as e:
        return {"error": str(e)}

    result: dict[str, Any] = {"status": rec.status.value, "slot": _slot_brief(ctx, rec.slot_id)}
    if rec.status == RequestStatus.PENDING:
        result["message"] = "Still deciding. Ask the driver to check again in a few seconds."
    elif rec.status == RequestStatus.WON:
        result["message"] = "Booked. Confirm the slot to the driver."
    else:
        result["reason"] = rec.decision_note
        result["offered_slot"] = _slot_brief(ctx, rec.offered_slot_id)
        result["message"] = "Not booked. Explain the reason and offer the offered_slot."
    return result
