import sqlite3
from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, status

from setuhaul.db.connection import open_db
from setuhaul.services.identity import InvalidSession, read_token


def get_conn() -> Iterator[sqlite3.Connection]:
    """One connection per request, always closed afterwards."""
    with open_db() as conn:
        yield conn


def current_driver_id(authorization: str = Header(default="")) -> str:
    """driver_id from 'Authorization: Bearer <token>'. The ONLY source of identity."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in")
    try:
        return read_token(token)
    except InvalidSession as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e


Conn = Depends(get_conn)
DriverId = Depends(current_driver_id)