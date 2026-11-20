"""Settings from .env and the simulated clock (spec A3, plan §4.4)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
IST = timezone(timedelta(hours=5, minutes=30), name="IST")
DEFAULT_CLOCK = "2026-08-04T09:40:00+05:30"
DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"  # any OpenAI-compatible API works

load_dotenv(REPO_ROOT / ".env")


def parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp; it must carry a UTC offset."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp has no UTC offset: {value!r}")
    return dt


def format_ts(dt: datetime) -> str:
    """Format like the seed data: 2026-08-04T09:40:00+05:30."""
    return dt.astimezone(IST).isoformat(timespec="seconds")


def _path(env_name: str, default: str) -> Path:
    path = Path(os.getenv(env_name) or default)
    return path if path.is_absolute() else REPO_ROOT / path


@dataclass(frozen=True)
class Settings:
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    app_clock: datetime
    session_secret: str
    app_db_path: Path
    checkpoint_db_path: Path


def load_settings() -> Settings:
    return Settings(
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL") or DEFAULT_LLM_BASE_URL,
        llm_model=os.getenv("LLM_MODEL", ""),
        app_clock=parse_ts(os.getenv("APP_CLOCK") or DEFAULT_CLOCK),
        session_secret=os.getenv("SESSION_SECRET") or "change-me",
        app_db_path=_path("APP_DB_PATH", "data/app.db"),
        checkpoint_db_path=_path("CHECKPOINT_DB_PATH", "data/checkpoints.db"),
    )


settings = load_settings()


class Clock:
    """Scheduling time. Fixed, not the real clock (spec A3); tests may move it."""

    def __init__(self, fixed: datetime) -> None:
        self.set(fixed)

    def now(self) -> datetime:
        return self._fixed

    def set(self, value: datetime | str) -> None:
        dt = parse_ts(value) if isinstance(value, str) else value
        if dt.tzinfo is None:
            raise ValueError("clock time must carry a UTC offset")
        self._fixed = dt.astimezone(IST)


clock = Clock(settings.app_clock)


def wall_now() -> datetime:
    """Real time, used only for chat timestamps (plan D2)."""
    return datetime.now(IST)
