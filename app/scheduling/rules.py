"""Slot rules S1-S7 (spec §6.5, plan §6.2). Pure functions: no database, no clock."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.scheduling.shipments import Shipment


@dataclass(frozen=True)
class SlotView:
    """One slot with its dock and whoever holds it right now."""
    slot_id: str
    facility_id: str
    dock_code: str
    dock_type: str
    dock_status: str
    max_weight_kg: int
    slot_status: str
    start: datetime
    end: datetime
    held_by_shipment: str | None   # shipment of the active appointment on this slot, if any


def required_dock_type(shipment: Shipment, heavy_kg: int | None) -> str:
    """S2: reefer for temperature control; heavy above the facility's rule or if marked heavy."""
    if shipment.temperature_control_required or shipment.required_dock_type == "REEFER":
        return "REEFER"
    if shipment.required_dock_type == "HEAVY" or (
        heavy_kg is not None and shipment.load_weight_kg > heavy_kg
    ):
        return "HEAVY"
    return "STANDARD"


def check_slot(
    shipment: Shipment,
    slot: SlotView,
    *,
    arrival: datetime,
    finish_by: datetime | None = None,
    heavy_kg: int | None = None,
) -> list[str]:
    """Return the IDs of the rules this slot fails; an empty list means the slot is valid.

    A7: an unload longer than the slot is *not* a failure.
    """
    failed = []
    if slot.facility_id != shipment.destination_facility_id:
        failed.append("S1")
    if slot.dock_type != required_dock_type(shipment, heavy_kg):
        failed.append("S2")
    if shipment.load_weight_kg > slot.max_weight_kg:
        failed.append("S3")
    if slot.slot_status != "OPEN" or slot.dock_status != "ACTIVE":
        failed.append("S4")
    if slot.held_by_shipment not in (None, shipment.shipment_id):   # own slot passes (Q2)
        failed.append("S5")
    if slot.start < arrival:
        failed.append("S6")
    if finish_by is not None and slot.start + timedelta(minutes=shipment.expected_unload_min) > finish_by:
        failed.append("S7")
    return failed


# Plain-English reasons the agent can pass on to the driver.
RULE_REASONS = {
    "S1": "that slot is at a different warehouse",
    "S2": "that dock is the wrong type for your load",
    "S3": "your load is too heavy for that dock",
    "S4": "that slot is closed",
    "S5": "that slot is already taken",
    "S6": "that slot starts before you arrive",
    "S7": "unloading would not finish in time",
}
