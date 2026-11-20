"""The chat model (plan §2, task T4.1): any OpenAI-compatible API, OpenRouter by default."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.config import Settings, settings as default_settings

TIMEOUT_SECONDS = 30
MAX_RETRIES = 1


class ModelNotConfigured(RuntimeError):
    pass


def build_chat_model(cfg: Settings = default_settings) -> BaseChatModel:
    if not cfg.llm_api_key or not cfg.llm_model:
        raise ModelNotConfigured("Set LLM_API_KEY and LLM_MODEL in .env.")
    return ChatOpenAI(
        model=cfg.llm_model,
        api_key=cfg.llm_api_key,
        base_url=cfg.llm_base_url,
        timeout=TIMEOUT_SECONDS,
        max_retries=MAX_RETRIES,
        temperature=0.2,   # low: we want consistent, rule-following replies
    )
