"""Bind driver tools to one request's ToolContext. The LLM sees only the declared arguments."""

from langchain_core.tools import BaseTool, tool

from setuhaul.tools import driver_tools as dt
from setuhaul.tools.context import ToolContext


def build_tools(ctx: ToolContext) -> list[BaseTool]:
    @tool
    def my_shipment_status(shipment_id: str | None = None) -> dict:
        """Get the driver's shipment: priority, status, latest ETA and current dock slot.
        Leave shipment_id empty unless the driver chose one from a needs_choice list."""
        return dt.my_shipment_status(ctx, shipment_id)

    @tool
    def slot_options(shipment_id: str | None = None) -> dict:
        """List up to 3 valid dock slots the truck can still make, earliest first.
        Call before suggesting any slot. Never invent slot ids."""
        return dt.slot_options(ctx, shipment_id)

    @tool
    def request_slot(slot_id: str, shipment_id: str | None = None) -> dict:
        """Request a slot the driver explicitly agreed to. slot_id must come from slot_options
        or an offered_slot. The result is PENDING, not a booking."""
        return dt.request_slot(ctx, slot_id, shipment_id)

    @tool
    def check_my_request(shipment_id: str | None = None) -> dict:
        """Check the decision on the driver's latest slot request: PENDING, WON, or LOST
        with a reason and an offered_slot."""
        return dt.check_my_request(ctx, shipment_id)

    return [my_shipment_status, slot_options, request_slot, check_my_request]
