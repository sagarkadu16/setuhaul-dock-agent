"""Database build and connections (plan §4.1).

Usage: python -m app.db reset
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from app.config import REPO_ROOT, settings

SEED_SQL = REPO_ROOT / "data" / "seed" / "setuhaul_schema_and_seed.sql"
MIGRATIONS_DIR = REPO_ROOT / "data" / "migrations"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection. Autocommit mode: callers open transactions explicitly
    (e.g. BEGIN IMMEDIATE for bookings, plan §6.4)."""
    conn = sqlite3.connect(path or settings.app_db_path, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def connect_readonly(path: Path | str | None = None) -> sqlite3.Connection:
    """Open a read-only connection. The scheduling layer uses only this, so it cannot change
    operational data even by mistake (spec R26, plan §1)."""
    uri = Path(path or settings.app_db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA query_only = ON")  # second guard, in case the file mode is ever changed
    return conn


def _remove(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def build_db(path: Path | str) -> Path:
    """Build a fresh database: seed data, then every migration in name order.
    Builds into a temp file and renames, so a failed build never leaves half a database."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".building")
    _remove(tmp)

    conn = sqlite3.connect(tmp, isolation_level=None)
    try:
        conn.executescript(SEED_SQL.read_text(encoding="utf-8"))
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.executescript(migration.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(f"foreign key problems after build: {problems[:5]}")
        conn.execute("PRAGMA journal_mode = WAL")  # readers don't block the booking writer
    finally:
        conn.close()

    _remove(path)
    os.replace(tmp, path)
    return path


def reset() -> None:
    build_db(settings.app_db_path)
    # Agent memory refers to conversations in app.db, so it goes too.
    _remove(settings.checkpoint_db_path)

    conn = connect()
    try:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("shipments", "appointment_slots", "appointments", "app_users")
        }
    finally:
        conn.close()
    path = settings.app_db_path
    shown = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
    print(f"Built {shown}: "
          + ", ".join(f"{n} {t}" for t, n in counts.items()))


if __name__ == "__main__":
    if sys.argv[1:] != ["reset"]:
        sys.exit("usage: python -m app.db reset")
    reset()
