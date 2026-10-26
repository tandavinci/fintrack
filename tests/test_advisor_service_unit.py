"""OFFLINE unit tests for the advisor service (Step 5 / G5).

Two DB-free layers, both part of the fast offline suite (no Postgres, no Redis,
no network, no model):

  * ``_format_context`` — the PURE grounding helper. Given plain holdings + recs
    dicts it produces a compact, bounded CONTEXT string that names the tickers
    and stored actions. We assert the facts land, the length cap holds, and the
    empty case is handled.
  * ``answer`` — with ``gateway.client.chat_completion`` MONKEYPATCHED to a canned
    OpenAI response (and the DB reads monkeypatched away), we assert it builds a
    system+user messages list, passes the configured model, returns the content
    plus ``grounded_on`` provenance, and that the SYSTEM prompt forbids inventing
    numbers. A monkeypatched gateway failure must surface as ``AdvisorUnavailable``.
"""

from __future__ import annotations

import anyio
import pytest

import backend.app.services.advisor_service as svc
from backend.app.services.advisor_service import (
    AdvisorUnavailable,
    SYSTEM_PROMPT,
    _build_messages,
    _format_context,
    answer,
)
from gateway.client import GatewayUnavailable
from gateway.settings import settings as gateway_settings


# --- _format_context (pure) --------------------------------------------------


def _sample_holdings():
    return [
        {"ticker": "RELIANCE.NS", "quantity": 10, "avg_price": "2500.00"},
        {"ticker": "TCS.BO", "quantity": 5, "avg_price": "3800.50"},
        {"ticker": "INFY.NS", "quantity": None, "avg_price": None},  # watchlist-only
    ]


def _sample_recs():
    return {
        "RELIANCE.NS": {
            "quant": {
                "action": "BUY",
                "confidence": 0.72,
                "as_of": "2024-01-15",
                "rationale": "Golden cross with rising volume.",
            },
            "deep": {
                "action": "HOLD",
                "confidence": 0.55,
                "as_of": "2024-01-14",
                "rationale": "Mixed news flow.",
            },
        },
        "TCS.BO": {
            "quant": {
                "action": "SELL",
                "confidence": 0.61,
                "as_of": "2024-01-15",
                "rationale": "Death cross forming.",
            },
        },
    }


def test_format_context_contains_holdings_and_recs():
    block = _format_context(_sample_holdings(), _sample_recs())
    # Holdings facts present.
    assert "RELIANCE.NS" in block
    assert "quantity 10" in block
    assert "2500.00" in block
    # Watchlist-only ticker appears as watched, not owned.
    assert "WATCHLIST" in block
    assert "INFY.NS" in block
    # Recommendation actions present for both sources.
    assert "BUY" in block
    assert "SELL" in block
    assert "[quant]" in block
    assert "[deep]" in block
    assert "Golden cross" in block


def test_format_context_empty_is_graceful():
    block = _format_context([], {})
    assert "no holdings on record" in block
    assert "no stored recommendations" in block


def test_format_context_is_length_bounded():
    # Many holdings + long rationales must not blow the num_ctx budget.
    holdings = [
        {"ticker": f"T{i}.NS", "quantity": i + 1, "avg_price": "100.00"}
        for i in range(200)
    ]
    recs = {
        f"T{i}.NS": {
            "quant": {
                "action": "BUY",
                "confidence": 0.5,
                "as_of": "2024-01-15",
                "rationale": "x" * 5000,
            }
        }
        for i in range(200)
    }
    block = _format_context(holdings, recs)
    assert len(block) <= svc.MAX_CONTEXT_CHARS + 64  # cap + the truncation note


def test_format_context_truncates_long_rationale():
    holdings = [{"ticker": "AAA.NS", "quantity": 1, "avg_price": "1.00"}]
    recs = {
        "AAA.NS": {
            "quant": {
                "action": "BUY",
                "confidence": 0.5,
                "as_of": "2024-01-15",
                "rationale": "y" * 999,
            }
        }
    }
    block = _format_context(holdings, recs)
    # The per-rationale cap applies (ellipsis marks the trim).
    assert "…" in block


# --- _build_messages (pure) --------------------------------------------------


def test_build_messages_shape_and_system_grounding():
    messages = _build_messages("CONTEXT: holdings...", "What do I own?", None)
    assert messages[0]["role"] == "system"
    assert messages[-1]["role"] == "user"
    # The context + question are combined into the single user turn.
    assert "CONTEXT: holdings..." in messages[-1]["content"]
    assert "What do I own?" in messages[-1]["content"]


def test_build_messages_replays_history():
    history = [
        {"role": "user", "content": "earlier q"},
        {"role": "assistant", "content": "earlier a"},
        {"role": "system", "content": "SHOULD BE DROPPED"},
    ]
    messages = _build_messages("CTX", "now?", history)
    roles = [m["role"] for m in messages]
    # system first, then the two valid prior turns, then the new user turn.
    assert roles == ["system", "user", "assistant", "user"]
    assert "SHOULD BE DROPPED" not in [m["content"] for m in messages]


def test_system_prompt_forbids_inventing_numbers():
    low = SYSTEM_PROMPT.lower()
    assert "do not invent" in low or "not invent" in low
    assert "decision support" in low
    assert "i don't have that information in your stored data" in low


# --- answer() with monkeypatched gateway + DB reads --------------------------


def _patch_db_reads(monkeypatch, holdings, recs):
    """Replace the two async DB readers so answer() needs no database."""

    async def _fake_holdings(session, user_id):
        return holdings

    async def _fake_recs(session, tickers):
        return recs

    monkeypatch.setattr(svc, "_load_holdings_rows", _fake_holdings)
    monkeypatch.setattr(svc, "_load_recs_by_ticker", _fake_recs)


def test_answer_builds_messages_and_returns_content(monkeypatch):
    captured = {}

    def _fake_chat_completion(payload):
        captured["payload"] = payload
        return {
            "choices": [
                {"message": {"role": "assistant", "content": "You own RELIANCE.NS."}}
            ]
        }

    _patch_db_reads(monkeypatch, _sample_holdings(), _sample_recs())
    monkeypatch.setattr(svc, "chat_completion", _fake_chat_completion)

    result = anyio.run(answer, None, 1, "What do I own?")

    # Returned content + provenance.
    assert result["answer"] == "You own RELIANCE.NS."
    assert result["grounded_on"]["holdings"] == 2  # two held, one watchlist-only
    assert result["grounded_on"]["recommendations"] == 3  # 2 for RELIANCE + 1 TCS

    # The payload passed to the gateway: configured model + a system+user list.
    payload = captured["payload"]
    assert payload["model"] == gateway_settings.GATEWAY_MODEL
    msgs = payload["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"
    # The system message carries the grounding instruction (no invented numbers).
    assert "not invent" in msgs[0]["content"].lower()
    # The user turn contains the real stored facts, not a free-form prompt.
    assert "RELIANCE.NS" in msgs[-1]["content"]


def test_answer_maps_gateway_failure_to_advisor_unavailable(monkeypatch):
    def _boom(payload):
        raise GatewayUnavailable("connection refused")

    _patch_db_reads(monkeypatch, _sample_holdings(), _sample_recs())
    monkeypatch.setattr(svc, "chat_completion", _boom)

    with pytest.raises(AdvisorUnavailable):
        anyio.run(answer, None, 1, "anything?")
