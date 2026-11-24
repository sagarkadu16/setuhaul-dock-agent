"""Domain models mirroring the seed schema (only the columns we use).

Built from DB rows with Model.model_validate(dict(row)).
Timestamps are parsed from ISO-8601 text into timezone-aware datetimes, so
code compares real instants, never strings.
"""

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, PositiveInt


class _Row(BaseModel):
    # ignore: a new DB column must not break reads.  frozen: rows are snapshots.
    model_config = ConfigDict(extra="ignore", frozen=True)


# --- Codes (values copied from the seed's CHECK constraints) ----------------

class Priority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        """Higher = more important. Explicit, so ordering never depends on spelling."""
        return _PRIORITY_RANK[self]


_PRIORITY_RANK = {Priority.LOW: 0, Priority.NORMAL: 1, Priority.HIGH: 2, Priority.CRITICAL: 3}


class DockType(StrEnum):
    ANY = "ANY"  # only valid as a shipment requirement, never as a dock's type
    STANDARD = "STANDARD"
    REEFER = "REEFER"
    HEAVY = "HEAVY"


class SlotAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    BLOCKED = "BLOCKED"
    CLOSED = "CLOSED"


class AppointmentStatus(StrEnum):
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"
    REJECTED = "REJECTED"

    @property
    def is_active(self) -> bool:
        """Matches the WHERE clause of ux_active_appointment_per_slot."""
        return self in _ACTIVE_APPOINTMENT


_ACTIVE_APPOINTMENT = {
    AppointmentStatus.PENDING_CONFIRMATION,
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.IN_PROGRESS,
}


class EtaConfidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# --- Rows ---------------------------------------------------------------------

class Driver(_Row):
    driver_id: str
    driver_name: str
    phone: str
    driver_status: str


class Shipment(_Row):
    shipment_id: str
    driver_id: str
    destination_facility_id: str
    priority_code: Priority
    required_dock_type: DockType
    temperature_control_required: bool
    load_weight_kg: PositiveInt
    expected_unload_min: PositiveInt
    original_eta_ts: AwareDatetime
    current_status: str


class EtaEstimate(_Row):
    """One row of v_latest_eta: the ETA we currently believe, and how sure we are."""

    shipment_id: str
    effective_eta_ts: AwareDatetime
    eta_source: str
    eta_confidence: EtaConfidence | None = None


class SlotView(_Row):
    """One row of v_slot_availability: a dock time window plus its live status."""

    slot_id: str
    facility_id: str
    dock_code: str
    dock_type: DockType
    supports_refrigerated: bool
    max_vehicle_weight_kg: PositiveInt
    slot_start_ts: AwareDatetime
    slot_end_ts: AwareDatetime
    availability_status: SlotAvailability
    appointment_id: str | None = None
    shipment_id: str | None = None

    @property
    def duration_min(self) -> int:
        return int((self.slot_end_ts - self.slot_start_ts).total_seconds() // 60)


class Appointment(_Row):
    appointment_id: str
    shipment_id: str
    slot_id: str
    appointment_status: AppointmentStatus
    booking_source: str
    is_current: bool
    booked_at: AwareDatetime

class RequestStatus(StrEnum):
    PENDING = "PENDING"
    WON = "WON"
    LOST = "LOST"
    FAILED = "FAILED"

class SlotRequestRecord(_Row):
    """One row of slot_requests. Times are system UTC, not scenario time."""

    request_seq: int
    slot_id: str
    shipment_id: str
    driver_id: str
    requested_at: AwareDatetime
    decide_after: AwareDatetime
    status: RequestStatus
    appointment_id: str | None = None
    offered_slot_id: str | None = None
    decision_note: str | None = None
    decided_at: AwareDatetime | None = None


class ChatMessage(_Row):
    chat_message_id: str
    thread_id: str
    sender_type: str
    message_text: str
    message_ts: AwareDatetime
    requires_human_review: bool
