import pytest
from pydantic import ValidationError

from setuhaul.api.main import api_docs_urls
from setuhaul.config import get_settings


@pytest.fixture(autouse=True)
def fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("app_env", ["staging", "prod"])
def test_deployed_env_refuses_local_login(monkeypatch, app_env):
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("AUTH_MODE", "local")
    with pytest.raises(ValidationError, match="requires AUTH_MODE=cognito"):
        get_settings()


def test_dev_may_use_local_login(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("AUTH_MODE", "local")
    assert get_settings().auth_mode == "local"


def test_docs_only_in_dev():
    assert api_docs_urls("dev") == {}
    assert api_docs_urls("prod") == {"docs_url": None, "redoc_url": None, "openapi_url": None}