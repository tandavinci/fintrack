"""The server-free gateway core (design 2.4, 2.5, 2.6).

This module is the ONLY component that talks to Ollama, and the single
enforcement point for ``num_ctx`` (design 0004 §3). It has NO FastAPI import
so quant/deep can call it in-process without a running server.

Public entry points:
  * ``generate_sync`` — plain synchronous /quick + /deep generation.
  * ``validate``      — the one deterministic schema validator.
  * ``embed``         — embeddings with a deterministic hash fallback.

Concurrency (design 2.5): a loop-agnostic ``threading`` priority gate — NOT
``asyncio`` — so sync callers and the async FastAPI routes (which offload via
``anyio.to_thread.run_sync``) share one gate instance and serialize against
each other. ``generate_sync`` is an ordinary synchronous function with no
per-call event loop, so calling it twice in-process cannot raise the old
``asyncio.run``-per-call "bound to a different event loop" error.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
import time
from typing import Any, Literal

import httpx
import jsonschema

from gateway.logging_jsonl import log_call, prompt_sha256
from gateway.settings import settings

# Single enforcement point for the hard num_ctx cap (design 0004 §3).
NUM_CTX_HARD_CAP = 8192

# Deterministic placeholder embedding dimension (design 2.6, named constant).
HASH_EMBED_DIM = 256

_TOKEN_SPLIT = re.compile(r"\W+")


class GatewayUnavailable(Exception):
    """Raised when Ollama is unreachable (connection error / timeout)."""


class GatewayHTTPError(Exception):
    """Raised when Ollama returns a 4xx/5xx HTTP status."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"ollama_http_{status_code}")


# ---------------------------------------------------------------------------
# Priority gate (design 2.5) — one module-level instance shared by every path.
# ---------------------------------------------------------------------------
# _ollama_lock serializes the actual HTTP call to Ollama (one in-flight
# generation at a time). _priority_cv guards _quick_waiting so /deep yields
# while any /quick is queued or in flight (boundary-level preemption — true
# mid-generation preemption is impossible with Ollama's HTTP API and is out of
# scope; deep generations are short on a 3B model).
_ollama_lock = threading.Lock()
_priority_cv = threading.Condition()
_quick_waiting = 0


def _effective_num_ctx() -> int:
    """The single num_ctx enforcement point: min(configured, hard cap)."""
    return min(settings.GATEWAY_NUM_CTX, NUM_CTX_HARD_CAP)


# ---------------------------------------------------------------------------
# Validation (design 2.4) — one deterministic validator.
# ---------------------------------------------------------------------------
def validate(text: str, json_schema: dict) -> tuple[bool, str | None]:
    """Validate ``text`` as JSON against ``json_schema``.

    Returns ``(True, None)`` on success, otherwise ``(False, reason)`` with
    reason ``"json_parse_error"`` or ``"schema_validation_failed"``.
    """
    import json

    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return (False, "json_parse_error")
    try:
        jsonschema.validate(obj, json_schema)
    except jsonschema.ValidationError:
        return (False, "schema_validation_failed")
    return (True, None)


