"""The slot decision is traced to LangSmith with the right facts, and only when one is made.

No network: a fake LangSmith client records what would have been sent.
"""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from langsmith import tracing_context

from setuhaul.clock import get_clock
from setuhaul.config import get_settings
from setuhaul.db.connection import open_db
from setuhaul.db.scenario import load_scenario
from setuhaul.services.contention import resolve_slot, submit_request

SLOT = "SLOT-JAI-019"
MANOJ, ARJUN = ("DRV006", "SHP1006"), ("DRV012", "SHP1012")   # HIGH vs NORMAL


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.db"))
    get_settings.cache_clear()
    load_scenario("two_drivers_one_slot")
    with open_db() as c:
        yield c
    get_settings.cache_clear()


def _decision_runs(client: MagicMock) -> list[dict]:
    """resolve_slot runs sent to the fake client: inputs on create, outputs on update."""
    created = [c.kwargs for c in client.create_run.call_args_list
               if c.kwargs.get("name") == "resolve_slot"]
    updated = {c.kwargs["run_id"]: c.kwargs for c in client.update_run.call_args_list}
    return [{**run, "outputs": updated[run["id"]]["outputs"]} for run in created]


def _contest(conn):
    business_now = get_clock().now()
    t0 = business_now
    for driver, shipment in (MANOJ, ARJUN):
        submit_request(conn, driver, shipment, SLOT, business_now=business_now, wall_now=t0)
    after_window = t0 + timedelta(seconds=get_settings().decision_window_seconds + 1)
    return business_now, t0, after_window


def test_decision_is_traced_with_winner_loser_and_reason(conn):
    business_now, _, after_window = _contest(conn)
    client = MagicMock()
    with tracing_context(enabled=True, client=client, project_name="test"):
        resolve_slot(conn, SLOT, business_now=business_now, wall_now=after_window)

    [run] = _decision_runs(client)
    assert run["inputs"]["slot_id"] == SLOT
    assert "conn" not in run["inputs"]                       # no DB handle in traces
    out = run["outputs"]
    assert out["winner"] == "SHP1006"
    [loser] = [o for o in out["outcomes"] if o["status"] == "LOST"]
    assert loser["shipment_id"] == "SHP1012"
    assert loser["reason"] == "another truck was ranked ahead on: priority"


def test_polling_inside_the_window_sends_no_trace(conn):
    business_now, t0, _ = _contest(conn)
    client = MagicMock()
    with tracing_context(enabled=True, client=client, project_name="test"):
        resolve_slot(conn, SLOT, business_now=business_now, wall_now=t0 + timedelta(seconds=1))
    assert _decision_runs(client) == []
