"""Phase 1 checks: clock (T1.2), database build (T1.4) and test isolation (T1.5)."""

from datetime import datetime

import pytest

from app.config import IST, clock, format_ts, parse_ts


def test_clock_defaults_to_snapshot_morning():
    assert format_ts(clock.now()) == "2026-08-04T09:40:00+05:30"


def test_clock_can_be_moved(fixed_clock):
    fixed_clock.set("2026-08-04T16:25:00+05:30")
    assert clock.now() == datetime(2026, 8, 4, 16, 25, tzinfo=IST)


def test_clock_rejects_time_without_offset():
    with pytest.raises(ValueError):
        clock.set("2026-08-04T16:25:00")
    with pytest.raises(ValueError):
        parse_ts("2026-08-04T16:25:00")


def test_database_has_seed_rows_and_empty_users(db):
    def count(table):
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    assert count("shipments") == 21
    assert count("appointment_slots") == 106
    assert count("appointments") == 20
    assert count("app_users") == 0


def test_foreign_keys_are_enforced(db):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO app_users (user_id, email, driver_id, created_at) "
            "VALUES ('USR-x', 'x@test.com', 'DRV999', '2026-08-04T09:40:00+05:30')"
        )


def test_driver_can_link_to_only_one_user(db):
    import sqlite3

    insert = ("INSERT INTO app_users (user_id, email, driver_id, created_at) "
              "VALUES (?, ?, 'DRV006', '2026-08-04T09:40:00+05:30')")
    db.execute(insert, ("USR-a", "a@test.com"))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(insert, ("USR-b", "b@test.com"))


# The next two tests write to the database. Each must see only its own row,
# whichever runs first, which proves every test gets a fresh copy.
@pytest.mark.parametrize("email", ["first@test.com", "second@test.com"])
def test_each_test_gets_a_fresh_database(db, email):
    db.execute(
        "INSERT INTO app_users (user_id, email, created_at) VALUES (?, ?, ?)",
        ("USR-" + email[:5], email, "2026-08-04T09:40:00+05:30"),
    )
    assert db.execute("SELECT COUNT(*) FROM app_users").fetchone()[0] == 1