# ---------------------------------------------------------------------------
# Ollama HTTP call (design 2.4, 2.8).
# ---------------------------------------------------------------------------
def _ollama_generate(
    prompt: str,
    *,
    system: str | None,
    json_schema: dict | None,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    """One POST to Ollama's /api/generate. Raises GatewayUnavailable /
    GatewayHTTPError on transport/status failures."""
    options: dict[str, Any] = {
        # Single enforcement point for the hard num_ctx cap (design 0004 §3).
        "num_ctx": _effective_num_ctx(),
        "temperature": temperature,
        "num_predict": max_tokens,
    }
    payload: dict[str, Any] = {
        "model": settings.GATEWAY_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": options,
    }
    if system is not None:
        payload["system"] = system
    if json_schema is not None:
        # Ollama structured-output mode: format = the JSON schema itself.
        payload["format"] = json_schema

    url = f"{settings.OLLAMA_BASE_URL}/api/generate"
    try:
        resp = httpx.post(url, json=payload, timeout=settings.GATEWAY_REQUEST_TIMEOUT_S)
    except httpx.RequestError as exc:  # connection error / timeout
        raise GatewayUnavailable(str(exc)) from exc
    if resp.status_code >= 400:
        raise GatewayHTTPError(resp.status_code)
    return resp.json()


def _generate_locked(
    route: Literal["quick", "deep"],
    prompt: str,
    system: str | None,
    json_schema: dict | None,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    """Run the generation (one-retry-then-structured-error, design 2.4) while
    holding ``_ollama_lock``. Returns a response dict matching
    ``GenerateResponse`` fields."""
    phash = prompt_sha256(prompt)
    num_ctx = _effective_num_ctx()
    model_id = settings.GATEWAY_MODEL
    max_attempts = 1 + max(0, settings.GATEWAY_MAX_RETRIES)

    text = ""
    eval_count = 0
    prompt_eval_count = 0
    attempts = 0
    valid = False
    error: str | None = None
    last_reason: str | None = None

    start = time.perf_counter()
    try:
        current_prompt = prompt
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            try:
                data = _ollama_generate(
                    current_prompt,
                    system=system,
                    json_schema=json_schema,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except GatewayUnavailable:
                latency_ms = int((time.perf_counter() - start) * 1000)
                log_call(
                    settings.GATEWAY_LOG_PATH,
                    route=route,
                    model_id=model_id,
                    prompt_sha256=phash,
                    num_ctx=num_ctx,
                    eval_count=None,
                    prompt_eval_count=None,
                    latency_ms=latency_ms,
                    attempts=attempts,
                    valid=False,
                    error="ollama_unreachable",
                )
                raise
            except GatewayHTTPError as exc:
                latency_ms = int((time.perf_counter() - start) * 1000)
                log_call(
                    settings.GATEWAY_LOG_PATH,
                    route=route,
                    model_id=model_id,
                    prompt_sha256=phash,
                    num_ctx=num_ctx,
                    eval_count=None,
                    prompt_eval_count=None,
                    latency_ms=latency_ms,
                    attempts=attempts,
                    valid=False,
                    error=f"ollama_http_{exc.status_code}",
                )
                raise

            text = data.get("response", "") or ""
            eval_count = int(data.get("eval_count") or 0)
            prompt_eval_count = int(data.get("prompt_eval_count") or 0)

            if json_schema is None:
                # Free-form: non-empty text is valid.
                if text.strip():
                    valid, error = True, None
                else:
                    valid, error = False, "empty_response"
                break

            ok, reason = validate(text, json_schema)
            if ok:
                valid, error = True, None
                break
            last_reason = reason
            valid, error = False, reason
            if attempt < max_attempts:
                # One retry with a terse corrective instruction.
                current_prompt = (
                    prompt
                    + "\n\nReturn ONLY valid JSON matching the schema; "
                    "previous output failed validation."
                )

        if json_schema is not None and not valid:
            # Still invalid after the retry: structured error, raw text kept.
            error = "schema_validation_failed_after_retry"

        latency_ms = int((time.perf_counter() - start) * 1000)
        log_call(
            settings.GATEWAY_LOG_PATH,
            route=route,
            model_id=model_id,
            prompt_sha256=phash,
            num_ctx=num_ctx,
            eval_count=eval_count,
            prompt_eval_count=prompt_eval_count,
            latency_ms=latency_ms,
            attempts=attempts,
            valid=valid if json_schema is not None else (valid if error is None else valid),
            error=error,
        )
    except (GatewayUnavailable, GatewayHTTPError):
        raise

    _ = last_reason  # retained for clarity; folded into `error` above.
    return {
        "text": text,
        "valid": valid,
        "model_id": model_id,
        "prompt_hash": phash,
        "eval_count": eval_count,
        "prompt_eval_count": prompt_eval_count,
        "latency_ms": latency_ms,
        "attempts": attempts,
        "error": error,
    }


def generate_sync(
    prompt: str,
    *,
    route: Literal["quick", "deep"] = "quick",
    system: str | None = None,
    json_schema: dict | None = None,
    temperature: float = 0.1,
    max_tokens: int = 512,
) -> dict[str, Any]:
    """Synchronous /quick + /deep generation with the priority gate.

    ``route="quick"`` registers as a high-priority waiter so a queued /quick
    drains before /deep acquires the Ollama lock. This is the public
    in-process entry point used by quant and deep (no server required).
    """
    global _quick_waiting

    if route == "quick":
        with _priority_cv:
            _quick_waiting += 1
            _priority_cv.notify_all()
        try:
            with _ollama_lock:
                return _generate_locked(
                    route, prompt, system, json_schema, temperature, max_tokens
                )
        finally:
            with _priority_cv:
                _quick_waiting -= 1
                _priority_cv.notify_all()
    else:
        # /deep: yield while any /quick is queued or in flight.
        with _priority_cv:
            _priority_cv.wait_for(lambda: _quick_waiting == 0)
        with _ollama_lock:
            return _generate_locked(
                route, prompt, system, json_schema, temperature, max_tokens
            )


# Alias so callers can use `generate` or `generate_sync` interchangeably.
generate = generate_sync


# ---------------------------------------------------------------------------
# Embeddings (design 2.6).
# ---------------------------------------------------------------------------
def _hash_embed_one(text: str) -> list[float]:
    """Deterministic hashing-trick embedding, L2-normalized (design 2.6).

    STOPGAP: this is a feature-hashing placeholder for offline pipeline wiring
    and local tests, NOT a semantic embedding. Step 1.3/2 replaces it with a
    real embedding model / pgvector population (docs/status.md).
    """
    vec = [0.0] * HASH_EMBED_DIM
    tokens = [t for t in _TOKEN_SPLIT.split(text.lower()) if t]
    for tok in tokens:
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:8], "big") % HASH_EMBED_DIM
        # Sign from a separate byte keeps the trick's +/- balance stable.
        sign = 1.0 if digest[8] % 2 == 0 else -1.0
        vec[bucket] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def embed(input: str | list[str]) -> dict[str, Any]:
    """Return embeddings for ``input`` (design 2.6).

    Tries Ollama's ``POST /api/embed`` first (``method="ollama"``, native dim).
    On any failure falls back to the deterministic hash placeholder
    (``method="hash_placeholder"``, ``dim=256``). The response always carries
    both ``dim`` and ``method``; callers must branch on ``method`` and must not
    assume one dimension across methods.
    """
    texts = [input] if isinstance(input, str) else list(input)
    model_id = settings.GATEWAY_MODEL
    url = f"{settings.OLLAMA_BASE_URL}/api/embed"

    try:
        resp = httpx.post(
            url,
            json={"model": model_id, "input": texts},
            timeout=settings.GATEWAY_REQUEST_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings")
        if not embeddings or not embeddings[0]:
            raise ValueError("empty embeddings")
        dim = len(embeddings[0])
        log_call(
            settings.GATEWAY_LOG_PATH,
            route="embed",
            model_id=model_id,
            prompt_sha256=prompt_sha256("".join(texts)),
            num_ctx=None,
            eval_count=None,
            prompt_eval_count=None,
            latency_ms=None,
            attempts=1,
            valid=True,
            error=None,
        )
        return {
            "embeddings": [[float(x) for x in v] for v in embeddings],
            "dim": dim,
            "method": "ollama",
            "model_id": model_id,
        }
    except Exception:
        embeddings = [_hash_embed_one(t) for t in texts]
        log_call(
            settings.GATEWAY_LOG_PATH,
            route="embed",
            model_id=model_id,
            prompt_sha256=prompt_sha256("".join(texts)),
            num_ctx=None,
            eval_count=None,
            prompt_eval_count=None,
            latency_ms=None,
            attempts=1,
            valid=True,
            error=None,
        )
        return {
            "embeddings": embeddings,
            "dim": HASH_EMBED_DIM,
            "method": "hash_placeholder",
            "model_id": None,
        }
