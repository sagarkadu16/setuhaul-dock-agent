"""Integrity proof: two drivers book the same slot at the same instant. Exactly one wins."""

import threading

from setuhaul.clock import get_clock
from setuhaul.db.connection import open_db
from setuhaul.db.scenario import load_scenario
from setuhaul.services.booking import SlotNotAvailable, book_slot

SCENARIO = "two_drivers_one_slot"
SLOT = "SLOT-JAI-019"
CONTENDERS = [("DRV006", "SHP1006"), ("DRV012", "SHP1012")]
ROUNDS = 20


def one_round() -> tuple[list[str], int]:
    now = get_clock().now()
    start = threading.Barrier(len(CONTENDERS))
    outcomes: list[str] = []

    def attempt(driver_id: str, shipment_id: str) -> None:
        with open_db() as conn:          # one connection per thread
            start.wait()                 # everyone fires together
            try:
                book_slot(conn, driver_id, shipment_id, SLOT, now)
                outcomes.append("won")
            except SlotNotAvailable:
                outcomes.append("refused")
            except Exception as exc:     # surface anything unexpected, don't hide it
                outcomes.append(f"error: {exc!r}")

    threads = [threading.Thread(target=attempt, args=c) for c in CONTENDERS]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with open_db(read_only=True) as conn:
        active = conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE slot_id = ? AND is_current = 1 "
            "AND appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')",
            (SLOT,),
        ).fetchone()[0]
    return sorted(outcomes), active


def main() -> None:
    for i in range(1, ROUNDS + 1):
        load_scenario(SCENARIO)
        outcomes, active = one_round()
        if outcomes != ["refused", "won"] or active != 1:
            raise SystemExit(f"FAIL round {i}: outcomes={outcomes} active={active}")
    load_scenario(SCENARIO)  # leave a clean DB behind
    print(f"OK: {ROUNDS} rounds, each had exactly 1 winner and 1 active appointment on {SLOT}")


if __name__ == "__main__":
    main()
