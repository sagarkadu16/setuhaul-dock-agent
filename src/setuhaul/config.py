"""Typed application settings, loaded once from environment / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import AwareDatetime, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AwareDatetime, SecretStr, field_validator, PositiveInt

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    @field_validator("seed_db_path", "app_db_path", "checkpoint_db_path")
    @classmethod
    def _anchor_to_project_root(cls, path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_ROOT / path

    # LLM
    llm_api_key: SecretStr
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "openai/gpt-4.1-mini"

    # Time: the scenario's "now". Must carry a UTC offset.
    app_clock: AwareDatetime
    decision_window_seconds: PositiveInt = 10

    # Auth
    session_secret: SecretStr

    # Storage
    seed_db_path: Path = PROJECT_ROOT / "data/seed/setuhaul_freight_operations.db"
    app_db_path: Path = PROJECT_ROOT / "data/app.db"
    checkpoint_db_path: Path = PROJECT_ROOT / "data/checkpoints.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
