"""Priority policy: who gets a contested slot. Deterministic and explainable.

The policy is an ordered list of criteria. Requests are compared on the first
criterion; only on a tie do we look at the next one. The last criterion must be
unique per request, so the order is always total (no coin flips).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from setuhaul.models.domain import Priority


@dataclass(frozen=True)
class SlotRequest:
    request_seq: int                       # arrival order at our system (unique, from the DB)
    shipment_id: str
    priority: Priority
    eta: datetime                          # latest believed arrival
    original_slot_start: datetime | None   # the appointment they had, if any
    at_facility: bool


# (name, key) pairs. Smaller key = served first.
Criterion = tuple[str, Callable[[SlotRequest], Any]]

POLICY: list[Criterion] = [
    # TODO(human): define the ranking criteria, most important first
    ("already at the facility", lambda r: not r.at_facility),
    ("priority", lambda r: -r.priority.rank),
    ("earlier booked appointment", lambda r: (r.original_slot_start is None, r.original_slot_start or r.eta)), # either you need appointment or both has appointment so earlier appointment
    ("arrival order", lambda r: r.request_seq)
]


def rank_key(req: SlotRequest) -> tuple:
    return tuple(key(req) for _, key in POLICY)


def rank_requests(requests: list[SlotRequest]) -> list[SlotRequest]:
    """Best first."""
    return sorted(requests, key=rank_key)


def deciding_criterion(winner: SlotRequest, loser: SlotRequest) -> str:
    """Name of the first criterion that separated two requests (for explanations)."""
    for name, key in POLICY:
        if key(winner) != key(loser):
            return name
    raise ValueError("requests are identical under POLICY; last criterion must be unique")
