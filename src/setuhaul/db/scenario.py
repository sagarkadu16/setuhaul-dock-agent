"""Scenario loader: seed DB + scenario YAML -> fresh working DB (data/app.db).

The seed file is opened read-only and copied; it is never modified.
The new DB is built in a temp file and swapped in only if every step succeeds.
"""

import os
import re
import sqlite3
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field

from setuhaul.config import PROJECT_ROOT, get_settings
from setuhaul.db.connection import open_db

SCENARIOS_DIR = PROJECT_ROOT / "scenarios"
APP_SCHEMA = Path(__file__).with_name("app_schema.sql")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalise_email(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL_RE.match(value):
        raise ValueError(f"not a valid email: {value!r}")
    return value


Email = Annotated[str, AfterValidator(_normalise_email)]


# --- Scenario file shape -----------------------------------------------------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BlockSlot(_Strict):
    slot_id: str
    reason: str = Field(min_length=1)


class ScenarioSetup(_Strict):
    block_slots: list[BlockSlot] = []


class ScenarioUser(_Strict):
    email: Email
    driver_phone: str


class Scenario(_Strict):
    name: str
    description: str
    clock: AwareDatetime
    facility_id: str
    setup: ScenarioSetup = ScenarioSetup()
    users: list[ScenarioUser] = Field(min_length=1)
    expect: dict[str, str] = {}


class ScenarioError(Exception):
    """The scenario cannot be applied to the seed data as written."""


# --- Loading -----------------------------------------------------------------

def read_scenario(name: str) -> Scenario:
    path = SCENARIOS_DIR / f"{name}.yaml"
    if not path.exists():
        available = sorted(p.stem for p in SCENARIOS_DIR.glob("*.yaml"))
        raise ScenarioError(f"Unknown scenario {name!r}. Available: {available}")
    scenario = Scenario.model_validate(yaml.safe_load(path.read_text()))
    if scenario.name != name:
        raise ScenarioError(f"File {path.name} declares name {scenario.name!r}")
    return scenario


def load_scenario(name: str) -> Path:
    settings = get_settings()
    scenario = read_scenario(name)

    if scenario.clock != settings.app_clock:
        raise ScenarioError(
            f"Scenario clock {scenario.clock.isoformat()} != APP_CLOCK "
            f"{settings.app_clock.isoformat()}. Update .env or the scenario."
        )

    target = settings.app_db_path
    tmp = target.with_name(target.name + ".loading")
    _remove_db_files(tmp)

    _copy_seed(settings.seed_db_path, tmp)
    try:
        with open_db(tmp) as conn:
            conn.execute("BEGIN")
            try:
                _apply_schema(conn)
                _block_slots(conn, scenario)
                _create_users(conn, scenario)
                _check_integrity(conn)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        _remove_db_files(tmp)
        raise

    # Swap in atomically. Old WAL/SHM files must go too, or SQLite would
    # replay the previous run's journal onto the new database.
    _remove_db_files(target)
    _remove_db_files(settings.checkpoint_db_path)  # chat memory refers to old state
    os.replace(tmp, target)
    _remove_db_files(tmp)
    return target


# --- Steps ---------------------------------------------------------------------

def _copy_seed(seed: Path, dest: Path) -> None:
    with open_db(seed, read_only=True) as src:
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()


def _apply_schema(conn: sqlite3.Connection) -> None:
    # Not executescript(): it silently COMMITs first, which would break our
    # all-or-nothing transaction. Keep ';' out of app_schema.sql comments.
    for statement in APP_SCHEMA.read_text().split(";"):
        if statement.strip():
            conn.execute(statement)


def _block_slots(conn: sqlite3.Connection, scenario: Scenario) -> None:
    for block in scenario.setup.block_slots:
        row = conn.execute(
            "SELECT facility_id, availability_status FROM v_slot_availability WHERE slot_id = ?",
            (block.slot_id,),
        ).fetchone()
        if row is None:
            raise ScenarioError(f"block_slots: unknown slot {block.slot_id}")
        if row["facility_id"] != scenario.facility_id:
            raise ScenarioError(f"block_slots: {block.slot_id} is not in {scenario.facility_id}")
        if row["availability_status"] != "AVAILABLE":
            raise ScenarioError(
                f"block_slots: {block.slot_id} is {row['availability_status']}, expected AVAILABLE"
            )
        conn.execute(
            "UPDATE appointment_slots SET slot_status = 'BLOCKED', block_reason = ? WHERE slot_id = ?",
            (block.reason, block.slot_id),
        )


def _create_users(conn: sqlite3.Connection, scenario: Scenario) -> None:
    created_at = scenario.clock.isoformat()
    for user in scenario.users:
        driver = conn.execute(
            "SELECT driver_id, driver_status FROM drivers WHERE phone = ?",
            (user.driver_phone,),
        ).fetchone()
        if driver is None:
            raise ScenarioError(f"users: no driver with phone {user.driver_phone}")
        if driver["driver_status"] != "ACTIVE":
            raise ScenarioError(f"users: driver {driver['driver_id']} is {driver['driver_status']}")
        conn.execute(
            "INSERT INTO app_users (email, driver_id, created_at) VALUES (?, ?, ?)",
            (user.email, driver["driver_id"], created_at),
        )


def _check_integrity(conn: sqlite3.Connection) -> None:
    problems = conn.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise ScenarioError(f"foreign key violations after load: {[tuple(p) for p in problems]}")


def _remove_db_files(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
