"""Request/response models for the three gateway routes (design 2.3).

These are the FastAPI wire models. The server-free core (gateway/client.py)
works with plain values and does not import these, keeping client.py generic.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    """Shared request shape for /quick and /deep (design 2.3)."""

    prompt: str
    json_schema: dict | None = None
    system: str | None = None
    temperature: float = 0.1
    max_tokens: int = 512


class GenerateResponse(BaseModel):
    """Shared response shape for /quick and /deep (design 2.3)."""

    text: str
    valid: bool
    model_id: str | None
    prompt_hash: str
    eval_count: int
    prompt_eval_count: int
    latency_ms: int
    attempts: int
    error: str | None = None


class EmbedRequest(BaseModel):
    input: str | list[str]


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    dim: int
    method: Literal["ollama", "hash_placeholder"]
    model_id: str | None = None
