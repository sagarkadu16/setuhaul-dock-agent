"""Who is talking: email login -> signed session token -> driver_id.

The driver_id used by the agent and its tools comes ONLY from read_token(),
never from anything the driver or the LLM types.
"""

import sqlite3

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from setuhaul.config import get_settings
from setuhaul.db import queries
from setuhaul.models.domain import Driver

SESSION_MAX_AGE_S = 12 * 60 * 60  # one driving shift
_SALT = "setuhaul.session.v1"


class LoginFailed(Exception):
    pass


class InvalidSession(Exception):
    pass


def _serializer() -> URLSafeTimedSerializer:
    secret = get_settings().session_secret.get_secret_value()
    return URLSafeTimedSerializer(secret, salt=_SALT)


def login(conn: sqlite3.Connection, email: str) -> tuple[Driver, str]:
    driver = queries.driver_for_email(conn, email)
    if driver is None or driver.driver_status != "ACTIVE":
        raise LoginFailed("Unknown email")  # same message for both: don't reveal which
    return driver, _serializer().dumps({"driver_id": driver.driver_id})


def read_token(token: str) -> str:
    """Return the driver_id inside a valid token, or raise InvalidSession."""
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE_S)
    except SignatureExpired as exc:
        raise InvalidSession("Session expired, please log in again") from exc
    except BadSignature as exc:
        raise InvalidSession("Invalid session") from exc
    return data["driver_id"]

def driver_for_session(conn: sqlite3.Connection, driver_id: str) -> Driver:
    """The Driver behind an already-verified session. Raises InvalidSession if it's gone."""
    driver = queries.get_driver(conn, driver_id)
    if driver is None:
        raise InvalidSession("Invalid session")
    return driver