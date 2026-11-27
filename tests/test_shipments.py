"""T2.0-T2.2: read-only connection, shipment queries, arrival maths, coordinator."""

import sqlite3

import pytest

from app.scheduling.shipments import (
    ArrivalInPast,
    BadArrivalInput,
    NotAllowed,
    coordinator_for,
    driver_shipments,
    get_own_shipment,
    load_shipment,
    resolve_arrival,
)
from tests.conftest import at


# --- T2.0: read-only by construction (R26) ---

def test_readonly_connection_cannot_write(ro_db):
    with pytest.raises(sqlite3.OperationalError):
        ro_db.execute("UPDATE appointments SET appointment_status = 'CANCELLED'")


# --- T2.1: which shipments a driver has ---

def ids(shipments):
    return [s.shipment_id for s in shipments]


def test_drv007_has_two_active_shipments(ro_db):  # AC7 setup
    result = driver_shipments(ro_db, "DRV007")
    assert ids(result.active) == ["SHP1007", "SHP1016"]
    assert [s.label for s in result.active] == [
        "Textiles from Ajmer",
        "Construction equipment from Mumbai",
    ]


def test_drv004_yard_shipment_is_not_active(ro_db):
    # SHP1004 is WAITING in the yard, so only SHP1020 counts (spec §5).
    assert ids(driver_shipments(ro_db, "DRV004").active) == ["SHP1020"]


def test_drv006_has_jaipur_and_gurugram_shipments(ro_db):  # AC12 asks "which shipment?"
    result = driver_shipments(ro_db, "DRV006")
    assert ids(result.active) == ["SHP1006", "SHP1021"]
    assert [s.destination_facility_id for s in result.active] == ["FAC-JAI-01", "FAC-GGN-01"]


def test_drv008_has_no_active_shipment_so_most_recent_is_shown(ro_db):  # AC16, R20a
    result = driver_shipments(ro_db, "DRV008")
    assert result.active == []
    assert result.most_recent_other.shipment_id == "SHP1019"
    assert result.most_recent_other.status_words == "cancelled"


def test_shipment_facts_and_current_appointment(ro_db):
    s = load_shipment(ro_db, "SHP1006")
    assert s.baseline_eta == at("10:20")
    assert s.latest_eta == at("11:20")
    assert s.current.appointment_id == "APT1006"
    assert (s.current.dock_code, s.current.start) == ("D4", at("10:00"))


def test_cannot_see_slots_for_another_drivers_shipment(ro_db):  # AC14
    with pytest.raises(NotAllowed, match="not one of yours"):
        get_own_shipment(ro_db, "DRV006", "SHP1014")


def test_cannot_see_slots_for_a_shipment_in_the_yard(ro_db):  # AC14a
    with pytest.raises(NotAllowed, match="waiting in the yard"):
        get_own_shipment(ro_db, "DRV004", "SHP1004")


def test_missing_shipment_looks_the_same_as_someone_elses(ro_db):
    with pytest.raises(NotAllowed, match="not one of yours"):
        get_own_shipment(ro_db, "DRV006", "SHP9999")


def test_jaipur_coordinator(ro_db):
    contact = coordinator_for(ro_db, "FAC-JAI-01")
    assert (contact.name, contact.phone) == ("Neha Bansal", "+91-141-5001101")


# --- T2.2: arrival maths (code does it, not the LLM) ---

def test_late_by_one_hour_adds_to_planned_time(ro_db):  # AC8
    shipment = load_shipment(ro_db, "SHP1013")
    assert shipment.baseline_eta == at("10:00")
    assert resolve_arrival(shipment, delay_minutes=60) == at("11:00")


def test_clock_time_is_today(ro_db):
    shipment = load_shipment(ro_db, "SHP1006")
    assert resolve_arrival(shipment, clock_time="11:20") == at("11:20")


def test_arrival_in_the_past_is_rejected(ro_db):  # AC17, clock 09:40
    shipment = load_shipment(ro_db, "SHP1006")
    with pytest.raises(ArrivalInPast):
        resolve_arrival(shipment, clock_time="9:00")


def test_arrival_follows_the_clock(ro_db, fixed_clock):
    fixed_clock.set("2026-08-04T16:25:00+05:30")
    shipment = load_shipment(ro_db, "SHP1015")
    assert resolve_arrival(shipment, clock_time="18:30") == at("18:30")
    with pytest.raises(ArrivalInPast):
        resolve_arrival(shipment, clock_time="16:00")


@pytest.mark.parametrize("kwargs", [
    {},                                              # neither
    {"clock_time": "11:20", "delay_minutes": 30},    # both
    {"clock_time": "1 pm"},                          # not HH:MM
    {"clock_time": "25:00"},
    {"delay_minutes": 0},
    {"delay_minutes": -15},
])
def test_bad_arrival_input(ro_db, kwargs):
    with pytest.raises(BadArrivalInput):
        resolve_arrival(load_shipment(ro_db, "SHP1006"), **kwargs)
