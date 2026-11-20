"""Shipments, arrival maths and coordinator contacts (plan §6.1, spec §5, R13, R20a, R24)."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from app.config import clock, parse_ts

# Spec §5: only these can be rescheduled (and so get slots shown).
RESCHEDULABLE = frozenset({"PLANNED", "ASSIGNED", "IN_TRANSIT"})
ACTIVE_APPOINTMENT = ("PENDING_CONFIRMATION", "CONFIRMED", "IN_PROGRESS")

_STATUS_WORDS = {
    "PLANNED": "planned",
    "ASSIGNED": "assigned",
    "IN_TRANSIT": "on the way",
    "AT_GATE": "at the gate",
    "WAITING": "waiting in the yard",
    "IN_DOCK": "unloading at a dock",
    "COMPLETED": "completed",
    "CANCELLED": "cancelled",
}


class NotAllowed(Exception):
    """The driver may not see slots for this shipment (R24). The message is safe to show."""


class ArrivalInPast(Exception):
    """The arrival time is earlier than the current time (R13, AC17)."""


class BadArrivalInput(ValueError):
    """The arrival input could not be understood."""


@dataclass(frozen=True)
class CurrentAppointment:
    appointment_id: str
    slot_id: str
    dock_code: str
    start: datetime
    end: datetime
    status: str


@dataclass(frozen=True)
class Shipment:
    shipment_id: str
    driver_id: str
    status: str
    destination_facility_id: str
    destination_name: str
    origin_city: str
    product_category: str
    load_weight_kg: int
    required_dock_type: str
    temperature_control_required: bool
    expected_unload_min: int
    baseline_eta: datetime          # originally planned arrival (spec §5)
    latest_eta: datetime            # v_latest_eta.effective_eta_ts
    current: CurrentAppointment | None

    @property
    def label(self) -> str:
        """How a driver recognises the delivery, without IDs (R13)."""
        return f"{self.product_category} from {self.origin_city}"

    @property
    def status_words(self) -> str:
        return _STATUS_WORDS.get(self.status, self.status.lower())

    @property
    def reschedulable(self) -> bool:
        return self.status in RESCHEDULABLE


@dataclass(frozen=True)
class DriverShipments:
    active: list[Shipment]
    most_recent_other: Shipment | None   # only filled when `active` is empty (R20a)


@dataclass(frozen=True)
class Contact:
    name: str
    phone: str


_SHIPMENT_SQL = """
SELECT s.shipment_id, s.driver_id, s.current_status, s.destination_facility_id,
       f.facility_name, s.origin_city, s.product_category, s.load_weight_kg,
       s.required_dock_type, s.temperature_control_required, s.expected_unload_min,
       s.original_eta_ts, le.effective_eta_ts,
       a.appointment_id, a.slot_id, a.appointment_status,
       d.dock_code, sl.slot_start_ts, sl.slot_end_ts
FROM shipments s
JOIN facilities f ON f.facility_id = s.destination_facility_id
JOIN v_latest_eta le ON le.shipment_id = s.shipment_id
LEFT JOIN appointments a
       ON a.shipment_id = s.shipment_id AND a.is_current = 1
      AND a.appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
