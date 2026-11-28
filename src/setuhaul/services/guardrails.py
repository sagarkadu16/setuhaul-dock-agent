"""Output guardrails: checks on what the agent says, before the driver sees it.

Input guardrails live in sanitize.py (cleaning, size limits, injection flag) and in the
session/ownership checks. This module checks the LLM's reply against the database,
because the model can be wrong even when the prompt says otherwise.
"""

import re
import sqlite3

from langsmith import traceable

from setuhaul.db import queries
from setuhaul.models.domain import RequestStatus

SAFE_PENDING_REPLY = (
    "Your slot is not booked yet. If you have requested one, the decision will appear "
    "here as a notification shortly."
)

# "booked" / "confirmed", and Hinglish "book ho gaya".
_BOOKING_WORD = re.compile(r"\b(booked|confirmed|book ho gaya|confirm ho gaya)\b", re.IGNORECASE)
# Words just before the claim that make it negative or conditional:
# "not booked", "isn't confirmed", "until it is confirmed", "once booked", "pending, not booked".
_NEGATION_BEFORE = re.compile(
    r"\b(not|never|no|isn't|is not|aren't|wasn't|until|unless|once|if|before|when|yet to be)\b"
    r"[\w\s,'-]{0,20}$",
    re.IGNORECASE,
)


def claims_booking(reply: str) -> bool:
    """True if the reply states, positively, that a slot is booked or confirmed."""
    for match in _BOOKING_WORD.finditer(reply):
        before = reply[max(0, match.start() - 30):match.start()]
        if not _NEGATION_BEFORE.search(before):
            return True
    return False


def driver_has_won_slot(conn: sqlite3.Connection, driver_id: str) -> bool:
    """True if any of the driver's open shipments has a slot request that WON."""
    for shipment in queries.open_shipments_for_driver(conn, driver_id):
        latest = queries.latest_request_for_shipment(conn, shipment.shipment_id)
        if latest is not None and latest.status == RequestStatus.WON:
            return True
    return False


@traceable(name="guardrail_booking_claim", run_type="chain", tags=["guardrail"],
           process_inputs=lambda i: {"driver_id": i["driver_id"], "reply": i["reply"]})
def check_booking_claim(conn: sqlite3.Connection, driver_id: str, reply: str) -> dict:
    """Block a reply that says 'booked' when the database has no booking for this driver.

    Returns {"reply": text to send, "blocked": bool}. Traced, so blocks show up in LangSmith.
    """
    if claims_booking(reply) and not driver_has_won_slot(conn, driver_id):
        return {"reply": SAFE_PENDING_REPLY, "blocked": True}
    return {"reply": reply, "blocked": False}
