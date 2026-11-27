"""Level 2: Cognito access-token verification, with our own RSA key. No AWS calls."""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import io
import json
import logging

from setuhaul.config import get_settings
from setuhaul.services import cognito_auth
from setuhaul.services.cognito_auth import AuthUnavailable, verify_access_token
from setuhaul.services.identity import InvalidSession

from setuhaul.api import main
from setuhaul.api.deps import chat_limiter
from setuhaul.api.main import app
from setuhaul.db.connection import open_db
from setuhaul.db.scenario import load_scenario
from setuhaul.logging_setup import setup_logging

REGION = "ap-southeast-2"
POOL = f"{REGION}_TESTPOOL"
CLIENT = "test-client-id"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"
KID = "test-kid"
SUB = "11111111-2222-3333-4444-555555555555"

# One key pair for the whole file: RSA key generation is slow.
POOL_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ATTACKER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJWKS:
    """Stands in for PyJWKClient: knows one public key, looked up by `kid`."""

    def __init__(self, fail: bool = False):
        self.fail = fail

    def get_signing_key_from_jwt(self, token: str):
        if self.fail:
            raise jwt.PyJWKClientConnectionError("network down")
        if jwt.get_unverified_header(token).get("kid") != KID:
            raise jwt.PyJWKClientError("Unable to find a signing key that matches")
        return SimpleNamespace(key=POOL_KEY.public_key())


@pytest.fixture
def cognito(monkeypatch):
    """Cognito-mode settings pointing at a fake pool, and the fake JWKS in place."""
    monkeypatch.setenv("AUTH_MODE", "cognito")
    monkeypatch.setenv("COGNITO_REGION", REGION)
    monkeypatch.setenv("COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("COGNITO_CLIENT_ID", CLIENT)
    get_settings.cache_clear()
    monkeypatch.setattr(cognito_auth, "_jwks_client", lambda: FakeJWKS())
    yield
    get_settings.cache_clear()


def claims(**changes) -> dict:
    """A valid access-token payload. Pass name=None to remove a claim."""
    now = int(time.time())
    base = {"sub": SUB, "iss": ISSUER, "client_id": CLIENT, "token_use": "access",
            "iat": now, "exp": now + 3600}
    base.update(changes)
    return {k: v for k, v in base.items() if v is not None}


def sign(payload: dict, key=POOL_KEY, kid: str = KID, alg: str = "RS256") -> str:
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": kid})

def test_valid_access_token_returns_sub(cognito):
    assert verify_access_token(sign(claims())) == SUB


def test_expired_within_leeway_is_accepted(cognito):
    now = int(time.time())
    assert verify_access_token(sign(claims(exp=now - 10))) == SUB


@pytest.mark.parametrize("token", [
    pytest.param(lambda: "not.a.jwt", id="garbage"),
    pytest.param(lambda: jwt.encode(claims(), None, algorithm="none", headers={"kid": KID}), id="alg-none"),
    pytest.param(lambda: sign(claims(), key="x" * 32, alg="HS256"), id="hs256-confusion"),
    pytest.param(lambda: sign(claims(), key=ATTACKER_KEY), id="forged-known-kid"),
    pytest.param(lambda: sign(claims(), key=ATTACKER_KEY, kid="evil"), id="forged-unknown-kid"),
    pytest.param(lambda: sign(claims(token_use="id")), id="id-token"),
    pytest.param(lambda: sign(claims(client_id="other-app")), id="other-client"),
    pytest.param(lambda: sign(claims(iss=f"https://cognito-idp.{REGION}.amazonaws.com/{REGION}_EVIL")), id="other-pool"),
    pytest.param(lambda: sign(claims(exp=int(time.time()) - 3600)), id="expired"),
    pytest.param(lambda: sign(claims(exp=None)), id="missing-exp"),
    pytest.param(lambda: sign(claims(token_use=None)), id="missing-token_use"),
    pytest.param(lambda: sign(claims(client_id=None)), id="missing-client_id"),
])
def test_bad_tokens_are_rejected(cognito, token):
    with pytest.raises(InvalidSession):
        verify_access_token(token())


def test_expired_token_says_log_in_again(cognito):
    with pytest.raises(InvalidSession, match="expired"):
        verify_access_token(sign(claims(exp=int(time.time()) - 3600)))


def test_jwks_unreachable_is_unavailable_not_invalid(cognito, monkeypatch):
    monkeypatch.setattr(cognito_auth, "_jwks_client", lambda: FakeJWKS(fail=True))
    with pytest.raises(AuthUnavailable):
        verify_access_token(sign(claims()))


REAL_JWKS_CLIENT = cognito_auth._jwks_client     # saved before any fixture swaps it out
JWKS = {"keys": [jwt.algorithms.RSAAlgorithm.to_jwk(POOL_KEY.public_key(), as_dict=True)
                 | {"kid": KID, "alg": "RS256", "use": "sig"}]}


def test_junk_kids_cannot_make_us_hammer_cognito(cognito, monkeypatch):
    """1000 tokens with made-up `kid`s must not become 1000 downloads of the JWKS."""
    fetched = []

    class FakeOpener:                              # the network layer under PyJWKClient
        def open(self, request, timeout):
            fetched.append(request.full_url)
            return io.BytesIO(json.dumps(JWKS).encode())

    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: FakeOpener())
    monkeypatch.setattr(cognito_auth, "_jwks_client", REAL_JWKS_CLIENT)
    REAL_JWKS_CLIENT.cache_clear()                 # fresh client built from the test settings

    for i in range(1000):
        with pytest.raises(InvalidSession):
            verify_access_token(sign(claims(), key=ATTACKER_KEY, kid=f"junk-{i}"))

    assert verify_access_token(sign(claims())) == SUB      # real users still get in
    assert len(fetched) <= 2                               # first load + one refetch, then cooldown
    assert set(fetched) == {f"{ISSUER}/.well-known/jwks.json"}
    REAL_JWKS_CLIENT.cache_clear()                 # don't leak this client into other tests

