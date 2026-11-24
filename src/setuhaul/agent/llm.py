from langchain_openai import ChatOpenAI

from setuhaul.config import get_settings

LLM_TIMEOUT_S = 30
LLM_MAX_RETRIES = 2

def make_llm() -> ChatOpenAI:
    s = get_settings()
    return ChatOpenAI(
        model=s.llm_model,
        base_url=s.llm_base_url,
        api_key=s.llm_api_key,     # SecretStr: never printed in logs or reprs
        temperature=0,
        timeout=LLM_TIMEOUT_S,
        max_retries=LLM_MAX_RETRIES,
    )
