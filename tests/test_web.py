"""T5.1: the HTTP API with a scripted model (no network)."""

import pytest
from fastapi.testclient import TestClient

from app.agent.model import ModelNotConfigured
from app.web.main import create_app
from tests.fakes import ScriptedChatModel, say


@pytest.fixture
def model():
    return ScriptedChatModel(script=[])


@pytest.fixture
def make_client(db_path, tmp_path, model):
    def _make(model_factory=lambda: model):
        app = create_app(db_path=db_path, checkpoint_path=tmp_path / "cp.db", model_factory=model_factory)
        return TestClient(app)
    return _make


@pytest.fixture
def client(make_client):
    return make_client()


def log_in_as_manoj(client):
    assert client.post("/api/login", json={"email": "manoj@test.com"}).status_code == 200
    r = client.post("/api/link", json={"phone": "+91-9000010006"})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def test_page_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "SetuHaul" in r.text
    assert client.get("/static/app.js").status_code == 200


def test_demo_driver_numbers_on_login_page_are_real_drivers(client, ro_db):
    import re

    from app.identity import _phone_key

    page = client.get("/").text
    shown = re.findall(r'data-phone="(\d+)"', page)
    assert shown[0] == "9000010006"          # the main demo first
    known = {_phone_key(r["phone"]) for r in ro_db.execute("SELECT phone FROM drivers")}
    assert sorted(shown) == sorted(known)    # every driver listed exactly once


def test_page_is_not_cached(client):
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/static/style.css").headers["cache-control"] == "no-cache"


def test_must_log_in_first(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/conversations").status_code == 401
    assert client.post("/api/link", json={"phone": "9000010006"}).status_code == 401


def test_invalid_email(client):  # AC2
    r = client.post("/api/login", json={"email": "manoj@"})
    assert r.status_code == 400
    assert "doesn't look like an email" in r.json()["detail"]


def test_login_then_link(client):  # AC1, AC3
    r = client.post("/api/login", json={"email": " Manoj@Test.com "})
    assert r.json()["user"] == {"email": "manoj@test.com", "linked": False, "driver_name": None}
    # Not linked yet: no chats.
    assert client.get("/api/conversations").status_code == 403
    user = log_in_as_manoj(client)
    assert user == {"email": "manoj@test.com", "linked": True, "driver_name": "Manoj Sharma"}
    assert client.get("/api/me").json()["user"]["linked"] is True


def test_unknown_phone(client):  # AC4
    client.post("/api/login", json={"email": "x@test.com"})
    r = client.post("/api/link", json={"phone": "+91-0000000000"})
    assert r.status_code == 400 and "couldn't find a driver" in r.json()["detail"]


def test_logout(client):
    log_in_as_manoj(client)
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401


def test_chat_round_trip(client, model):
    log_in_as_manoj(client)
    model.script.append(say("Which delivery is late? 1) Medical devices from Ghaziabad  2) Office supplies from Noida"))

    conv = client.post("/api/conversations").json()["conversation"]
    r = client.post(f"/api/conversations/{conv['id']}/messages", json={"text": "  running late  "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["driver_message"]["text"] == "running late"     # cleaned
    assert body["agent_message"]["text"].startswith("Which delivery is late?")

    listed = client.get("/api/conversations").json()["conversations"]
    assert listed[0]["id"] == conv["id"]
    assert listed[0]["preview"].startswith("Which delivery")
    messages = client.get(f"/api/conversations/{conv['id']}").json()["messages"]
    assert [m["sender"] for m in messages] == ["DRIVER", "AGENT"]


def test_too_long_message_rejected_and_agent_not_called(client, model):  # AC6
    log_in_as_manoj(client)
    conv = client.post("/api/conversations").json()["conversation"]
    r = client.post(f"/api/conversations/{conv['id']}/messages", json={"text": "a" * 1_200})
    assert r.status_code == 400 and "too long" in r.json()["detail"]
    assert model.seen == []
    assert client.get(f"/api/conversations/{conv['id']}").json()["messages"] == []


def test_cannot_open_another_drivers_conversation(make_client):  # R5
    manoj, other = make_client(), make_client()
    log_in_as_manoj(manoj)
    conv = manoj.post("/api/conversations").json()["conversation"]

    other.post("/api/login", json={"email": "nitin@test.com"})
    other.post("/api/link", json={"phone": "+91-9000010007"})
    assert other.get(f"/api/conversations/{conv['id']}").status_code == 404
    assert other.post(f"/api/conversations/{conv['id']}/messages", json={"text": "hi"}).status_code == 404
    assert conv["id"] not in [c["id"] for c in other.get("/api/conversations").json()["conversations"]]


def test_missing_model_settings_give_clear_error(make_client):
    def not_configured():
        raise ModelNotConfigured("Set LLM_API_KEY and LLM_MODEL in .env.")

    client = make_client(model_factory=not_configured)
    log_in_as_manoj(client)
    conv = client.post("/api/conversations").json()["conversation"]
    r = client.post(f"/api/conversations/{conv['id']}/messages", json={"text": "late"})
    assert r.status_code == 503 and "LLM_API_KEY" in r.json()["detail"]
    assert client.get(f"/api/conversations/{conv['id']}").json()["messages"] == []
