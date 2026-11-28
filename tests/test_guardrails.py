"""Output guardrail: the agent may not tell a driver a slot is booked unless the DB says so."""

from datetime import timedelta

import pytest

from setuhaul.clock import get_clock
from setuhaul.config import get_settings
from setuhaul.db.connection import open_db
from setuhaul.db.scenario import load_scenario
from setuhaul.services.contention import resolve_slot, submit_request
from setuhaul.services.guardrails import (
    SAFE_PENDING_REPLY, check_booking_claim, claims_booking,
)

SLOT = "SLOT-JAI-019"
MANOJ, ARJUN = ("DRV006", "SHP1006"), ("DRV012", "SHP1012")   # HIGH vs NORMAL


@pytest.mark.parametrize("reply", [
    "Your slot at dock D2, 12:00 is booked.",
    "Done! Dock D2 at 12:00 is confirmed for you.",
    "Booked: D2 12:00.",
    "Aapka slot book ho gaya hai.",
])
def test_positive_booking_claims_are_detected(reply):
    assert claims_booking(reply)


@pytest.mark.parametrize("reply", [
    "Your request for D2 at 12:00 is pending, not booked yet.",
    "It is not confirmed until the decision arrives.",
    "I can't confirm it now. You will be told once it is booked.",
    "Here are 3 slots: D2 12:00, D1 13:00, D2 13:00. Which one do you want?",
    "Your slot isn't confirmed yet.",
])
def test_pending_or_negative_wording_is_allowed(reply):
    assert not claims_booking(reply)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.db"))
    get_settings.cache_clear()
    load_scenario("two_drivers_one_slot")
    with open_db() as c:
        yield c
    get_settings.cache_clear()


def _decide_contest(conn):
    now = get_clock().now()
    for driver, shipment in (MANOJ, ARJUN):
        submit_request(conn, driver, shipment, SLOT, business_now=now, wall_now=now)
    after = now + timedelta(seconds=get_settings().decision_window_seconds + 1)
    resolve_slot(conn, SLOT, business_now=now, wall_now=after)


def test_false_booking_claim_is_replaced_when_nothing_is_booked(conn):
    guard = check_booking_claim(conn, "DRV012", "Dock D2 at 12:00 is booked for you!")
    assert guard == {"reply": SAFE_PENDING_REPLY, "blocked": True}


def test_loser_still_cannot_be_told_booked_after_the_decision(conn):
    _decide_contest(conn)
    guard = check_booking_claim(conn, "DRV012", "Good news, D2 12:00 is confirmed.")
    assert guard["blocked"]


def test_winner_may_be_told_booked(conn):
    _decide_contest(conn)
    reply = "Dock D2 at 12:00 is confirmed for you."
    assert check_booking_claim(conn, "DRV006", reply) == {"reply": reply, "blocked": False}
