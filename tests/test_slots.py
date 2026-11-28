"""T2.4-T2.5: find_slots and check_pick against the real seed data."""

import pytest

from app.scheduling.shipments import ArrivalInPast, NotAllowed
from app.scheduling.slots import check_pick, find_slots
from tests.conftest import at


def summary(options):
    return [(o.dock_code, f"{o.start:%H:%M}") for o in options]


# --- T2.4: find_slots ---

def test_ac9_standard_slots_after_12_for_shp1006(ro_db):
    options = find_slots(ro_db, "DRV006", "SHP1006", earliest_start=at("12:00"))
    assert summary(options) == [
        ("D2", "12:00"), ("D4", "12:00"), ("D1", "13:00"), ("D2", "13:00"), ("D3", "13:00"),
    ]
    assert not any(o.is_current for o in options)


def test_ac10_21_tonne_load_never_gets_d1_or_d3(ro_db):
    # SHP1014: 21,000 kg; D1 and D3 take 20,000 kg max. Slots from its latest ETA (11:25).
    options = find_slots(ro_db, "DRV014", "SHP1014", earliest_start=at("11:25"))
    assert options, "expected some slots"
    assert {o.dock_code for o in options} <= {"D2", "D4"}
    assert summary(options) == [
        ("D2", "12:00"), ("D4", "12:00"), ("D2", "13:00"), ("D4", "13:00"), ("D2", "14:00"),
    ]


def test_ac11_reefer_has_no_slot_after_maintenance(ro_db, fixed_clock):
    fixed_clock.set("2026-08-04T16:25:00+05:30")
    assert find_slots(ro_db, "DRV015", "SHP1015", earliest_start=at("18:30")) == []


def test_ac18_current_slot_is_shown_and_marked(ro_db):
    options = find_slots(ro_db, "DRV001", "SHP1017", earliest_start=at("11:30"))
    assert summary(options) == [
        ("D1", "12:00"), ("D2", "12:00"), ("D4", "12:00"), ("D1", "13:00"), ("D2", "13:00"),
    ]
    assert [o.is_current for o in options] == [True, False, False, False, False]


def test_heavy_load_only_gets_heavy_dock(ro_db):
    options = find_slots(ro_db, "DRV007", "SHP1016", earliest_start=at("12:00"))
    assert {o.dock_code for o in options} == {"D6"}
    assert summary(options)[0] == ("D6", "12:30")


def test_finish_by_limits_slots(ro_db):
    # SHP1006 unloads in 45 min; finish by 13:45 allows 12:00 and 13:00 starts only.
    options = find_slots(ro_db, "DRV006", "SHP1006", earliest_start=at("12:00"), finish_by=at("13:45"))
    assert {f"{o.start:%H:%M}" for o in options} == {"12:00", "13:00"}


def test_gurugram_shipment_gets_gurugram_slots(ro_db):  # S1, A4
    options = find_slots(ro_db, "DRV006", "SHP1021", earliest_start=at("14:00"))
    assert {o.slot_id[:8] for o in options} == {"SLOT-GGN"}


def test_find_slots_refuses_other_drivers_shipment(ro_db):  # AC14
    with pytest.raises(NotAllowed):
        find_slots(ro_db, "DRV006", "SHP1014", earliest_start=at("12:00"))


def test_find_slots_refuses_yard_shipment(ro_db):  # AC14a
    with pytest.raises(NotAllowed):
        find_slots(ro_db, "DRV004", "SHP1004", earliest_start=at("12:00"))


def test_find_slots_rejects_start_in_past(ro_db):
    with pytest.raises(ArrivalInPast):
        find_slots(ro_db, "DRV006", "SHP1006", earliest_start=at("09:00"))


# --- T2.5: check_pick ---

def test_picked_slot_is_valid_and_gives_coordinator(ro_db):  # AC12, R16
    result = check_pick(ro_db, "DRV006", "SHP1006", "d2", at("12:00"), arrival=at("11:20"))
    assert result.valid
    assert (result.dock_code, result.start) == ("D2", at("12:00"))
    assert result.coordinator.phone == "+91-141-5001101"


def test_picked_slot_taken_by_someone_else(ro_db):
    # D1 12:00 is held by APT1017 (SHP1017).
    result = check_pick(ro_db, "DRV006", "SHP1006", "D1", at("12:00"), arrival=at("11:20"))
    assert not result.valid
    assert result.failed_rules == ["S5"]
    assert result.reason == "that slot is already taken"
    assert summary(result.alternatives) == [
        ("D2", "12:00"), ("D4", "12:00"), ("D1", "13:00"), ("D2", "13:00"), ("D3", "13:00"),
    ]


def test_picked_slot_too_heavy(ro_db):
    result = check_pick(ro_db, "DRV014", "SHP1014", "D1", at("13:00"), arrival=at("11:25"))
    assert not result.valid
    assert result.failed_rules == ["S3"]


def test_picked_slot_that_does_not_exist(ro_db):
    result = check_pick(ro_db, "DRV006", "SHP1006", "D2", at("12:30"), arrival=at("11:20"))
    assert not result.valid
    assert result.reason == "there is no slot on dock D2 at 12:30"
    assert result.alternatives


def test_check_pick_refuses_other_drivers_shipment(ro_db):
    with pytest.raises(NotAllowed):
        check_pick(ro_db, "DRV006", "SHP1014", "D2", at("12:00"), arrival=at("11:25"))


def test_nothing_is_written(db_path, ro_db):  # R26
    import sqlite3

    def snapshot():
        conn = sqlite3.connect(db_path)
        try:
            return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                    for t in ("shipments", "appointments", "appointment_slots", "eta_updates")}
        finally:
            conn.close()

    before = snapshot()
    find_slots(ro_db, "DRV006", "SHP1006", earliest_start=at("12:00"))
    check_pick(ro_db, "DRV006", "SHP1006", "D2", at("12:00"), arrival=at("11:20"))
    assert snapshot() == before
