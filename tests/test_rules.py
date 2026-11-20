"""T2.3: each slot rule S1-S7 on its own, using hand-made shipments and slots."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.scheduling.rules import SlotView, check_slot, required_dock_type
from app.scheduling.shipments import Shipment
from tests.conftest import at

SHIPMENT = Shipment(
    shipment_id="SHP-T", driver_id="DRV-T", status="IN_TRANSIT",
    destination_facility_id="FAC-JAI-01", destination_name="Jaipur DC",
    origin_city="Ajmer", product_category="Textiles",
    load_weight_kg=10_000, required_dock_type="STANDARD", temperature_control_required=False,
    expected_unload_min=45, baseline_eta=at("10:00"), latest_eta=at("11:00"), current=None,
)
SLOT = SlotView(
    slot_id="SLOT-T", facility_id="FAC-JAI-01", dock_code="D2", dock_type="STANDARD",
    dock_status="ACTIVE", max_weight_kg=25_000, slot_status="OPEN",
    start=at("12:00"), end=at("13:00"), held_by_shipment=None,
)
HEAVY_KG = 25_000


def check(shipment=SHIPMENT, slot=SLOT, arrival=at("11:00"), finish_by=None):
    return check_slot(shipment, slot, arrival=arrival, finish_by=finish_by, heavy_kg=HEAVY_KG)


def test_valid_slot_passes_every_rule():
    assert check() == []


def test_s1_other_facility():
    assert check(slot=replace(SLOT, facility_id="FAC-GGN-01")) == ["S1"]


@pytest.mark.parametrize("shipment_changes, needed", [
    ({}, "STANDARD"),
    ({"temperature_control_required": True}, "REEFER"),
    ({"required_dock_type": "REEFER"}, "REEFER"),
    ({"required_dock_type": "HEAVY"}, "HEAVY"),
    ({"load_weight_kg": 25_001}, "HEAVY"),       # above the facility rule
    ({"load_weight_kg": 25_000}, "STANDARD"),    # exactly at the rule is not "above"
])
def test_s2_required_dock_type(shipment_changes, needed):
    assert required_dock_type(replace(SHIPMENT, **shipment_changes), HEAVY_KG) == needed


def test_s2_heavy_rule_missing_means_standard():
    assert required_dock_type(replace(SHIPMENT, load_weight_kg=30_000), None) == "STANDARD"


def test_s2_wrong_dock_type():
    assert check(slot=replace(SLOT, dock_type="REEFER")) == ["S2"]


def test_s3_too_heavy_for_dock():
    assert check(shipment=replace(SHIPMENT, load_weight_kg=21_000),
                 slot=replace(SLOT, max_weight_kg=20_000)) == ["S3"]


@pytest.mark.parametrize("slot_changes", [
    {"slot_status": "BLOCKED"},
    {"slot_status": "CLOSED"},
    {"dock_status": "MAINTENANCE"},
])
def test_s4_slot_or_dock_not_open(slot_changes):
    assert check(slot=replace(SLOT, **slot_changes)) == ["S4"]


def test_s5_held_by_another_shipment():
    assert check(slot=replace(SLOT, held_by_shipment="SHP-OTHER")) == ["S5"]


def test_s5_own_current_slot_passes():  # Q2
    assert check(slot=replace(SLOT, held_by_shipment="SHP-T")) == []


def test_s6_starts_before_arrival():
    assert check(arrival=at("12:01")) == ["S6"]
    assert check(arrival=at("12:00")) == []        # "at or after" arrival


def test_s7_must_finish_by():
    # 12:00 + 45 min unload = 12:45
    assert check(finish_by=at("12:45")) == []
    assert check(finish_by=at("12:44")) == ["S7"]


def test_a7_unload_longer_than_slot_is_not_a_failure():
    long_unload = replace(SHIPMENT, expected_unload_min=75)
    assert SLOT.end - SLOT.start == timedelta(minutes=60)
    assert check(shipment=long_unload) == []


def test_several_failures_are_all_reported():
    slot = replace(SLOT, dock_type="REEFER", slot_status="BLOCKED", held_by_shipment="SHP-OTHER")
    assert check(slot=slot, arrival=at("13:00")) == ["S2", "S4", "S5", "S6"]
