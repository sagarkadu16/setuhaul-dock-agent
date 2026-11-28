"""Finding valid slots and checking a slot the driver picked (plan §6.3, §6.4). Read-only."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from app.config import clock, parse_ts
from app.scheduling.rules import RULE_REASONS, SlotView, check_slot
from app.scheduling.shipments import (
    ArrivalInPast,
    Contact,
    Shipment,
    coordinator_for,
    get_own_shipment,
)

MAX_RESULTS = 5


@dataclass(frozen=True)
class SlotOption:
    slot_id: str
    dock_code: str
    start: datetime
    end: datetime
    is_current: bool        # the shipment's own current slot (Q2, AC18)


@dataclass(frozen=True)
class PickResult:
    valid: bool
    dock_code: str
    start: datetime
    coordinator: Contact | None
    failed_rules: list[str] = field(default_factory=list)
    reason: str | None = None
    alternatives: list[SlotOption] = field(default_factory=list)


def _facility_slots(conn: sqlite3.Connection, facility_id: str) -> list[SlotView]:
    rows = conn.execute(
        """SELECT sl.slot_id, sl.facility_id, d.dock_code, d.dock_type, d.dock_status,
                  d.max_vehicle_weight_kg, sl.slot_status, sl.slot_start_ts, sl.slot_end_ts,
                  a.shipment_id AS held_by
           FROM appointment_slots sl
           JOIN docks d ON d.dock_id = sl.dock_id
           LEFT JOIN appointments a
                  ON a.slot_id = sl.slot_id
                 AND a.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
           WHERE sl.facility_id = ?""",
        (facility_id,),
    ).fetchall()
    return [
        SlotView(
            slot_id=r["slot_id"],
            facility_id=r["facility_id"],
            dock_code=r["dock_code"],
            dock_type=r["dock_type"],
            dock_status=r["dock_status"],
            max_weight_kg=r["max_vehicle_weight_kg"],
            slot_status=r["slot_status"],
            start=parse_ts(r["slot_start_ts"]),
            end=parse_ts(r["slot_end_ts"]),
            held_by_shipment=r["held_by"],
        )
        for r in rows
    ]


def _heavy_limit_kg(conn: sqlite3.Connection, facility_id: str) -> int | None:
    row = conn.execute(
        """SELECT rule_value FROM facility_rules
           WHERE facility_id = ? AND rule_type = 'HEAVY_DOCK_REQUIRED_KG' AND active_flag = 1""",
        (facility_id,),
    ).fetchone()
    return int(row["rule_value"]) if row else None


def _check_not_past(when: datetime, now: datetime | None) -> None:
    now = now or clock.now()
    if when < now:
        raise ArrivalInPast(f"{when:%H:%M} is earlier than now ({now:%H:%M}).")


def _valid_options(
    conn: sqlite3.Connection,
    shipment: Shipment,
    earliest_start: datetime,
    finish_by: datetime | None,
) -> list[SlotOption]:
    heavy_kg = _heavy_limit_kg(conn, shipment.destination_facility_id)
    current_slot = shipment.current.slot_id if shipment.current else None
    valid = [
        s for s in _facility_slots(conn, shipment.destination_facility_id)
        if not check_slot(shipment, s, arrival=earliest_start, finish_by=finish_by, heavy_kg=heavy_kg)
    ]
    valid.sort(key=lambda s: (s.start, s.dock_code))   # earliest first, then dock code (spec §6.5)
    return [
        SlotOption(s.slot_id, s.dock_code, s.start, s.end, is_current=s.slot_id == current_slot)
        for s in valid[:MAX_RESULTS]
    ]


def find_slots(
    conn: sqlite3.Connection,
    driver_id: str,
    shipment_id: str,
    earliest_start: datetime,
    finish_by: datetime | None = None,
    *,
    now: datetime | None = None,
) -> list[SlotOption]:
    """Up to 5 valid slots for the driver's shipment, starting at or after `earliest_start`.

    `earliest_start` is the later of the confirmed arrival and any "after 12" the driver asked for.
    Raises NotAllowed (R24) or ArrivalInPast.
    """
    shipment = get_own_shipment(conn, driver_id, shipment_id)
    _check_not_past(earliest_start, now)
    return _valid_options(conn, shipment, earliest_start, finish_by)


def check_pick(
    conn: sqlite3.Connection,
    driver_id: str,
    shipment_id: str,
    dock_code: str,
    start: datetime,
    arrival: datetime,
    finish_by: datetime | None = None,
    *,
    now: datetime | None = None,
) -> PickResult:
    """Check one slot the driver picked (R16), including one that was never shown (spec §8).
    Valid → the coordinator to call. Not valid → a plain reason and the valid alternatives."""
    shipment = get_own_shipment(conn, driver_id, shipment_id)
    _check_not_past(arrival, now)
    coordinator = coordinator_for(conn, shipment.destination_facility_id)
    dock_code = dock_code.strip().upper()

    slot = next(
        (s for s in _facility_slots(conn, shipment.destination_facility_id)
         if s.dock_code == dock_code and s.start == start),
        None,
    )
    if slot is None:
        failed, reason = [], f"there is no slot on dock {dock_code} at {start:%H:%M}"
    else:
        failed = check_slot(
            shipment, slot, arrival=arrival, finish_by=finish_by,
            heavy_kg=_heavy_limit_kg(conn, shipment.destination_facility_id),
        )
        reason = RULE_REASONS[failed[0]] if failed else None

    if slot is not None and not failed:
        return PickResult(valid=True, dock_code=dock_code, start=start, coordinator=coordinator)
    return PickResult(
        valid=False,
        dock_code=dock_code,
        start=start,
        coordinator=coordinator,
        failed_rules=failed,
        reason=reason,
        alternatives=_valid_options(conn, shipment, arrival, finish_by),
    )
