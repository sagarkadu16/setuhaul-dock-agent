"""Shared fixtures (plan §4.1, §9): every test gets its own copy of the seed database
and starts at the default simulated clock."""

from __future__ import annotations

import shutil
from datetime import datetime

import pytest

from app.config import DEFAULT_CLOCK, IST, clock
from app.db import build_db, connect, connect_readonly


@pytest.fixture(scope="session")
def seed_db_path(tmp_path_factory):
    """Build the database once per test run."""
    return build_db(tmp_path_factory.mktemp("seed") / "seed.db")


@pytest.fixture
def db_path(seed_db_path, tmp_path):
    """A private copy of the seed database for one test."""
    path = tmp_path / "app.db"
    shutil.copyfile(seed_db_path, path)
    return path


@pytest.fixture
def db(db_path):
    conn = connect(db_path)
    yield conn
    conn.close()


@pytest.fixture
def ro_db(db_path):
    """Read-only connection, as the scheduling layer uses in the app."""
    conn = connect_readonly(db_path)
    yield conn
    conn.close()


def at(hhmm: str) -> datetime:
    """`at("11:20")` → 2026-08-04 11:20 IST, the snapshot day."""
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 8, 4, hour, minute, tzinfo=IST)


@pytest.fixture(autouse=True)
def fixed_clock():
    """Reset the simulated clock before and after every test. Use `fixed_clock.set(...)`
    to move it, e.g. to 16:25 for AC11."""
    clock.set(DEFAULT_CLOCK)
    yield clock
    clock.set(DEFAULT_CLOCK)
