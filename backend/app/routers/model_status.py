"""Read-only Model Status endpoint (Step 5 / G5 "Model Status" page).

``GET /api/model/status`` summarizes the local-LLM gateway's JSONL call log
(``gateway/logs/calls.jsonl``) and probes Ollama liveness. It is deliberately:
  * read-only — it reads the log tail + pings Ollama; it never writes;
  * additive — a new router file; it does not modify any existing router;
  * resilient — a missing log reads as empty, and the Ollama probe is wrapped
    so a down/unreachable Ollama reports ``ollama_up: false`` rather than
    failing the endpoint.

The JSONL-summary computation is factored into the PURE helper
:func:`summarize_calls` (a list of parsed log dicts -> a summary dict) so it is
unit-testable offline with no file/network. The route only does the I/O (read
the tail, probe Ollama) and wraps the pure summary in the response model.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import anyio
import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from gateway.settings import settings as gateway_settings

router = APIRouter(prefix="/api/model", tags=["model"])

# How many trailing log lines to summarize (keeps the read bounded + cheap).
_LOG_TAIL_N = 200
# The hard num_ctx cap enforced in gateway/client.py (design 0004 §3). Reported
# so the UI shows the ACTUAL ceiling, independent of the requested value.
_NUM_CTX_CAP = 8192
# Routes we break the call counts out by (anything else lands in "other").
_KNOWN_ROUTES = ("quick", "deep", "embed", "chat")
# Short timeout for the liveness probe — this endpoint must stay snappy.
_OLLAMA_PROBE_TIMEOUT_S = 2.0


class RouteCount(BaseModel):
    """Call count for one route label."""

    route: str
    count: int


class CallSummary(BaseModel):
    """Pure summary of the gateway call log (no I/O; see :func:`summarize_calls`)."""

    total_calls: int
    by_route: list[RouteCount]
    valid_count: int
    invalid_count: int
    valid_rate: float | None  # None when there are no classified calls
    avg_latency_ms: float | None  # None when no call had a latency
    max_latency_ms: int | None
    model_id: str | None  # the most recent non-null model_id seen


class ModelStatusResponse(BaseModel):
    """The full Model Status payload: liveness + config + log summary."""

    ollama_up: bool
    ollama_base_url: str
    configured_model: str
    num_ctx_cap: int
    log_path: str
    summary: CallSummary


def _coerce_latency(value: Any) -> float | None:
    """Return ``value`` as a float latency, or None when absent/non-numeric."""
    if value is None:
        return None
    if isinstance(value, bool):  # guard: bool is an int subclass
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def summarize_calls(records: list[dict]) -> CallSummary:
    """Compute the call-log summary from a list of parsed log dicts (PURE).

    * ``total_calls`` — len(records).
    * ``by_route`` — counts grouped by the ``route`` field; unknown/missing
      routes are bucketed under ``"other"``. Reported in a stable order
      (quick, deep, embed, chat, then any extras alphabetically).
    * ``valid_count`` / ``invalid_count`` — ``valid is True`` vs ``valid is
      False`` (a None/absent ``valid`` counts as neither).
    * ``valid_rate`` — ``valid / (valid + invalid)`` over CLASSIFIED calls, or
      None when nothing is classified.
    * ``avg_latency_ms`` / ``max_latency_ms`` — mean/max over the calls that
      carry a numeric ``latency_ms`` (None entries are ignored); both None when
      no call has a latency.
    * ``model_id`` — the most recent (last-in-list) non-null ``model_id``.

    An empty ``records`` list yields all-zero counts and None rates/latencies.
    """
    total = len(records)

    route_counter: Counter[str] = Counter()
    valid_count = 0
    invalid_count = 0
    latencies: list[float] = []
    model_id: str | None = None

    for rec in records:
        route = rec.get("route")
        route_counter[route if route in _KNOWN_ROUTES else "other"] += 1

        valid = rec.get("valid")
        if valid is True:
            valid_count += 1
        elif valid is False:
            invalid_count += 1

        latency = _coerce_latency(rec.get("latency_ms"))
        if latency is not None:
            latencies.append(latency)

        mid = rec.get("model_id")
        if mid:
            model_id = mid  # keep the most recent non-null id (records in order)

    classified = valid_count + invalid_count
    valid_rate = (valid_count / classified) if classified else None

    avg_latency = (sum(latencies) / len(latencies)) if latencies else None
    max_latency = int(max(latencies)) if latencies else None

    # Stable route ordering: known routes first (in declared order), then any
    # extras (e.g. "other") alphabetically — only those actually present.
    ordered_routes = [r for r in _KNOWN_ROUTES if r in route_counter]
    ordered_routes += sorted(r for r in route_counter if r not in _KNOWN_ROUTES)
    by_route = [RouteCount(route=r, count=route_counter[r]) for r in ordered_routes]

    return CallSummary(
        total_calls=total,
        by_route=by_route,
        valid_count=valid_count,
        invalid_count=invalid_count,
        valid_rate=round(valid_rate, 4) if valid_rate is not None else None,
        avg_latency_ms=round(avg_latency, 1) if avg_latency is not None else None,
        max_latency_ms=max_latency,
        model_id=model_id,
    )


def _read_log_tail(log_path: str, n: int = _LOG_TAIL_N) -> list[dict]:
    """Read the last ``n`` JSONL lines of ``log_path`` as parsed dicts.

    A missing file reads as an empty list (the log simply hasn't been written
    yet). Malformed lines are skipped defensively so one bad line can't break
    the whole page. Returns records in chronological (file) order.
    """
    try:
        with open(log_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        return []
    except OSError:
        return []

    records: list[dict] = []
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records


def _probe_ollama_sync(base_url: str) -> bool:
    """Blocking GET {base_url}/api/tags; True only on a 2xx. Never raises."""
    try:
        resp = httpx.get(
            f"{base_url.rstrip('/')}/api/tags", timeout=_OLLAMA_PROBE_TIMEOUT_S
        )
        return resp.is_success
    except Exception:  # noqa: BLE001 — a down Ollama is a normal state, not an error
        return False


@router.get(
    "/status",
    response_model=ModelStatusResponse,
    summary="Local-LLM gateway call-log summary + Ollama liveness (read-only)",
)
async def get_model_status() -> ModelStatusResponse:
    """Summarize the gateway call log and report Ollama liveness (read-only).

    The log read is cheap but is still offloaded to a worker thread alongside
    the blocking Ollama probe so neither touches the event loop. A down Ollama
    yields ``ollama_up: false`` — it never fails the endpoint.
    """
    log_path = gateway_settings.GATEWAY_LOG_PATH
    base_url = gateway_settings.OLLAMA_BASE_URL

    records = await anyio.to_thread.run_sync(_read_log_tail, log_path, _LOG_TAIL_N)
    ollama_up = await anyio.to_thread.run_sync(_probe_ollama_sync, base_url)

    return ModelStatusResponse(
        ollama_up=ollama_up,
        ollama_base_url=base_url,
        configured_model=gateway_settings.GATEWAY_MODEL,
        num_ctx_cap=_NUM_CTX_CAP,
        log_path=log_path,
        summary=summarize_calls(records),
    )
