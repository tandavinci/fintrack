"""Gateway configuration (design 2.2).

pydantic-settings ``Settings`` with env-overridable fields. ``num_ctx`` is
hard-capped elsewhere (gateway/client.py) at 8192 regardless of what
``GATEWAY_NUM_CTX`` is set to — this object only supplies the *requested*
value; the single enforcement point lives in ``client.py`` (design 0004 §3).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    GATEWAY_MODEL: str = "llama3.2"
    GATEWAY_NUM_CTX: int = 8192
    GATEWAY_REQUEST_TIMEOUT_S: float = 120.0
    GATEWAY_MAX_RETRIES: int = 1
    GATEWAY_LOG_PATH: str = "gateway/logs/calls.jsonl"
    MAX_PROMPT_CHARS: int = 24000


settings = Settings()
