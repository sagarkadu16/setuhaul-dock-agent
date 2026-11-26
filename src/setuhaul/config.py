"""Typed application settings, loaded once from environment / .env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AwareDatetime, SecretStr, field_validator, model_validator, PositiveInt

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    @field_validator("seed_db_path", "app_db_path", "checkpoint_db_path")
    @classmethod
    def _anchor_to_project_root(cls, path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_ROOT / path
    
    @field_validator("session_secret")
    @classmethod
    def _secret_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("SESSION_SECRET must be at least 32 characters")
        return value


    # LLM
    llm_api_key: SecretStr
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "openai/gpt-4.1-mini"

    # Time: the scenario's "now". Must carry a UTC offset.
    app_clock: AwareDatetime
    decision_window_seconds: PositiveInt = 10

    # Abuse limits: each /chat message is an LLM call (costs money)
    chat_rate_limit: PositiveInt = 10           # messages per driver...
    chat_rate_window_seconds: PositiveInt = 60  # ...per this many seconds

        # Auth
    auth_mode: Literal["local", "cognito"] = "local"
    session_secret: SecretStr            # signs Level 1 tokens (AUTH_MODE=local)
    cognito_region: str = ""
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""

    @property
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"

    @property
    def cognito_jwks_url(self) -> str:
        return f"{self.cognito_issuer}/.well-known/jwks.json"

    @model_validator(mode="after")
    def _cognito_settings_present(self) -> "Settings":
        """In cognito mode, refuse to start unless the pool and client are configured."""
        if self.auth_mode != "cognito":
            return self
        required = {
            "COGNITO_REGION": self.cognito_region,
            "COGNITO_USER_POOL_ID": self.cognito_user_pool_id,
            "COGNITO_CLIENT_ID": self.cognito_client_id,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise ValueError(f"AUTH_MODE=cognito needs: {', '.join(missing)}")
        if not self.cognito_user_pool_id.startswith(self.cognito_region + "_"):
            raise ValueError("COGNITO_USER_POOL_ID must start with COGNITO_REGION + '_'")
        return self

    # Storage
    seed_db_path: Path = PROJECT_ROOT / "data/seed/setuhaul_freight_operations.db"
    app_db_path: Path = PROJECT_ROOT / "data/app.db"
    checkpoint_db_path: Path = PROJECT_ROOT / "data/checkpoints.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
