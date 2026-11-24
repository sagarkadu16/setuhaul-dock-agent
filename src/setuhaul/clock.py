"""Business clock: the single source of "now" for scheduling decisions.

Services never call datetime.now() directly; they receive a Clock.
Level 1 uses FixedClock (scenario time from APP_CLOCK).
Real-time operation will use SystemClock; see get_clock().
"""

from datetime import UTC, datetime
from typing import Protocol

from setuhaul.config import get_settings


class Clock(Protocol):
    def now(self) -> datetime: ...


class FixedClock:
    """Always returns the same instant. Used for scenarios and demos."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("Clock time must be timezone-aware")
        self._at = at

    def now(self) -> datetime:
        return self._at


class SystemClock:
    """Real wall-clock time, always timezone-aware (UTC).

    Convert to the facility's timezone at the edges (display, slot lookup),
    never by stripping tzinfo.
    """

    def now(self) -> datetime:
        return datetime.now(UTC)


def get_clock() -> Clock:
    # TODO(real-time): choose SystemClock when APP_CLOCK is unset.
    return FixedClock(get_settings().app_clock)
