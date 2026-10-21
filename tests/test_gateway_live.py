"""Live gateway test against the real Ollama (design 7.4).

Ollama is reachable in this environment, so this RUNS. It still guards with a
probe of ``GET {OLLAMA_BASE_URL}/api/tags`` and ``pytest.skip`` if Ollama is
down, so the suite stays green on a machine without a model server.

Coverage:
  * a free-form ``/quick`` returns non-empty text,
  * a schema-constrained call returns valid JSON, exercising the one-retry
    path (attempts<=2, eventual valid=true OR a clean structured error),
  * the last ``gateway/logs/calls.jsonl`` line carries ``num_ctx==8192`` and
    the full field set (model_id, prompt_sha256, eval_count,
    prompt_eval_count, latency_ms, valid).

The gateway core (``gateway.client``) is exercised directly; the FastAPI app is
additionally smoke-tested through ``TestClient`` to prove the HTTP route wiring.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway import client
from gateway.app import app
from gateway.settings import settings


def _ollama_up() -> bool:
    """Probe GET {OLLAMA_BASE_URL}/api/tags; True only on a clean 200."""
    try:
        resp = httpx.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=5.0)
        return resp.status_code == 200
    except httpx.RequestError:
        return False


pytestmark = pytest.mark.skipif(
    not _ollama_up(),
    reason="Ollama not reachable at the configured OLLAMA_BASE_URL",
)

# A small, unambiguous JSON schema for the structured-output path.
_SIGNAL_SCHEMA = {
    "type": "object",
    "required": ["signal", "confidence"],
    "properties": {
        "signal": {"type": "string", "enum": ["BUY", "HOLD", "SELL"]},
        "confidence": {"type": "number"},
    },
    "additionalProperties": False,
}

_REQUIRED_LOG_FIELDS = (
    "ts",
    "route",
    "model_id",
    "prompt_sha256",
    "num_ctx",
    "eval_count",
    "prompt_eval_count",
    "latency_ms",
    "attempts",
    "valid",
    "error",
)


def _last_log_line() -> dict:
    """Return the last JSON record from the gateway call log."""
    with open(settings.GATEWAY_LOG_PATH, encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    assert lines, "expected at least one gateway call log line"
    return json.loads(lines[-1])


def test_free_form_quick_returns_text():
    """A free-form /quick generation returns non-empty text and logs fully."""
    resp = client.generate_sync(
        "Reply with a single short sentence about moving averages.",
        route="quick",
    )
    assert resp["valid"] is True
    assert resp["text"].strip() != ""
    assert resp["error"] is None

    rec = _last_log_line()
    for field in _REQUIRED_LOG_FIELDS:
        assert field in rec, f"missing log field {field!r}"
    assert rec["num_ctx"] == 8192
    assert rec["route"] == "quick"
    assert rec["model_id"] == settings.GATEWAY_MODEL
    assert isinstance(rec["prompt_sha256"], str) and len(rec["prompt_sha256"]) == 64
    assert rec["latency_ms"] is not None


def test_schema_constrained_returns_valid_json_or_clean_error():
    """A schema-constrained call either validates (valid=true) or returns the
    structured error, with attempts in the one-retry envelope (<=2)."""
    resp = client.generate_sync(
        "Decide a trading signal. Return JSON with keys 'signal' "
        "(BUY, HOLD or SELL) and 'confidence' (0..1).",
        route="deep",
        json_schema=_SIGNAL_SCHEMA,
        temperature=0.1,
    )

    assert resp["attempts"] <= 2
    if resp["valid"]:
        # Eventual valid=true: the text parses and matches the schema.
        obj = json.loads(resp["text"])
        assert obj["signal"] in {"BUY", "HOLD", "SELL"}
        assert resp["error"] is None
    else:
        # Clean structured error after the single retry — never a crash.
        assert resp["error"] == "schema_validation_failed_after_retry"

    rec = _last_log_line()
    for field in _REQUIRED_LOG_FIELDS:
        assert field in rec, f"missing log field {field!r}"
    assert rec["num_ctx"] == 8192
    assert rec["attempts"] <= 2
    assert rec["eval_count"] is not None
    assert rec["prompt_eval_count"] is not None


def test_quick_route_via_fastapi_app():
    """The FastAPI /quick route is wired to the same core and logs a call."""
    with TestClient(app) as http:
        resp = http.post(
            "/quick",
            json={"prompt": "Say the word ready and nothing else."},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["text"].strip() != ""

    rec = _last_log_line()
    assert rec["num_ctx"] == 8192
    assert rec["route"] == "quick"
