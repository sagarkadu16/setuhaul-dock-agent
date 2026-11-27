"""Slot feasibility: which dock slots can this shipment actually use, and why not the rest.

Pure decision logic: reads through db.queries, never writes, never calls the LLM.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from setuhaul.db import queries
from setuhaul.models.domain import DockType, Shipment, SlotAvailability, SlotView

SEARCH_HORIZON = timedelta(hours=8)


@dataclass(frozen=True)
class SlotCheck:
    slot: SlotView
    reasons: tuple[str, ...]  # empty tuple = feasible

    @property
    def feasible(self) -> bool:
        return not self.reasons


def check_slot(slot: SlotView, shipment: Shipment, earliest_start: datetime) -> SlotCheck:
    reasons: list[str] = []

    if slot.availability_status != SlotAvailability.AVAILABLE:
        reasons.append(f"slot is {slot.availability_status.lower()}")
    if slot.slot_start_ts < earliest_start:
        reasons.append("starts before the truck can arrive")
    if shipment.required_dock_type not in (DockType.ANY, slot.dock_type):
        reasons.append(
            f"needs a {shipment.required_dock_type.lower()} dock, "
            f"{slot.dock_code} is {slot.dock_type.lower()}"
        )
    if shipment.temperature_control_required and not slot.supports_refrigerated:
        reasons.append(f"load needs refrigeration, {slot.dock_code} has none")
    if shipment.load_weight_kg > slot.max_vehicle_weight_kg:
        reasons.append(
            f"load {shipment.load_weight_kg} kg exceeds {slot.dock_code} "
            f"limit {slot.max_vehicle_weight_kg} kg"
        )
    if shipment.expected_unload_min > slot.duration_min:
        reasons.append(
            f"unload needs {shipment.expected_unload_min} min, slot is {slot.duration_min} min"
        )

    return SlotCheck(slot=slot, reasons=tuple(reasons))


def check_slots(
    conn: sqlite3.Connection,
    shipment: Shipment,
    eta: datetime,
    now: datetime,
    horizon: timedelta = SEARCH_HORIZON,
) -> list[SlotCheck]:
    """Every slot from now until (earliest possible start + horizon), each with a verdict."""
    earliest_start = max(eta, now)
    candidates = queries.slots_starting_between(
        conn, shipment.destination_facility_id, now, earliest_start + horizon
    )
    return [check_slot(slot, shipment, earliest_start) for slot in candidates]


def feasible_slots(
    conn: sqlite3.Connection,
    shipment: Shipment,
    eta: datetime,
    now: datetime,
    horizon: timedelta = SEARCH_HORIZON,
) -> list[SlotView]:
    """Usable slots, earliest first (ties: dock code, from the query's ORDER BY)."""
    return [c.slot for c in check_slots(conn, shipment, eta, now, horizon) if c.feasible]