@pytest.mark.parametrize("token, reason", [
    (lambda: sign(claims(client_id="other-app")), "wrong client_id"),
    (lambda: sign(claims(token_use="id")), "not an access token"),
    (lambda: sign(claims(), key=ATTACKER_KEY), "InvalidSignatureError"),
])
def test_rejection_reason_is_logged_but_token_is_not(cognito, caplog, token, reason):
    token = token()
    with caplog.at_level(logging.WARNING, logger=cognito_auth.__name__):
        with pytest.raises(InvalidSession, match="^Invalid session$"):   # caller learns nothing
            verify_access_token(token)
    assert reason in caplog.text                                         # we learn why
    assert token not in caplog.text

# ---- API level: the whole path token -> sub -> driver through FastAPI ----

MANOJ_SUB = "69fe94b8-50a1-7006-fba8-5a0cd3f5fd4e"   # linked to DRV006 in the scenario YAML


@pytest.fixture
def api(cognito, tmp_path, monkeypatch):
    """Cognito mode + a fresh scenario DB in a temp folder + a test client."""
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.db"))
    get_settings.cache_clear()
    load_scenario("two_drivers_one_slot")
    return TestClient(app)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}

def test_linked_driver_can_start_a_thread(api):
    r = api.post("/threads", headers=bearer(sign(claims(sub=MANOJ_SUB))))
    assert r.status_code == 201
    assert r.json()["driver_name"] == "Manoj Sharma"


def test_unlinked_cognito_user_gets_403(api):
    r = api.post("/threads", headers=bearer(sign(claims())))   # SUB is in no app_users row
    assert r.status_code == 403


def test_deactivated_driver_is_locked_out_immediately(api):
    token = sign(claims(sub=MANOJ_SUB))
    assert api.post("/threads", headers=bearer(token)).status_code == 201
    with open_db() as conn:
        conn.execute("UPDATE drivers SET driver_status = 'SUSPENDED' WHERE driver_id = 'DRV006'")
        conn.commit()
    assert api.post("/threads", headers=bearer(token)).status_code == 403


def test_forged_token_gets_401_with_bearer_challenge(api):
    r = api.post("/threads", headers=bearer(sign(claims(sub=MANOJ_SUB), key=ATTACKER_KEY)))
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == "Bearer"


def test_jwks_down_gets_503_with_retry_after(api, monkeypatch):
    monkeypatch.setattr(cognito_auth, "_jwks_client", lambda: FakeJWKS(fail=True))
    r = api.post("/threads", headers=bearer(sign(claims(sub=MANOJ_SUB))))
    assert r.status_code == 503
    assert "Retry-After" in r.headers


def test_email_only_login_is_hidden_in_cognito_mode(api):
    r = api.post("/login", json={"email": "manoj@setuhaul.test"})
    assert r.status_code == 404

def test_auth_config_in_cognito_mode_gives_public_client_info(api):
    assert api.get("/auth/config").json() == {
        "mode": "cognito", "region": REGION, "client_id": CLIENT,
    }

def test_auth_config_in_local_mode_gives_only_the_mode():
    assert TestClient(app).get("/auth/config").json() == {
        "mode": "local", "region": None, "client_id": None,
    }


def test_chat_is_rate_limited_per_driver(api, monkeypatch):
    monkeypatch.setenv("CHAT_RATE_LIMIT", "2")
    get_settings.cache_clear()
    chat_limiter.cache_clear()                     # rebuild the limiter with limit=2
    monkeypatch.setattr(main, "run_turn", lambda *a, **k: "ok")   # no LLM in tests
    manoj = bearer(sign(claims(sub=MANOJ_SUB)))
    thread_id = api.post("/threads", headers=manoj).json()["thread_id"]

    def chat(i: int):
        body = {"thread_id": thread_id, "text": "hi", "client_msg_id": f"m{i}"}
        return api.post("/chat", headers=manoj, json=body)

    assert [chat(i).status_code for i in range(2)] == [200, 200]
    r = chat(2)
    assert r.status_code == 429
    assert 0 < int(r.headers["Retry-After"]) <= 60
    chat_limiter.cache_clear()                     # don't leak limit=2 into other tests




@pytest.fixture
def json_logs():
    """Switch to JSON logs (into a buffer) for one test, then put pytest's logging back."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    buffer = io.StringIO()
    setup_logging(json_logs=True, stream=buffer)
    yield buffer
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_log_lines_are_json_and_carry_the_request_id(api, json_logs):
    r = api.post("/threads", headers=bearer(sign(claims(), key=ATTACKER_KEY)))
    lines = [json.loads(line) for line in json_logs.getvalue().splitlines()]
    rejected = [entry for entry in lines if entry["msg"].startswith("Rejected access token")]
    access = [entry for entry in lines if entry["msg"].startswith("POST /threads -> 401")]
    assert rejected and access
    assert rejected[0]["request_id"] == access[0]["request_id"] == r.headers["X-Request-ID"]


def test_unsafe_incoming_request_id_is_replaced(api):
    assert api.get("/health", headers={"X-Request-ID": "lb-abc-123"}).headers["X-Request-ID"] == "lb-abc-123"
    replaced = api.get("/health", headers={"X-Request-ID": 'x" fake=1'}).headers["X-Request-ID"]
    assert replaced != 'x" fake=1' and len(replaced) == 32