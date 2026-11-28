"""Run the LLM-judgement acceptance criteria against the real model (task T6.1).

Each scenario runs in a fresh throwaway copy of the seed database, prints the conversation and the
tools the model called, and applies a few simple automatic checks. Read the transcripts too: the
automatic checks catch obvious failures, not tone.

Usage: .venv/bin/python scripts/manual_checks.py [AC7 AC12 ...]
Costs a few cents per full run.
"""

from __future__ import annotations

import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.agent.graph import Agent, open_checkpointer
from app.agent.model import build_chat_model
from app.chat import ChatTurn, handle_driver_message
from app.config import DEFAULT_CLOCK, clock, settings
from app.conversations import create_conversation
from app.db import build_db, connect

BOOKED_WORDS = re.compile(r"\b(booked|reserved|held for you|is confirmed|have confirmed)\b", re.I)
NOT_BOOKED = re.compile(r"\bnot (yet )?(booked|reserved|confirmed)\b", re.I)


def says_booked(text: str) -> bool:
    """True if the reply claims a booking (ignoring 'not booked')."""
    return bool(BOOKED_WORDS.search(NOT_BOOKED.sub("", text)))


def tools_used(turn: ChatTurn) -> list[str]:
    return [c["name"] for c in turn.tool_calls]


Check = Callable[[list[ChatTurn]], list[str]]


@dataclass
class Scenario:
    ac: str
    title: str
    driver_id: str
    messages: list[str]
    check: Check
    clock_at: str = DEFAULT_CLOCK
    expect: str = ""


def expect(*conditions: tuple[bool, str]) -> list[str]:
    return [msg for ok, msg in conditions if not ok]


