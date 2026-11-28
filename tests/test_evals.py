"""The eval harness and code evaluators work, using a scripted fake LLM (no network, no key)."""

from itertools import cycle

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from setuhaul.agent import graph as graph_module
from setuhaul.config import get_settings
from setuhaul.evals import evaluators as ev
from setuhaul.evals.cases import CASES, SCENARIO
from setuhaul.evals.harness import run_case

CASE_IDS = [c["id"] for c in CASES]


class ScriptedLLM(GenericFakeChatModel):
    """Replays fixed AI messages; bind_tools is a no-op so it fits the agent graph."""

    def bind_tools(self, tools, **kwargs):
        return self


def _use_llm(monkeypatch, *messages: AIMessage) -> None:
    monkeypatch.setattr(graph_module, "make_llm",
                        lambda: ScriptedLLM(messages=cycle(messages)))


def _call(name: str, **args) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"call_{name}"}])


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "local")
    yield
    get_settings.cache_clear()


def test_case_ids_are_unique_and_complete():
    assert len(CASE_IDS) == len(set(CASE_IDS))
    for case in CASES:
        assert {"id", "driver_id", "turns", "expect_tools", "forbid_tools",
                "forbidden_text", "behaviour"} <= case.keys()


def test_harness_records_replies_and_tool_calls(monkeypatch):
    _use_llm(monkeypatch, _call("slot_options", shipment_id="SHP1006"),
             AIMessage("1) Dock D2 at 12:00. Which one do you want?"))
    out = run_case({"driver_id": "DRV006", "turns": ["reaching 11:20"], "scenario": SCENARIO})

    assert out["final_reply"].startswith("1) Dock D2")
    assert [c["name"] for c in out["tool_calls"]] == ["slot_options"]
    assert out["guardrail_blocked"] is False


def test_harness_shows_the_guardrail_replacing_a_false_booking(monkeypatch):
    _use_llm(monkeypatch, AIMessage("Done, dock D2 at 12:00 is booked for you!"))
    out = run_case({"driver_id": "DRV012", "turns": ["book D2 now"], "scenario": SCENARIO})

    assert out["guardrail_blocked"] is True
    ref = {"expect_tools": [], "forbid_tools": [], "forbidden_text": [], "behaviour": ""}
    assert ev.no_false_booking_shown({}, out, ref)["score"] is True
    assert ev.guardrail_triggered({}, out, ref)["score"] is True


def _outputs(replies, tools=()):
    return {"replies": replies, "final_reply": replies[-1],
            "tool_calls": [{"name": t, "args": {}} for t in tools], "guardrail_blocked": False}


REF = {"expect_tools": ["slot_options"], "forbid_tools": ["request_slot"],
       "forbidden_text": ["How to help:"], "behaviour": "x"}


def test_tool_evaluators():
    good = _outputs(["ok"], tools=["my_shipment_status", "slot_options"])
    bad = _outputs(["ok"], tools=["request_slot"])
    assert ev.expected_tools_called({}, good, REF)["score"]
    assert ev.forbidden_tools_not_called({}, good, REF)["score"]
    assert not ev.expected_tools_called({}, bad, REF)["score"]
    assert not ev.forbidden_tools_not_called({}, bad, REF)["score"]


def test_leak_and_booking_evaluators():
    assert not ev.no_leaks({}, _outputs(["How to help: 1. ..."]), REF)["score"]
    assert ev.no_leaks({}, _outputs(["I can't share that."]), REF)["score"]
    assert not ev.no_false_booking_shown({}, _outputs(["D2 is confirmed."]), REF)["score"]
    assert ev.no_false_booking_shown({}, _outputs(["Pending, not booked yet."]), REF)["score"]
