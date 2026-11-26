import math
import sqlite3
from collections.abc import Iterator
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status

from setuhaul.config import get_settings
from setuhaul.db.connection import open_db
from setuhaul.services.cognito_auth import AuthUnavailable, NotADriver, driver_id_for_access_token
from setuhaul.services.identity import InvalidSession, read_token
from setuhaul.services.rate_limit import RateLimiter


def get_conn() -> Iterator[sqlite3.Connection]:
    """One connection per request, always closed afterwards."""
    with open_db() as conn:
        yield conn


_BEARER = {"WWW-Authenticate": "Bearer"}

def current_driver_id(
    authorization: str = Header(default=""),
    conn: sqlite3.Connection = Depends(get_conn),
) -> str:
    """driver_id from 'Authorization: Bearer <token>'. The ONLY source of identity."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in", headers=_BEARER)
    try:
        if get_settings().auth_mode == "cognito":
            return driver_id_for_access_token(conn, token)
        return read_token(token)
    except InvalidSession as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e), headers=_BEARER) from e
    except NotADriver as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    except AuthUnavailable as e:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Login service temporarily unavailable",
            headers={"Retry-After": "30"},
        ) from e

@lru_cache
def chat_limiter() -> RateLimiter:
    """One shared limiter for the whole process (its memory IS the count)."""
    s = get_settings()
    return RateLimiter(s.chat_rate_limit, s.chat_rate_window_seconds)


def chat_driver_id(driver_id: str = Depends(current_driver_id)) -> str:
    """current_driver_id + a per-driver message limit. 429 when exceeded."""
    wait_s = chat_limiter().hit(driver_id)
    if wait_s:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many messages. Please wait a moment and try again.",
            headers={"Retry-After": str(math.ceil(wait_s))},
        )
    return driver_id


Conn = Depends(get_conn)
DriverId = Depends(current_driver_id)
ChatDriverId = Depends(chat_driver_id)