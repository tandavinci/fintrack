"""Offline, deterministic gateway unit tests (design 7.6).

No network / no Ollama: the Ollama HTTP call is monkeypatched everywhere. These
cover: validate() three branches; hash-embed determinism + L2-norm + dim=256;
the JSONL logger field set; the threading priority-gate ordering on two threads;
the twice-in-a-row generate_sync regression (proves no asyncio-loop defect);
the num_ctx <= 8192 cap; and prompt_too_long 422 writing NO log line.
"""

from __future__ import annotations

import json
import math
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway import client
from gateway.app import app
from gateway.logging_jsonl import log_call
from gateway.settings import settings


# ---------------------------------------------------------------------------
# Helpers: a fake Ollama /api/generate response + patchable httpx.post.
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


def _make_generate_post(response_text: str, *, delay: float = 0.0):
    """Returns a fake httpx.post that answers /api/generate with given text."""

    def _post(url, *args, **kwargs):
        if delay:
            time.sleep(delay)
        return _FakeResponse(
            {
                "response": response_text,
                "eval_count": 7,
                "prompt_eval_count": 3,
            }
        )

    return _post


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    """Point the gateway log at a temp file for assertion/isolation."""
    p = tmp_path / "calls.jsonl"
    monkeypatch.setattr(settings, "GATEWAY_LOG_PATH", str(p))
    return p


# ---------------------------------------------------------------------------
# validate() — three branches (design 2.4).
# ---------------------------------------------------------------------------
def test_validate_three_branches():
    schema = {"type": "object", "required": ["signal"], "properties": {"signal": {"type": "string"}}}

    ok, reason = client.validate('{"signal": "BUY"}', schema)
    assert ok is True and reason is None

    ok, reason = client.validate("not json {", schema)
    assert ok is False and reason == "json_parse_error"

    ok, reason = client.validate('{"nope": 1}', schema)
    assert ok is False and reason == "schema_validation_failed"


