"""Who is talking (AUTH_MODE=cognito): Cognito access token -> verified `sub`.

Verification is local: Cognito's public keys (JWKS) are fetched once and cached,
so no AWS call happens per request.
"""

from functools import lru_cache

import jwt
from jwt import PyJWKClient
import sqlite3

from setuhaul.config import get_settings
from setuhaul.services.identity import InvalidSession
from setuhaul.db import queries
import logging

CLOCK_SKEW_S = 30  # tolerate small clock differences between Cognito and us

log = logging.getLogger(__name__)

class AuthUnavailable(Exception):
    """We could not fetch Cognito's public keys. Not the user's fault (-> 503, not 401)."""

class NotADriver(Exception):
    """Valid Cognito user, but not linked to an ACTIVE driver -> 403."""


@lru_cache
def _jwks_client() -> PyJWKClient:
    # cache_jwk_set + lifespan: refetch the key set at most hourly.
    # Unknown `kid` (Cognito rotated keys) -> one immediate refetch, then a
    # cooldown: junk kids can't make us hammer Cognito and get throttled (-> 503s).
    return PyJWKClient(
        get_settings().cognito_jwks_url,
        cache_jwk_set=True,
        lifespan=3600,
        cooldown_duration=30,
        timeout=5,
    )

def _reject(reason: str) -> InvalidSession:
    """Log WHY (for us), return a vague error (for the caller). Never log the token."""
    log.warning("Rejected access token: %s", reason)
    return InvalidSession("Invalid session")

def verify_access_token(token: str) -> str:
    """Return the Cognito `sub` of a valid access token, or raise InvalidSession."""
    settings = get_settings()
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=settings.cognito_issuer,
            leeway=CLOCK_SKEW_S,
            options={"require": ["exp", "iat", "iss", "sub", "token_use", "client_id"]},
        )
    except jwt.PyJWKClientConnectionError as exc:
        log.error("Cannot fetch Cognito JWKS: %s", type(exc).__name__)
        raise AuthUnavailable("Cannot reach Cognito to fetch signing keys") from exc
    except jwt.ExpiredSignatureError as exc:
        log.info("Rejected access token: expired")       # normal: happens every hour
        raise InvalidSession("Session expired, please log in again") from exc
    except jwt.PyJWTError as exc:
        raise _reject(type(exc).__name__) from exc

    if claims["token_use"] != "access":
        raise _reject("not an access token")
    if claims["client_id"] != settings.cognito_client_id:
        raise _reject("wrong client_id")

    return claims["sub"]


def driver_id_for_access_token(conn: sqlite3.Connection, token: str) -> str:
    """Verified Cognito access token -> driver_id. The Level 2 version of read_token()."""
    sub = verify_access_token(token)              # raises InvalidSession / AuthUnavailable
    driver = queries.driver_for_cognito_sub(conn, sub)
    if driver is None or driver.driver_status != "ACTIVE":
        log.warning("Cognito user %s is not linked to an ACTIVE driver", sub)
        raise NotADriver("This account is not linked to an active driver")
    return driver.driver_id