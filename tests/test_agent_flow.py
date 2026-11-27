"""T4.5: the AC12 conversation end to end through handle_driver_message, plus AC21 and AC6.

The model is scripted, so this checks the plumbing (cleaning, storage, tools, memory, data never
changing), not the model's judgement - that is the manual check list in the README.
"""

import json
import sqlite3

import pytest
from langchain_core.messages import ToolMessage

from app.agent.graph import Agent, open_checkpointer
from app.chat import handle_driver_message
from app.conversations import create_conversation, get_messages, list_conversations
from app.input_clean import InvalidMessage
from tests.fakes import ScriptedChatModel, call, say

OPERATIONAL_TABLES = ("shipments", "appointments", "appointment_slots", "eta_updates")


def snapshot(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in OPERATIONAL_TABLES}
    finally:
        conn.close()


def tool_results(model):
    """Tool results the model saw, by tool name (latest call wins)."""
    results = {}
    for messages in model.seen:
        for m in messages:
            if isinstance(m, ToolMessage):
                results[m.name] = json.loads(m.content)
    return results


def test_ac12_and_ac21_full_conversation(db, db_path, tmp_path):
    before = snapshot(db_path)
    model = ScriptedChatModel(script=[
        # Turn 1: DRV006 has two deliveries, so ask which one.
        say("Which delivery is late? 1) Medical devices from Ghaziabad  2) Office supplies from Noida"),
        # Turn 2: "the Jaipur one, reaching 11:20" answers both questions.
        call("resolve_arrival", "c1", shipment_id="SHP1006", clock_time="11:20"),
        call("find_slots", "c2", shipment_id="SHP1006", earliest_start="11:20"),
        say("1) Dock D2 at 12:00\n2) Dock D4 at 12:00\n3) Dock D1 at 13:00\n4) Dock D2 at 13:00\n"
            "5) Dock D3 at 13:00\nTell me if you must finish by a certain time."),
        # Turn 3: driver picks D2 at 12:00.
        call("check_pick", "c3", shipment_id="SHP1006", dock_code="D2", start_time="12:00", arrival_time="11:20"),
        say("Dock D2 at 12:00 is available now. It is not booked yet. "
            "Please call Neha Bansal on +91-141-5001101 to confirm it."),
    ])
    agent = Agent(model, open_checkpointer(tmp_path / "cp.db"), db_path=db_path)
    thread = create_conversation(db, "DRV006")

    def send(text):
        return handle_driver_message(db, agent, thread.thread_id, "DRV006", text)

    first = send("stuck in traffic, I'll be late")
    assert first.agent_message.text.startswith("Which delivery is late?")
    assert first.tool_calls == []

    second = send("the Jaipur one, reaching 11:20")
    assert [c["name"] for c in second.tool_calls] == ["resolve_arrival", "find_slots"]
    found = tool_results(model)["find_slots"]
    assert [(s["dock"], s["start"]) for s in found["slots"]] == [
        ("D2", "12:00"), ("D4", "12:00"), ("D1", "13:00"), ("D2", "13:00"), ("D3", "13:00"),
    ]

    third = send("I'll take the 12 o'clock one on D2")
    picked = tool_results(model)["check_pick"]
    assert picked["valid"] is True
    assert picked["coordinator"]["phone"] == "+91-141-5001101"
    reply = third.agent_message.text
    assert "D2" in reply and "12:00" in reply and "+91-141-5001101" in reply

    # Everything is stored, in order, and the conversation now knows its shipment (R9).
    stored = get_messages(db, thread.thread_id, "DRV006")
    assert [m.sender for m in stored] == ["DRIVER", "AGENT"] * 3
    assert list_conversations(db, "DRV006")[0].shipment_id == "SHP1006"

    # AC21 / R26: no operational data changed.
    assert snapshot(db_path) == before


def test_ac6_invalid_message_never_reaches_agent(db, db_path, tmp_path):
    model = ScriptedChatModel(script=[])        # any call would fail the test
    agent = Agent(model, open_checkpointer(tmp_path / "cp.db"), db_path=db_path)
    thread = create_conversation(db, "DRV006")
    for text in ("   ", "a" * 1_200):
        with pytest.raises(InvalidMessage):
            handle_driver_message(db, agent, thread.thread_id, "DRV006", text)
    assert model.seen == []
    assert get_messages(db, thread.thread_id, "DRV006") == []


def test_driver_message_is_saved_even_if_agent_fails(db, db_path, tmp_path):  # R9
    model = ScriptedChatModel(script=[TimeoutError("down")])
    agent = Agent(model, open_checkpointer(tmp_path / "cp.db"), db_path=db_path)
    thread = create_conversation(db, "DRV006")
    turn = handle_driver_message(db, agent, thread.thread_id, "DRV006", "late by one hour")
    assert turn.agent_message.text.startswith("Sorry")
    assert [m.text for m in get_messages(db, thread.thread_id, "DRV006")] == [
        "late by one hour", turn.agent_message.text,
    ]