SCENARIOS = [
    Scenario(
        "AC7", "Two deliveries → asks which one", "DRV007",
        ["I will be late by 45 minutes"],
        lambda t: expect(
            ("Textiles" in t[0].agent_message.text and "Construction" in t[0].agent_message.text,
             "should list both deliveries by label"),
            ("?" in t[0].agent_message.text, "should ask a question"),
            ("find_slots" not in tools_used(t[0]), "should not search slots yet"),
        ),
        expect="Asks which delivery, listing Textiles from Ajmer and Construction equipment from Mumbai.",
    ),
    Scenario(
        "AC8", "Relative delay → confirms exact time", "DRV013",
        ["I am late by one hour"],
        lambda t: expect(
            ("11:00" in t[0].agent_message.text, "should suggest 11:00"),
            ("?" in t[0].agent_message.text, "should ask to confirm"),
            ("find_slots" not in tools_used(t[0]), "should wait for confirmation before slots"),
        ),
        expect='Asks "So you will reach at about 11:00?"',
    ),
    Scenario(
        "AC11", "Reefer, no slot after maintenance", "DRV015",
        ["Reaching 18:30"],
        lambda t: expect(
            ("find_slots" in tools_used(t[0]), "should look for slots"),
            ("+91-141-5001101" in t[0].agent_message.text, "should give the coordinator's number"),
            (not re.search(r"\bD\d\b", t[0].agent_message.text), "should not list any dock"),
        ),
        clock_at="2026-08-04T16:25:00+05:30",
        expect="Says no slot is available and gives +91-141-5001101.",
    ),
    Scenario(
        "AC12", "Full flow → pick D2 12:00 → coordinator, not booked", "DRV006",
        ["Stuck in traffic, I'll be late", "the Jaipur one", "reaching 11:20",
         "I'll take the 12 o'clock one on D2"],
        lambda t: expect(
            ("?" in t[0].agent_message.text, "turn 1 should ask which delivery"),
            (any("find_slots" in tools_used(x) for x in t), "should search slots"),
            ("check_pick" in tools_used(t[-1]), "should check the picked slot"),
            ("D2" in t[-1].agent_message.text and "12:00" in t[-1].agent_message.text,
             "last reply should repeat D2 12:00"),
            ("+91-141-5001101" in t[-1].agent_message.text, "last reply should give coordinator number"),
            (not any(says_booked(x.agent_message.text) for x in t), "must never say booked"),
        ),
        expect="Asks which delivery; shows D2 12:00, D4 12:00, D1 13:00…; on pick repeats D2 12:00 and gives +91-141-5001101.",
    ),
    Scenario(
        "AC15", "Off topic → short refusal, no tools", "DRV006",
        ["write me a poem"],
        lambda t: expect(
            (tools_used(t[0]) == [], "should not call any tool"),
            (len(t[0].agent_message.text) < 250, "should be short"),
        ),
        expect="One short polite sentence; no tool calls.",
    ),
    Scenario(
        "AC16", "All shipments cancelled", "DRV008",
        ["what time is my slot?"],
        lambda t: expect(
            ("cancel" in t[0].agent_message.text.lower(), "should say it is cancelled"),
            ("dispatch" in t[0].agent_message.text.lower(), "should say contact dispatch"),
            (tools_used(t[0]) == [], "should not call tools"),
        ),
        expect="Says the delivery was cancelled and to contact dispatch.",
    ),
    Scenario(
        "AC17", "Arrival in the past → asks again", "DRV006",
        ["The Jaipur delivery, reaching 9:00"],
        lambda t: expect(
            ("?" in t[0].agent_message.text, "should ask for the time again"),
            ("find_slots" not in tools_used(t[0]), "should not search slots"),
        ),
        expect="Says 9:00 has passed and asks for the arrival time again.",
    ),
    Scenario(
        "AC18", "Current slot still valid → shown and marked", "DRV001",
        ["Reaching 11:30"],
        lambda t: expect(
            ("find_slots" in tools_used(t[0]), "should search slots straight away"),
            ("current" in t[0].agent_message.text.lower(), "should mark the current slot"),
        ),
        expect="Lists D1 12:00 (your current slot), D2 12:00, D4 12:00, D1 13:00, D2 13:00.",
    ),
    Scenario(
        "AC19", "Everything in one message → no question", "DRV006",
        ["the Jaipur one, reaching 11:20"],
        lambda t: expect(
            ("find_slots" in tools_used(t[0]), "should search slots straight away"),
            ("D2" in t[0].agent_message.text and "12:00" in t[0].agent_message.text, "should list D2 12:00"),
        ),
        expect="Lists slots straight away, starting D2 12:00.",
    ),
    Scenario(
        "AC20", "Two unclear answers → hand over to coordinator", "DRV013",
        ["I'm going to be late", "not sure", "not sure"],
        lambda t: expect(
            ("+91-141-5001101" in t[-1].agent_message.text, "should give the coordinator's number"),
        ),
        expect="Asks for the arrival time; after two unclear answers gives +91-141-5001101.",
    ),
    Scenario(
        "Edge", '"Book it" → explains it cannot book', "DRV006",
        ["the Jaipur one, reaching 11:20", "book the first one for me"],
        lambda t: expect(
            ("+91-141-5001101" in t[-1].agent_message.text, "should give the coordinator's number"),
            (not says_booked(t[-1].agent_message.text), "must not say booked"),
        ),
        expect="Says it can't book and gives the coordinator's number.",
    ),
]


def run(scenario: Scenario, model) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = build_db(Path(tmp) / "app.db")
        clock.set(scenario.clock_at)
        agent = Agent(model, open_checkpointer(Path(tmp) / "cp.db"), db_path=db_path)
        conn = connect(db_path)
        try:
            thread = create_conversation(conn, scenario.driver_id)
            turns = []
            for text in scenario.messages:
                turn = handle_driver_message(conn, agent, thread.thread_id, scenario.driver_id, text)
                turns.append(turn)
                print(f"  DRIVER: {text}")
                for c in turn.tool_calls:
                    print(f"    [tool] {c['name']}({c['args']})")
                for line in turn.agent_message.text.splitlines():
                    print(f"  AGENT:  {line}")
            return scenario.check(turns)
        finally:
            conn.close()
            clock.set(DEFAULT_CLOCK)


def main(selected: list[str]) -> int:
    print(f"Model: {settings.llm_model}\n")
    model = build_chat_model()
    results = []
    for scenario in SCENARIOS:
        if selected and scenario.ac not in selected:
            continue
        print(f"=== {scenario.ac}: {scenario.title}  (driver {scenario.driver_id})")
        print(f"  expected: {scenario.expect}")
        problems = run(scenario, model)
        status = "PASS" if not problems else "CHECK"
        for p in problems:
            print(f"  !! {p}")
        print(f"  => {status}\n")
        results.append((scenario.ac, status))
    print("Summary: " + ", ".join(f"{ac} {s}" for ac, s in results))
    return 0 if all(s == "PASS" for _, s in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
