"""Which shipment is the driver talking about? Python decides, the LLM never guesses."""

import sqlite3
from dataclasses import dataclass

from setuhaul.db.queries import open_shipments_for_driver
from setuhaul.models.domain import Shipment
from setuhaul.services.booking import owned_shipment

ON_ROAD_STATUSES = {"IN_TRANSIT", "AT_GATE", "WAITING"}


@dataclass(frozen=True)
class ShipmentChoice:
    shipment: Shipment | None          # set = use it
    options: tuple[Shipment, ...] = () # set = ask the driver to pick one of these


def choose_shipment(
    conn: sqlite3.Connection, driver_id: str, requested_id: str | None = None
) -> ShipmentChoice:
    if requested_id:                                    # the driver named one: check it is theirs
        return ShipmentChoice(owned_shipment(conn, driver_id, requested_id))

    open_ = open_shipments_for_driver(conn, driver_id)
    on_road = [s for s in open_ if s.current_status in ON_ROAD_STATUSES]

    if len(on_road) == 1:
        return ShipmentChoice(on_road[0])
    if len(open_) == 1:
        return ShipmentChoice(open_[0])
    return ShipmentChoice(None, tuple(on_road or open_))