LEFT JOIN appointment_slots sl ON sl.slot_id = a.slot_id
LEFT JOIN docks d ON d.dock_id = sl.dock_id
"""


def _row_to_shipment(row: sqlite3.Row) -> Shipment:
    current = None
    if row["appointment_id"]:
        current = CurrentAppointment(
            appointment_id=row["appointment_id"],
            slot_id=row["slot_id"],
            dock_code=row["dock_code"],
            start=parse_ts(row["slot_start_ts"]),
            end=parse_ts(row["slot_end_ts"]),
            status=row["appointment_status"],
        )
    return Shipment(
        shipment_id=row["shipment_id"],
        driver_id=row["driver_id"],
        status=row["current_status"],
        destination_facility_id=row["destination_facility_id"],
        destination_name=row["facility_name"],
        origin_city=row["origin_city"],
        product_category=row["product_category"],
        load_weight_kg=row["load_weight_kg"],
        required_dock_type=row["required_dock_type"],
        temperature_control_required=bool(row["temperature_control_required"]),
        expected_unload_min=row["expected_unload_min"],
        baseline_eta=parse_ts(row["original_eta_ts"]),
        latest_eta=parse_ts(row["effective_eta_ts"]),
        current=current,
    )


def load_shipment(conn: sqlite3.Connection, shipment_id: str) -> Shipment | None:
    row = conn.execute(_SHIPMENT_SQL + " WHERE s.shipment_id = ?", (shipment_id,)).fetchone()
    return _row_to_shipment(row) if row else None


def driver_shipments(conn: sqlite3.Connection, driver_id: str) -> DriverShipments:
    rows = conn.execute(
        _SHIPMENT_SQL + " WHERE s.driver_id = ? ORDER BY s.original_eta_ts DESC, s.shipment_id DESC",
        (driver_id,),
    ).fetchall()
    shipments = [_row_to_shipment(r) for r in rows]
    active = sorted((s for s in shipments if s.reschedulable), key=lambda s: s.baseline_eta)
    if active:
        return DriverShipments(active=active, most_recent_other=None)
    # Latest planned arrival first, so DRV008 gets SHP1019 (16:00) over SHP1008 (09:45). AC16.
    return DriverShipments(active=[], most_recent_other=shipments[0] if shipments else None)


def get_own_shipment(conn: sqlite3.Connection, driver_id: str, shipment_id: str) -> Shipment:
    """The shipment, if it is this driver's and can be rescheduled. Otherwise NotAllowed (R24).
    Another driver's shipment gets the same answer as a missing one, so IDs don't leak."""
    shipment = load_shipment(conn, shipment_id)
    if shipment is None or shipment.driver_id != driver_id:
        raise NotAllowed("That delivery is not one of yours.")
    if not shipment.reschedulable:
        raise NotAllowed(f"That delivery is {shipment.status_words}, so it can't be rescheduled here.")
    return shipment


_CLOCK_TIME = re.compile(r"^\s*([01]?\d|2[0-3]):([0-5]\d)\s*$")


def today_at(hhmm: str, now: datetime | None = None) -> datetime:
    """24-hour "HH:MM" as a time on the scheduling day (spec A3)."""
    now = now or clock.now()
    match = _CLOCK_TIME.match(hhmm or "")
    if not match:
        raise BadArrivalInput(f"Times must be 24-hour HH:MM, got {hhmm!r}.")
    return datetime.combine(now.date(), time(int(match.group(1)), int(match.group(2))), tzinfo=now.tzinfo)


def resolve_arrival(
    shipment: Shipment,
    *,
    clock_time: str | None = None,
    delay_minutes: int | None = None,
    now: datetime | None = None,
) -> datetime:
    """Turn what the driver said into an exact arrival time (R13). The LLM extracts the numbers;
    this function does the maths.

    clock_time:    24-hour "HH:MM", read as today in Asia/Kolkata (A3).
    delay_minutes: added to the baseline (originally planned) ETA (spec §5).
    """
    if (clock_time is None) == (delay_minutes is None):
        raise BadArrivalInput("Give exactly one of clock_time or delay_minutes.")
    now = now or clock.now()

    if clock_time is not None:
        arrival = today_at(clock_time, now)
    else:
        if not 0 < delay_minutes <= 24 * 60:
            raise BadArrivalInput("delay_minutes must be between 1 and 1440.")
        arrival = shipment.baseline_eta + timedelta(minutes=delay_minutes)

    if arrival < now:
        raise ArrivalInPast(f"{arrival:%H:%M} is earlier than now ({now:%H:%M}).")
    return arrival


def coordinator_for(conn: sqlite3.Connection, facility_id: str) -> Contact | None:
    """The facility's warehouse coordinator, for hand-offs (R13a, R16, R18)."""
    row = conn.execute(
        """SELECT contact_name, phone FROM facility_contacts
           WHERE facility_id = ? AND contact_role = 'WAREHOUSE_COORDINATOR'
             AND active_flag = 1 AND phone IS NOT NULL
           ORDER BY contact_id LIMIT 1""",
        (facility_id,),
    ).fetchone()
    return Contact(name=row["contact_name"], phone=row["phone"]) if row else None
