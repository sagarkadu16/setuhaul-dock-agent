"""Login by email and linking to a driver by phone (spec R1-R5, plan §5.1).

Email login is identification, not security (spec A1). Links are permanent (R4b): there is
deliberately no function here that unlinks or relinks.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass

from email_validator import EmailNotValidError, validate_email

from app.config import format_ts, wall_now


class IdentityError(Exception):
    """Base class; every message is safe to show to the user."""


class InvalidEmail(IdentityError):
    pass


class NoSuchDriver(IdentityError):
    pass


class DriverAlreadyLinked(IdentityError):
    pass


class AlreadyLinked(IdentityError):
    pass


class NotLinked(IdentityError):
    pass


class UnknownUser(IdentityError):
    pass


@dataclass(frozen=True)
class User:
    user_id: str
    email: str
    driver_id: str | None
    driver_name: str | None

    @property
    def linked(self) -> bool:
        return self.driver_id is not None


def normalise_email(raw: str) -> str:
    """Trim, check the format (no DNS lookup) and lower-case the whole address (R2, R3)."""
    candidate = (raw or "").strip()
    try:
        return validate_email(candidate, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        raise InvalidEmail("That doesn't look like an email address. Please check and try again.") from None


def _phone_key(phone: str) -> str | None:
    """Last 10 digits, ignoring spaces, dashes and any +91 prefix (R4)."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else None


_USER_SQL = """
SELECT u.user_id, u.email, u.driver_id, d.driver_name
FROM app_users u LEFT JOIN drivers d ON d.driver_id = u.driver_id
"""


def _row_to_user(row: sqlite3.Row) -> User:
    return User(row["user_id"], row["email"], row["driver_id"], row["driver_name"])


def get_user(conn: sqlite3.Connection, user_id: str) -> User:
    row = conn.execute(_USER_SQL + " WHERE u.user_id = ?", (user_id,)).fetchone()
    if row is None:
        raise UnknownUser("Please log in again.")
    return _row_to_user(row)


def login(conn: sqlite3.Connection, email: str) -> User:
    """Return the user for this email, creating it on first use (R1, R2)."""
    email = normalise_email(email)
    conn.execute(
        "INSERT INTO app_users (user_id, email, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(email) DO NOTHING",
        (f"USR-{uuid.uuid4().hex[:8]}", email, format_ts(wall_now())),
    )
    return _row_to_user(conn.execute(_USER_SQL + " WHERE u.email = ?", (email,)).fetchone())


def link_phone(conn: sqlite3.Connection, user_id: str, phone: str) -> User:
    """Link the user to the driver with this phone number (R4). One driver per email (R4a);
    a user who is already linked cannot change it (R4b)."""
    key = _phone_key(phone)
    drivers = conn.execute("SELECT driver_id, phone FROM drivers").fetchall()
    match = next((d["driver_id"] for d in drivers if key and _phone_key(d["phone"]) == key), None)
    if match is None:
        raise NoSuchDriver("We couldn't find a driver with that phone number.")

    conn.execute("BEGIN IMMEDIATE")
    try:
        user = get_user(conn, user_id)
        if user.linked:
            raise AlreadyLinked("Your email is already linked to a driver. Please contact dispatch to change it.")
        try:
            conn.execute(
                "UPDATE app_users SET driver_id = ?, linked_at = ? WHERE user_id = ? AND driver_id IS NULL",
                (match, format_ts(wall_now()), user_id),
            )
        except sqlite3.IntegrityError:   # UNIQUE(driver_id): another email has this driver (R4a)
            raise DriverAlreadyLinked(
                "This driver is already linked to another email. Please contact dispatch."
            ) from None
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return get_user(conn, user_id)


def require_driver(user: User) -> str:
    """The user's driver ID; everything after login needs one (plan §5.1)."""
    if not user.linked:
        raise NotLinked("Please link your phone number first.")
    return user.driver_id
