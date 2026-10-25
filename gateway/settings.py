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
    # The gateway's own HTTP base URL. The deep adapter wires TradingAgents'
    # OpenAI-compatible client at ``GATEWAY_BASE_URL + "/v1"`` so every deep-lane
    # model call is routed through the gateway (STEERING rule 1 — the gateway is
    # the only path to Ollama), never at Ollama's :11434 directly.
    GATEWAY_BASE_URL: str = "http://localhost:8001"
    GATEWAY_MODEL: str = "llama3.2"
    GATEWAY_NUM_CTX: int = 8192
    # Per-call Ollama timeout. The deep lane's multi-agent prompts are large
    # (thousands of prompt tokens) and on modest CPU/iGPU hardware a single
    # generation can take ~3 minutes, so the default must exceed that or the
    # gateway reports a false ``ollama_unreachable`` while Ollama is still
    # working. 600s gives heavy deep calls room; quick/interactive calls return
    # long before it. Override via GATEWAY_REQUEST_TIMEOUT_S on faster hardware.
    GATEWAY_REQUEST_TIMEOUT_S: float = 600.0
    GATEWAY_MAX_RETRIES: int = 1
    GATEWAY_LOG_PATH: str = "gateway/logs/calls.jsonl"
    MAX_PROMPT_CHARS: int = 24000


settings = Settings()
