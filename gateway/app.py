"""FastAPI app for the gateway (design 2.3, 2.8).

Three async routes offload the blocking server-free core
(``gateway.client.generate_sync`` / ``embed``) to a worker thread via
``anyio.to_thread.run_sync`` so the event loop stays responsive. They import
and use the SAME module-level priority gate from ``gateway.client`` — this is
load-bearing: uvicorn /quick and an in-process /deep job must serialize
against each other through one gate instance.

Input validation (design 2.8) happens BEFORE any model call so a rejected
request produces NO log line.
"""

from __future__ import annotations

import anyio
from fastapi import FastAPI, HTTPException

from gateway import client
from gateway.schemas import (
    EmbedRequest,
    EmbedResponse,
    GenerateRequest,
    GenerateResponse,
)
from gateway.settings import settings

app = FastAPI(title="Local Trading Intelligence Gateway")


def _validate_generate_request(req: GenerateRequest) -> tuple[float, int]:
    """Design 2.8 input validation. Returns clamped (temperature, max_tokens).

    Raises HTTPException(422) for prompt_too_long / invalid_json_schema BEFORE
    any model call (no log line written for these)."""
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=422, detail="prompt_required")
    if len(req.prompt) > settings.MAX_PROMPT_CHARS:
        # No model call, no log line (design 2.8).
        raise HTTPException(status_code=422, detail="prompt_too_long")
    if req.json_schema is not None and not isinstance(req.json_schema, dict):
        raise HTTPException(status_code=422, detail="invalid_json_schema")
    temperature = max(0.0, min(req.temperature, 2.0))
    max_tokens = min(req.max_tokens, 2048)
    return temperature, max_tokens


async def _run_generate(route: str, req: GenerateRequest) -> GenerateResponse:
    temperature, max_tokens = _validate_generate_request(req)
    try:
        result = await anyio.to_thread.run_sync(
            lambda: client.generate_sync(
                req.prompt,
                route=route,  # type: ignore[arg-type]
                system=req.system,
                json_schema=req.json_schema,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        )
    except client.GatewayUnavailable:
        raise HTTPException(status_code=503, detail="ollama_unreachable")
    except client.GatewayHTTPError as exc:
        raise HTTPException(status_code=502, detail=f"ollama_http_{exc.status_code}")
    return GenerateResponse(**result)


@app.post("/quick", response_model=GenerateResponse)
async def quick(req: GenerateRequest) -> GenerateResponse:
    return await _run_generate("quick", req)


@app.post("/deep", response_model=GenerateResponse)
async def deep(req: GenerateRequest) -> GenerateResponse:
    # /deep default max_tokens is 1024 when the caller did not set it (design 2.3).
    if "max_tokens" not in req.model_fields_set:
        req.max_tokens = 1024
    return await _run_generate("deep", req)


@app.post("/embed", response_model=EmbedResponse)
async def embed(req: EmbedRequest) -> EmbedResponse:
    result = await anyio.to_thread.run_sync(lambda: client.embed(req.input))
    return EmbedResponse(**result)
