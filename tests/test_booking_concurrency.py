"""Integrity proof: two drivers book the same slot at the same instant. Exactly one wins."""

import threading

import pytest

from setuhaul.clock import get_clock
from setuhaul.config import get_settings
from setuhaul.db.connection import open_db
from setuhaul.db.scenario import load_scenario
from setuhaul.services.booking import SlotNotAvailable, book_slot

SCENARIO = "two_drivers_one_slot"
SLOT = "SLOT-JAI-019"
CONTENDERS = [("DRV006", "SHP1006"), ("DRV012", "SHP1012")]
ROUNDS = 20


@pytest.fixture
def scenario_db(tmp_path, monkeypatch):
    """A fresh scenario DB in a temp folder, so tests never touch data/app.db."""
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.db"))
    get_settings.cache_clear()          # re-read settings with the temp paths
    load_scenario(SCENARIO)
    yield
    get_settings.cache_clear()          # next test sees the real settings again


@pytest.mark.parametrize("round_no", range(ROUNDS))
def test_simultaneous_bookings_exactly_one_wins(scenario_db, round_no):
    now = get_clock().now()
    start = threading.Barrier(len(CONTENDERS), timeout=5)
    outcomes: list[str] = []

    def attempt(driver_id: str, shipment_id: str) -> None:
        with open_db() as conn:
            start.wait()
            try:
                book_slot(conn, driver_id, shipment_id, SLOT, now)
                outcomes.append(f"won:{shipment_id}")
            except SlotNotAvailable:
                outcomes.append(f"refused:{shipment_id}")
            except Exception as exc:
                outcomes.append(f"error: {exc!r}")

    threads = [threading.Thread(target=attempt, args=c) for c in CONTENDERS]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive(), "a booking thread hung"

    winners = [o.removeprefix("won:") for o in outcomes if o.startswith("won:")]
    refused = [o for o in outcomes if o.startswith("refused:")]
    assert len(winners) == 1 and len(refused) == 1, outcomes


    with open_db(read_only=True) as conn:
        rows = conn.execute(
            "SELECT shipment_id FROM appointments WHERE slot_id = ? AND is_current = 1 "
            "AND appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')",
            (SLOT,),
        ).fetchall()
    assert [r["shipment_id"] for r in rows] == winners
    print(f"round {round_no:2}: {winners[0]} got {SLOT}")
