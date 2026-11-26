"""Build data/app.db from the read-only seed plus a scenario.

Usage: uv run python scripts/load_scenario.py two_drivers_one_slot
"""

import argparse
import sys

from pydantic import ValidationError

from setuhaul.db.connection import open_db
from setuhaul.db.scenario import ScenarioError, load_scenario, read_scenario


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenario", help="scenario name, e.g. two_drivers_one_slot")
    args = parser.parse_args()

    try:
        db_path = load_scenario(args.scenario)
    except (ScenarioError, ValidationError) as exc:
        print(f"Scenario load failed:\n{exc}", file=sys.stderr)
        return 1

    scenario = read_scenario(args.scenario)
    with open_db(db_path, read_only=True) as db:
        users = db.execute(
            "SELECT u.email, u.cognito_sub, d.driver_id, d.driver_name FROM app_users u "
            "JOIN drivers d USING (driver_id) ORDER BY u.email"
        ).fetchall()
        blocked = db.execute(
            "SELECT slot_id, block_reason FROM appointment_slots "
            "WHERE block_reason LIKE 'Scenario:%'"
        ).fetchall()

    print(f"Loaded '{scenario.name}' -> {db_path}")
    print(f"  clock: {scenario.clock.isoformat()}")
    for row in blocked:
        print(f"  blocked: {row['slot_id']} ({row['block_reason']})")
    for row in users:
        print(f"  user: {row['email']} -> {row['driver_id']} {row['driver_name']} (cognito_sub={row['cognito_sub']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
