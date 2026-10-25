"""Offline + live tests for the OpenAI-compatible chat-completions proxy (G4).

The gateway exposes ``POST /v1/chat/completions`` as a THIN pass-through to
Ollama's own OpenAI-compatible endpoint so TradingAgents routes its model calls
THROUGH the gateway (STEERING rule 1: only the gateway talks to Ollama).

Offline tests monkeypatch ``httpx.post`` to a canned OpenAI-shaped response and
assert:
  (a) the forwarded request injects ``options.num_ctx == 8192`` and
      ``stream == False``;
  (b) a ``tool_calls`` block passes back through the route unchanged;
  (c) a 2xx logs one JSONL line with route="chat", num_ctx==8192, a 64-char
      prompt_sha256, and valid True;
  (d) an httpx connection error -> GatewayUnavailable -> route returns 503;
      an Ollama 4xx/5xx -> GatewayHTTPError -> route returns 502.

A final skip-safe live check probes ``GET {OLLAMA_BASE_URL}/api/tags`` and runs
a tiny real chat completion only when Ollama is reachable.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway import client
from gateway.app import app
from gateway.settings import settings


# ---------------------------------------------------------------------------
# Fakes + fixtures (mirror tests/test_gateway_unit.py).
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


_PLAIN_COMPLETION = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "model": "llama3.2",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "hello back"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
}

_TOOL_CALL_COMPLETION = {
    "id": "chatcmpl-2",
    "object": "chat.completion",
    "model": "llama3.2",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "get_price",
                            "arguments": '{"ticker": "RELIANCE.NS"}',
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
}

_REQUEST_BODY = {
    "model": "llama3.2",
    "messages": [
        {"role": "system", "content": "You are a trading analyst."},
        {"role": "user", "content": "What's the signal?"},
    ],
    "temperature": 0.1,
    "tools": [
        {
            "type": "function",
            "function": {"name": "get_price", "parameters": {"type": "object"}},
        }
    ],
}


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    p = tmp_path / "calls.jsonl"
    monkeypatch.setattr(settings, "GATEWAY_LOG_PATH", str(p))
    return p


def _capturing_post(captured: dict, response_payload: dict, status_code: int = 200):
    """A fake httpx.post that records the forwarded JSON body and URL."""

    def _post(url, *args, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return _FakeResponse(response_payload, status_code=status_code)

    return _post


# ---------------------------------------------------------------------------
# (a) num_ctx cap injected + stream forced False on the forwarded request.
# ---------------------------------------------------------------------------
def test_chat_completion_injects_num_ctx_cap_and_disables_stream(
    monkeypatch, log_path
):
    monkeypatch.setattr(settings, "GATEWAY_NUM_CTX", 999999)  # prove the cap wins
    captured: dict = {}
    monkeypatch.setattr(
        client.httpx, "post", _capturing_post(captured, _PLAIN_COMPLETION)
    )

    # Caller asked for streaming; the proxy must force it off.
    body = dict(_REQUEST_BODY, stream=True)
    client.chat_completion(body)

    assert captured["url"].endswith("/v1/chat/completions")
    fwd = captured["json"]
    assert fwd["options"]["num_ctx"] == 8192
    assert fwd["stream"] is False
    # The caller's dict is never mutated.
    assert "options" not in body
    assert body["stream"] is True


# ---------------------------------------------------------------------------
# (b) tool_calls pass back through the route unchanged.
# ---------------------------------------------------------------------------
def test_tool_calls_pass_through_route_unchanged(monkeypatch, log_path):
    captured: dict = {}
    monkeypatch.setattr(
        client.httpx, "post", _capturing_post(captured, _TOOL_CALL_COMPLETION)
    )

    with TestClient(app) as http:
        resp = http.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert resp.status_code == 200
    body = resp.json()
    # The whole Ollama response survives verbatim, including tool_calls.
    assert body == _TOOL_CALL_COMPLETION
    tc = body["choices"][0]["message"]["tool_calls"][0]
    assert tc["function"]["name"] == "get_price"
    assert tc["function"]["arguments"] == '{"ticker": "RELIANCE.NS"}'


# ---------------------------------------------------------------------------
# (c) a 2xx logs one JSONL line with route="chat", num_ctx==8192, 64-char hash.
# ---------------------------------------------------------------------------
def test_chat_completion_logs_one_line(monkeypatch, log_path):
    captured: dict = {}
    monkeypatch.setattr(
        client.httpx, "post", _capturing_post(captured, _PLAIN_COMPLETION)
    )

    client.chat_completion(_REQUEST_BODY)

    lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["route"] == "chat"
    assert rec["num_ctx"] == 8192
    assert rec["valid"] is True
    assert rec["error"] is None
    assert isinstance(rec["prompt_sha256"], str) and len(rec["prompt_sha256"]) == 64
    assert rec["model_id"] == "llama3.2"
    # usage -> token counts carried through.
    assert rec["prompt_eval_count"] == 11
    assert rec["eval_count"] == 4


# ---------------------------------------------------------------------------
# (d) connection error -> 503; Ollama 4xx/5xx -> 502.
# ---------------------------------------------------------------------------
def test_connection_error_maps_to_503(monkeypatch, log_path):
    def _boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(client.httpx, "post", _boom)

    # Core raises GatewayUnavailable...
    with pytest.raises(client.GatewayUnavailable):
        client.chat_completion(_REQUEST_BODY)

    # ...and a failure log line was still written.
    rec = json.loads(
        [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()][-1]
    )
    assert rec["route"] == "chat" and rec["valid"] is False
    assert rec["error"] == "ollama_unreachable"

    # ...and the route maps it to 503.
    with TestClient(app) as http:
        resp = http.post("/v1/chat/completions", json=_REQUEST_BODY)
    assert resp.status_code == 503
    assert resp.json()["detail"] == "ollama_unreachable"


def test_ollama_http_error_maps_to_502(monkeypatch, log_path):
    captured: dict = {}
    monkeypatch.setattr(
        client.httpx,
        "post",
        _capturing_post(captured, {"error": "bad"}, status_code=500),
    )

    with pytest.raises(client.GatewayHTTPError):
        client.chat_completion(_REQUEST_BODY)

    with TestClient(app) as http:
        resp = http.post("/v1/chat/completions", json=_REQUEST_BODY)
    assert resp.status_code == 502
    assert resp.json()["detail"] == "ollama_http_500"


# ---------------------------------------------------------------------------
# Minimal request validation writes NO log line (no model call).
# ---------------------------------------------------------------------------
def test_missing_messages_422_no_model_call(monkeypatch, log_path):
    def _should_not_call(*a, **k):
        raise AssertionError("Ollama must not be called for a rejected request")

    monkeypatch.setattr(client.httpx, "post", _should_not_call)
    with TestClient(app) as http:
        r1 = http.post("/v1/chat/completions", json={"model": "llama3.2"})
        r2 = http.post(
            "/v1/chat/completions", json={"model": "llama3.2", "messages": []}
        )
        r3 = http.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert r1.status_code == 422 and r1.json()["detail"] == "messages_required"
    assert r2.status_code == 422 and r2.json()["detail"] == "messages_required"
    assert r3.status_code == 422 and r3.json()["detail"] == "model_required"
    assert not log_path.exists() or log_path.read_text(encoding="utf-8").strip() == ""


# ---------------------------------------------------------------------------
# Live, skip-safe check against a real Ollama (mirrors test_gateway_live.py).
# ---------------------------------------------------------------------------
def _ollama_up() -> bool:
    try:
        resp = httpx.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=5.0)
        return resp.status_code == 200
    except httpx.RequestError:
        return False


@pytest.mark.skipif(
    not _ollama_up(),
    reason="Ollama not reachable at the configured OLLAMA_BASE_URL",
)
def test_live_chat_completion_returns_message():
    with TestClient(app) as http:
        resp = http.post(
            "/v1/chat/completions",
            json={
                "model": settings.GATEWAY_MODEL,
                "messages": [
                    {"role": "user", "content": "Say the word ready and nothing else."}
                ],
                "temperature": 0.0,
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"].strip() != ""
