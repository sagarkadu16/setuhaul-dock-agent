"""T4.2-T4.4: prompt, tools and the agent loop, with a scripted model (no network)."""

import json

import pytest
from langchain_core.messages import SystemMessage, ToolMessage

from app.agent.graph import SORRY, Agent, open_checkpointer
from app.agent.prompt import build_system_prompt
from app.agent.tools import TOOLS, check_pick, find_slots, resolve_arrival
from tests.fakes import ScriptedChatModel, call, say


def config_for(driver_id, db_path, thread_id=None):
    return {"configurable": {"driver_id": driver_id, "db_path": str(db_path), "thread_id": thread_id}}


@pytest.fixture
def make_agent(db_path, tmp_path):
    checkpointer = open_checkpointer(tmp_path / "checkpoints.db")

    def _make(*script):
        model = ScriptedChatModel(script=list(script))
        return Agent(model, checkpointer, db_path=db_path), model

    return _make


# --- T4.2 prompt ---

def test_prompt_for_driver_with_two_deliveries(ro_db):
    prompt = build_system_prompt(ro_db, "DRV007", "Nitin Patil")
    assert "DRIVER: Nitin" in prompt
    assert "Active deliveries (2)" in prompt
    assert "Textiles from Ajmer (shipment_id SHP1007)" in prompt
    assert "Construction equipment from Mumbai (shipment_id SHP1016)" in prompt
    assert "Neha Bansal, +91-141-5001101" in prompt
    assert "Tuesday 04 August 2026, 09:40" in prompt
    # Nobody else's shipments.
    for other in ("SHP1006", "SHP1014", "SHP1019"):
        assert other not in prompt


def test_prompt_for_driver_with_no_active_delivery(ro_db):  # AC16 facts, R20a
    prompt = build_system_prompt(ro_db, "DRV008", "Ashok Prajapat")
    assert "NO active delivery" in prompt
    assert "cancelled" in prompt
    assert "shipment_id" not in prompt     # nothing to look slots up for


def test_prompt_rules_forbid_booking_words():
    from app.agent.prompt import RULES
    assert "Never say a slot is booked, reserved, held or confirmed" in RULES


# --- T4.3 tools ---

def test_model_cannot_pass_a_driver_id():
    for t in TOOLS:
        params = set(t.tool_call_schema.model_json_schema()["properties"])
        assert not params & {"driver_id", "config", "db_path", "thread_id"}, t.name


def test_tools_refuse_another_drivers_shipment(db_path):
    cfg = config_for("DRV006", db_path)
    for result in (
        resolve_arrival.invoke({"shipment_id": "SHP1014", "clock_time": "12:00"}, cfg),
        find_slots.invoke({"shipment_id": "SHP1014", "earliest_start": "12:00"}, cfg),
        check_pick.invoke({"shipment_id": "SHP1014", "dock_code": "D2",
                           "start_time": "12:00", "arrival_time": "11:25"}, cfg),
    ):
        assert json.loads(result) == {"error": "That delivery is not one of yours."}


def test_find_slots_tool_output(db_path):  # AC9 via the tool
    out = json.loads(find_slots.invoke({"shipment_id": "SHP1006", "earliest_start": "12:00"},
                                       config_for("DRV006", db_path)))
    assert [(s["number"], s["dock"], s["start"]) for s in out["slots"]] == [
        (1, "D2", "12:00"), (2, "D4", "12:00"), (3, "D1", "13:00"), (4, "D2", "13:00"), (5, "D3", "13:00"),
    ]
    assert out["coordinator"] == {"name": "Neha Bansal", "phone": "+91-141-5001101"}
    assert "NOT booked" in out["note"]


def test_resolve_arrival_tool(db_path):
    cfg = config_for("DRV013", db_path)
    assert json.loads(resolve_arrival.invoke({"shipment_id": "SHP1013", "delay_minutes": 60}, cfg)) == {
        "arrival_time": "11:00", "needs_confirmation": True,
    }
    past = json.loads(resolve_arrival.invoke({"shipment_id": "SHP1013", "clock_time": "09:00"}, cfg))
    assert "earlier than now" in past["error"]


def test_bad_time_format_is_an_error_not_a_crash(db_path):
    out = json.loads(find_slots.invoke({"shipment_id": "SHP1006", "earliest_start": "noon"},
                                       config_for("DRV006", db_path)))
    assert "HH:MM" in out["error"]


# --- T4.4 graph and run_turn ---

def test_reply_without_tools(make_agent):
    agent, model = make_agent(say("I can only help with delivery delays and slots."))
    result = agent.run_turn("THR-t", "DRV006", "write me a poem")
    assert result.reply == "I can only help with delivery delays and slots."
    assert result.tool_calls == [] and not result.failed
    first_call = model.seen[0]
    assert isinstance(first_call[0], SystemMessage) and "DRIVER: Manoj" in first_call[0].content


def test_tool_round_trip(make_agent):
    agent, model = make_agent(
        call("find_slots", shipment_id="SHP1006", earliest_start="12:00"),
        say("1) Dock D2 at 12:00"),
    )
    result = agent.run_turn("THR-t", "DRV006", "any slot after 12?")
    assert result.reply == "1) Dock D2 at 12:00"
    assert result.tool_calls == [{"name": "find_slots", "args": {"shipment_id": "SHP1006", "earliest_start": "12:00"}}]
    tool_result = next(m for m in model.seen[1] if isinstance(m, ToolMessage))
    assert json.loads(tool_result.content)["slots"][0]["dock"] == "D2"


def test_model_failure_gives_sorry(make_agent):  # spec §8
    agent, _ = make_agent(TimeoutError("LLM down"))
    result = agent.run_turn("THR-t", "DRV006", "late by one hour")
    assert result.failed and result.reply == SORRY


def test_conversation_continues_after_failure_mid_tool_call(make_agent):
    # The model asks for a tool, then fails before answering. The next turn must still work,
    # which needs the unanswered tool call to be closed off in memory.
    agent, model = make_agent(
        call("find_slots", shipment_id="SHP1006", earliest_start="12:00"),
        TimeoutError("LLM down"),
        say("Sorry about that. What time will you reach?"),
    )
    assert agent.run_turn("THR-t", "DRV006", "any slot after 12?").reply == SORRY
    second = agent.run_turn("THR-t", "DRV006", "hello?")
    assert second.reply == "Sorry about that. What time will you reach?"
    history = model.seen[-1]
    tool_ids = {m.tool_call_id for m in history if isinstance(m, ToolMessage)}
    assert "call_find_slots" in tool_ids


def test_memory_is_per_conversation(make_agent):
    agent, model = make_agent(say("first"), say("second"))
    agent.run_turn("THR-a", "DRV006", "message in A")
    agent.run_turn("THR-b", "DRV006", "message in B")
    texts_seen_in_b = [m.content for m in model.seen[1]]
    assert "message in A" not in texts_seen_in_b
