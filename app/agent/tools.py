"""Tools the model can call (plan §7.3, task T4.3). All read-only (R26).

The driver ID and database path come from the run config, never from the model's arguments, so
the model cannot act for another driver (R5, R24). `config` is hidden from the tool schema.
"""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.conversations import NotFound, set_shipment
from app.db import connect, connect_readonly
from app.scheduling.shipments import (
    ArrivalInPast,
    BadArrivalInput,
    Contact,
    NotAllowed,
    coordinator_for,
    get_own_shipment,
    resolve_arrival as _resolve_arrival,
    today_at,
)
from app.scheduling.slots import SlotOption, check_pick as _check_pick, find_slots as _find_slots

_EXPECTED = (NotAllowed, ArrivalInPast, BadArrivalInput)


def _ctx(config: RunnableConfig) -> tuple[str, str | None, str | None]:
    conf = config.get("configurable", {})
    return conf["driver_id"], conf.get("db_path"), conf.get("thread_id")


def _hhmm(dt: datetime) -> str:
    return f"{dt:%H:%M}"


def _slot(option: SlotOption, number: int) -> dict:
    return {
        "number": number,
        "dock": option.dock_code,
        "start": _hhmm(option.start),
        "end": _hhmm(option.end),
        "your_current_slot": option.is_current,
    }


def _contact(contact: Contact | None) -> dict | None:
    return {"name": contact.name, "phone": contact.phone} if contact else None


def _json(data: dict) -> str:
    return json.dumps(data)


@tool
def resolve_arrival(
    shipment_id: str,
    config: RunnableConfig,
    clock_time: str | None = None,
    delay_minutes: int | None = None,
) -> str:
    """Work out the driver's exact arrival time. Give exactly one of:
    clock_time: the time they said, as 24-hour HH:MM (e.g. "1 pm" -> "13:00");
    delay_minutes: how late they said they are (e.g. "late by one hour" -> 60).
    Returns arrival_time as HH:MM, or an error to explain to the driver."""
    driver_id, db_path, _ = _ctx(config)
    try:
        with closing(connect_readonly(db_path)) as conn:
            shipment = get_own_shipment(conn, driver_id, shipment_id)
            arrival = _resolve_arrival(shipment, clock_time=clock_time, delay_minutes=delay_minutes)
    except _EXPECTED as exc:
        return _json({"error": str(exc)})
    return _json({"arrival_time": _hhmm(arrival), "needs_confirmation": delay_minutes is not None})


@tool
def find_slots(
    shipment_id: str,
    earliest_start: str,
    config: RunnableConfig,
    finish_by: str | None = None,
) -> str:
    """Find up to 5 valid unloading slots for the shipment, earliest first.
    earliest_start: 24-hour HH:MM - the arrival time, or a later time the driver asked for.
    finish_by: optional 24-hour HH:MM by which unloading must be finished."""
    driver_id, db_path, thread_id = _ctx(config)
    try:
        with closing(connect_readonly(db_path)) as conn:
            options = _find_slots(
                conn, driver_id, shipment_id,
                earliest_start=today_at(earliest_start),
                finish_by=today_at(finish_by) if finish_by else None,
            )
            shipment = get_own_shipment(conn, driver_id, shipment_id)
            contact = coordinator_for(conn, shipment.destination_facility_id)
    except _EXPECTED as exc:
        return _json({"error": str(exc)})

    _remember_shipment(db_path, thread_id, driver_id, shipment_id)
    return _json({
        "slots": [_slot(o, i) for i, o in enumerate(options, start=1)],
        "coordinator": _contact(contact),
        "note": "Slots are available now but NOT booked." if options else "No valid slot is available.",
    })


@tool
def check_pick(
    shipment_id: str,
    dock_code: str,
    start_time: str,
    arrival_time: str,
    config: RunnableConfig,
    finish_by: str | None = None,
) -> str:
    """Check the slot the driver picked. dock_code like "D2"; start_time, arrival_time and
    finish_by as 24-hour HH:MM. Returns whether it is valid now, the coordinator to call to
    confirm it, or the reason it is not valid plus alternatives."""
    driver_id, db_path, _ = _ctx(config)
    try:
        with closing(connect_readonly(db_path)) as conn:
            result = _check_pick(
                conn, driver_id, shipment_id, dock_code,
                start=today_at(start_time),
                arrival=today_at(arrival_time),
                finish_by=today_at(finish_by) if finish_by else None,
            )
    except _EXPECTED as exc:
        return _json({"error": str(exc)})

    data = {
        "valid": result.valid,
        "dock": result.dock_code,
        "start": _hhmm(result.start),
        "coordinator": _contact(result.coordinator),
    }
    if result.valid:
        data["note"] = "Available now but NOT booked. The driver must call the coordinator to confirm."
    else:
        data["reason"] = result.reason
        data["alternatives"] = [_slot(o, i) for i, o in enumerate(result.alternatives, start=1)]
    return _json(data)


def _remember_shipment(db_path: str | None, thread_id: str | None, driver_id: str, shipment_id: str) -> None:
    """Note which shipment the conversation is about (plan §4.3). Chat data only, never operational."""
    if not thread_id:
        return
    try:
        with closing(connect(db_path)) as conn:
            set_shipment(conn, thread_id, driver_id, shipment_id)
    except NotFound:
        pass


TOOLS = [resolve_arrival, find_slots, check_pick]