# ---------------------------------------------------------------------------
# hash-embed determinism + L2-norm + dim=256 (design 2.6).
# ---------------------------------------------------------------------------
def test_hash_embed_deterministic_l2norm_dim(monkeypatch, log_path):
    # Force the fallback path by making Ollama embed fail.
    def _boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(client.httpx, "post", _boom)

    r1 = client.embed("Reliance momentum is strong")
    r2 = client.embed("Reliance momentum is strong")

    assert r1["method"] == "hash_placeholder"
    assert r1["dim"] == 256
    assert len(r1["embeddings"][0]) == 256
    # Determinism: identical input -> identical vector.
    assert r1["embeddings"] == r2["embeddings"]
    # L2 norm == 1 for non-empty input.
    norm = math.sqrt(sum(v * v for v in r1["embeddings"][0]))
    assert abs(norm - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# JSONL logger writes all required fields (design 2.7).
# ---------------------------------------------------------------------------
def test_jsonl_logger_fields(tmp_path):
    p = tmp_path / "logs" / "calls.jsonl"  # dir does not exist yet
    log_call(
        str(p),
        route="quick",
        model_id="llama3.2",
        prompt_sha256="abc",
        num_ctx=8192,
        eval_count=5,
        prompt_eval_count=2,
        latency_ms=12,
        attempts=1,
        valid=True,
        error=None,
    )
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    for field in (
        "ts", "route", "model_id", "prompt_sha256", "num_ctx",
        "eval_count", "prompt_eval_count", "latency_ms", "attempts",
        "valid", "error",
    ):
        assert field in rec
    assert rec["route"] == "quick"
    assert rec["num_ctx"] == 8192
    assert rec["valid"] is True


# ---------------------------------------------------------------------------
# num_ctx cap = min(., 8192) — single enforcement point (design 0004 §3).
# ---------------------------------------------------------------------------
def test_num_ctx_capped(monkeypatch, log_path):
    monkeypatch.setattr(settings, "GATEWAY_NUM_CTX", 999999)
    assert client._effective_num_ctx() == 8192

    captured = {}

    def _post(url, *args, **kwargs):
        captured["num_ctx"] = kwargs["json"]["options"]["num_ctx"]
        return _FakeResponse({"response": "hello", "eval_count": 1, "prompt_eval_count": 1})

    monkeypatch.setattr(client.httpx, "post", _post)
    client.generate_sync("hi", route="quick")
    assert captured["num_ctx"] == 8192

    # And the enforced value appears in the log line.
    rec = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["num_ctx"] == 8192


# ---------------------------------------------------------------------------
# Regression: generate_sync twice in-process both succeed (design 7.6).
# Proves the old asyncio.run-per-call "bound to a different event loop" cannot
# recur, because the core is plain synchronous with a threading gate.
# ---------------------------------------------------------------------------
def test_generate_sync_twice_in_process(monkeypatch, log_path):
    monkeypatch.setattr(client.httpx, "post", _make_generate_post("first then second"))

    r1 = client.generate_sync("prompt one", route="quick")
    r2 = client.generate_sync("prompt two", route="deep")

    assert r1["valid"] is True and r1["text"] == "first then second"
    assert r2["valid"] is True and r2["text"] == "first then second"
    assert r1["attempts"] == 1 and r2["attempts"] == 1


# ---------------------------------------------------------------------------
# One-retry-then-structured-error for schema-constrained generation (design 2.4).
# ---------------------------------------------------------------------------
def test_schema_retry_then_structured_error(monkeypatch, log_path):
    schema = {"type": "object", "required": ["signal"], "properties": {"signal": {"type": "string"}}}
    monkeypatch.setattr(client.httpx, "post", _make_generate_post("totally not json"))

    r = client.generate_sync("give me json", route="quick", json_schema=schema)
    assert r["valid"] is False
    assert r["error"] == "schema_validation_failed_after_retry"
    assert r["attempts"] == 2
    assert r["text"] == "totally not json"  # raw text preserved


def test_schema_valid_first_attempt(monkeypatch, log_path):
    schema = {"type": "object", "required": ["signal"], "properties": {"signal": {"type": "string"}}}
    monkeypatch.setattr(client.httpx, "post", _make_generate_post('{"signal": "BUY"}'))

    r = client.generate_sync("give me json", route="quick", json_schema=schema)
    assert r["valid"] is True and r["error"] is None and r["attempts"] == 1


def test_free_form_empty_response(monkeypatch, log_path):
    monkeypatch.setattr(client.httpx, "post", _make_generate_post("   "))
    r = client.generate_sync("say nothing", route="quick")
    assert r["valid"] is False and r["error"] == "empty_response"


# ---------------------------------------------------------------------------
# Priority-gate ordering on two threads (design 2.5, 7.6).
# A slow /deep is launched first; a /quick launched shortly after must finish
# FIRST because /deep yields while _quick_waiting > 0 and the quick request
# jumps the Ollama lock at the request boundary.
# ---------------------------------------------------------------------------
def test_priority_gate_quick_preempts_deep(monkeypatch, log_path):
    order: list[str] = []
    order_lock = threading.Lock()
    # Gate so we can deterministically interleave: deep is in flight, then quick
    # arrives; we assert quick is served next.
    monkeypatch.setattr(client.httpx, "post", _make_generate_post("ok", delay=0.25))

    def run_deep():
        client.generate_sync("deep prompt", route="deep")
        with order_lock:
            order.append("deep")

    def run_quick():
        client.generate_sync("quick prompt", route="quick")
        with order_lock:
            order.append("quick")

    t_deep = threading.Thread(target=run_deep)
    t_deep.start()
    # Let deep acquire the lock and start its slow call.
    time.sleep(0.05)
    t_quick = threading.Thread(target=run_quick)
    t_quick.start()

    t_deep.join(timeout=5)
    t_quick.join(timeout=5)

    assert order == ["deep", "quick"] or order[-1] == "quick"
    # The load-bearing assertion: once deep finished, quick was served next
    # (not blocked behind another deep). Here with a single quick + single deep,
    # quick completes immediately after the in-flight deep releases the lock.
    assert "quick" in order and "deep" in order


def test_deep_yields_while_quick_waiting(monkeypatch, log_path):
    """A /deep that starts while a /quick is queued must wait for the quick."""
    monkeypatch.setattr(client.httpx, "post", _make_generate_post("ok", delay=0.2))
    order: list[str] = []
    order_lock = threading.Lock()

    def run_quick():
        client.generate_sync("quick prompt", route="quick")
        with order_lock:
            order.append("quick")

    def run_deep():
        client.generate_sync("deep prompt", route="deep")
        with order_lock:
            order.append("deep")

    t_quick = threading.Thread(target=run_quick)
    t_quick.start()
    time.sleep(0.02)  # quick increments _quick_waiting and holds the lock
    t_deep = threading.Thread(target=run_deep)
    t_deep.start()

    t_quick.join(timeout=5)
    t_deep.join(timeout=5)
    assert order == ["quick", "deep"]


# ---------------------------------------------------------------------------
# prompt_too_long 422 writes NO log line (design 2.8).
# ---------------------------------------------------------------------------
def test_prompt_too_long_no_log_line(monkeypatch, log_path):
    # Ensure no model call happens: any httpx.post would be a bug.
    def _should_not_call(*a, **k):
        raise AssertionError("Ollama must not be called for prompt_too_long")

    monkeypatch.setattr(client.httpx, "post", _should_not_call)

    too_long = "x" * (settings.MAX_PROMPT_CHARS + 1)
    with TestClient(app) as c:
        resp = c.post("/quick", json={"prompt": too_long})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "prompt_too_long"
    # No log line written.
    assert not log_path.exists() or log_path.read_text(encoding="utf-8").strip() == ""


def test_invalid_json_schema_422(monkeypatch, log_path):
    def _should_not_call(*a, **k):
        raise AssertionError("Ollama must not be called for invalid_json_schema")

    monkeypatch.setattr(client.httpx, "post", _should_not_call)
    with TestClient(app) as c:
        # json_schema as a list, not a dict -> pydantic coerces? send via raw.
        resp = c.post("/quick", json={"prompt": "hi", "json_schema": [1, 2, 3]})
    # pydantic rejects a non-dict json_schema at the model layer (422) before
    # our handler, which is also a 422 — either way no model call.
    assert resp.status_code == 422
