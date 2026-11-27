"""Push notifications (outbox + polling) and slot buttons, on a temp scenario DB. No LLM."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from setuhaul.api.main import app
from setuhaul.clock import FixedClock, get_clock
from setuhaul.config import get_settings
from setuhaul.db import queries
from setuhaul.db.connection import open_db
from setuhaul.db.scenario import load_scenario
from setuhaul.models.domain import NotificationKind
from setuhaul.services import chat
from setuhaul.services.chat import NotYourThread
from setuhaul.services.contention import submit_request
from setuhaul.services.notifications import poll
from setuhaul.tools import driver_tools as dt
from setuhaul.tools.context import ToolContext

SCENARIO = "two_drivers_one_slot"
SLOT = "SLOT-JAI-019"            # contested 12:00 slot (scenario `expect`)
RUNNER_UP_OFFER = "SLOT-JAI-006"
MANOJ, ARJUN = ("DRV006", "SHP1006"), ("DRV012", "SHP1012")   # HIGH vs NORMAL


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.db"))
    get_settings.cache_clear()
    load_scenario(SCENARIO)
    with open_db() as c:
        yield c
    get_settings.cache_clear()


@pytest.fixture
def contest(conn):
    """Both drivers open a chat, then request the same slot at wall time t0."""
    business_now = get_clock().now()
    t0 = business_now                         # any aware instant works as wall time
    threads = {d: chat.start_thread(conn, d, t0 - timedelta(minutes=1)) for d, _ in (MANOJ, ARJUN)}
    for driver, shipment in (MANOJ, ARJUN):
        submit_request(conn, driver, shipment, SLOT, business_now=business_now, wall_now=t0)
    window = timedelta(seconds=get_settings().decision_window_seconds)

    def poll_as(driver: str, after: int = 0, at: timedelta = window + timedelta(seconds=1)):
        return poll(conn, driver, threads[driver], after, business_now=business_now, wall_now=t0 + at)

    return poll_as, threads, t0


def system_messages(conn, thread_id):
    return [m for m in queries.messages_for_thread(conn, thread_id) if m.sender_type == "SYSTEM"]


def test_nothing_is_pushed_while_the_window_is_open(contest):
    poll_as, _, _ = contest
    assert poll_as("DRV006", at=timedelta(seconds=1)) == []


def test_winner_and_runner_up_each_get_exactly_their_own_message(contest):
    poll_as, _, _ = contest
    [won] = poll_as("DRV006")                     # this poll decides the window for both
    [lost] = poll_as("DRV012")

    assert won.notification.kind == NotificationKind.SLOT_CONFIRMED
    assert "dock D2, 12:00–13:00" in won.notification.message_text
    assert won.offered_slot is None

    assert lost.notification.kind == NotificationKind.SLOT_NOT_BOOKED
    assert lost.offered_slot.slot_id == RUNNER_UP_OFFER
    assert "Next valid slot" in lost.notification.message_text


def test_polling_again_repeats_nothing_and_the_cursor_skips_seen(conn, contest):
    poll_as, threads, _ = contest
    [first] = poll_as("DRV006")
    [again] = poll_as("DRV006")                   # same cursor: same row, not a new one
    assert again.notification.notification_id == first.notification.notification_id
    assert len(system_messages(conn, threads["DRV006"])) == 1   # stored in chat once
    assert poll_as("DRV006", after=first.notification.notification_id) == []


def test_a_new_login_does_not_replay_old_decisions(conn, contest):
    poll_as, _, t0 = contest
    poll_as("DRV006")
    later_thread = chat.start_thread(conn, "DRV006", t0 + timedelta(hours=1))
    assert poll(conn, "DRV006", later_thread, 0,
                business_now=get_clock().now(), wall_now=t0 + timedelta(hours=1)) == []


def test_cannot_poll_someone_elses_thread(conn, contest):
    _, threads, t0 = contest
    with pytest.raises(NotYourThread):
        poll(conn, "DRV012", threads["DRV006"], 0, business_now=get_clock().now(), wall_now=t0)


def test_agent_sees_delivered_notices_until_it_replies(conn, contest):
    poll_as, threads, t0 = contest
    poll_as("DRV012")
    [notice] = queries.system_notices_since_last_agent_reply(conn, threads["DRV012"])
    assert notice.startswith("❌ Not booked")
    chat.record_agent_message(conn, threads["DRV012"], "Sorry about that.", t0)
    assert queries.system_notices_since_last_agent_reply(conn, threads["DRV012"]) == []


# --- Slot buttons: tools decide what the UI may offer ---------------------------

def test_slot_options_become_buttons_and_requesting_clears_them(conn):
    clock = get_clock()
    ctx = ToolContext(conn, "DRV006", clock, FixedClock(clock.now()))
    result = dt.slot_options(ctx)
    assert [s.slot_id for s in ctx.shown_slots] == [o["slot_id"] for o in result["options"]]
    assert ctx.shown_slots, "scenario should offer at least one slot"

    dt.request_slot(ctx, ctx.shown_slots[0].slot_id)
    assert ctx.shown_slots == []


# --- API ------------------------------------------------------------------------

def test_notifications_route_needs_login_and_returns_a_list(conn):
    api = TestClient(app)
    assert api.get("/notifications", params={"thread_id": "THR-x"}).status_code == 401

    login = api.post("/login", json={"email": "manoj@setuhaul.test"}).json()
    auth = {"Authorization": f"Bearer {login['token']}"}
    r = api.get("/notifications", params={"thread_id": login["thread_id"]}, headers=auth)
    assert r.status_code == 200
    assert r.json() == {"notifications": []}

    r = api.get("/notifications", params={"thread_id": "THR-not-mine"}, headers=auth)
    assert r.status_code == 404
