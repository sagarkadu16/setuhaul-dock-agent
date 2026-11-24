"""SQLite connections.

Every connection is configured the same way:
- rows behave like dicts (row["shipment_id"])
- foreign keys are enforced (SQLite has them OFF by default)
- writers wait up to BUSY_TIMEOUT_MS for a lock instead of failing instantly
- autocommit=True: no hidden transactions; callers open them explicitly
  (BEGIN IMMEDIATE for bookings, added in the booking step)
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from setuhaul.config import get_settings

BUSY_TIMEOUT_MS = 5_000


def connect(path: Path | None = None, *, read_only: bool = False) -> sqlite3.Connection:
    db_path = path or get_settings().app_db_path
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found: {db_path}\n"
            "Load a scenario first: uv run python scripts/load_scenario.py <scenario>"
        )

    mode = "ro" if read_only else "rw"
    conn = sqlite3.connect(
        f"file:{db_path}?mode={mode}",
        uri=True,
        autocommit=True,
        timeout=BUSY_TIMEOUT_MS / 1000,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    if not read_only:
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def open_db(path: Path | None = None, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
    """Open a connection and always close it, even on error."""
    conn = connect(path, read_only=read_only)
    try:
        yield conn
    finally:
        conn.close()

@contextmanager
def immediate_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT, or ROLLBACK on any error.

    IMMEDIATE takes the write lock *before* we read, so the check-then-write
    sequence inside cannot interleave with another writer.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")

@contextmanager
def savepoint(conn: sqlite3.Connection, name: str = "attempt") -> Iterator[sqlite3.Connection]:
    """A nested 'mini transaction' inside an open one. On error, undo only this part."""
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield conn
    except BaseException:
        conn.execute(f"ROLLBACK TO {name}")
        conn.execute(f"RELEASE {name}")
        raise
    else:
        conn.execute(f"RELEASE {name}")
