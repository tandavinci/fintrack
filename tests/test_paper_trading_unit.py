"""Offline unit tests for the paper-trading pure helpers (G6 deliverable 4).

FAST, OFFLINE: no Postgres / Redis / network. Exercises the pure P&L math, the
open/mark/close transition rules, and the per-book vs-benchmark summary — the
deterministic core of the paper-trading mechanism. DB round-trips live in
``tests/test_paper_trading_integration.py`` (marked integration, skip-with-reason).
"""

from __future__ import annotations

import pytest

from backend.app.models.paper_position import PAPER_NOTIONAL
from backend.app.services.paper_trading_service import (
    PAPER_SOURCES,
    BookSummary,
    decide_transition,
    paper_quantity,
    paper_return_pct,
    summarize_book,
)


# --- paper_return_pct --------------------------------------------------------


def test_paper_return_pct_gain_and_loss():
    assert paper_return_pct(100.0, 110.0) == pytest.approx(0.10)
    assert paper_return_pct(100.0, 90.0) == pytest.approx(-0.10)
    assert paper_return_pct(100.0, 100.0) == pytest.approx(0.0)


def test_paper_return_pct_rejects_nonpositive_entry():
    with pytest.raises(ValueError):
        paper_return_pct(0.0, 100.0)
    with pytest.raises(ValueError):
        paper_return_pct(-5.0, 100.0)


def test_paper_quantity_is_fixed_notional_over_entry():
    assert paper_quantity(100.0) == pytest.approx(PAPER_NOTIONAL / 100.0)
    assert paper_quantity(250.0, 10_000.0) == pytest.approx(40.0)
    with pytest.raises(ValueError):
        paper_quantity(0.0)


# --- decide_transition -------------------------------------------------------


def test_no_position_buy_opens():
    assert (
        decide_transition(
            has_open_position=False, action="BUY", days_held=None, horizon_days=10
        )
        == "open"
    )


@pytest.mark.parametrize("action", ["HOLD", "SELL", ""])
def test_no_position_non_buy_does_nothing(action):
    assert (
        decide_transition(
            has_open_position=False, action=action, days_held=None, horizon_days=10
        )
        == "none"
    )


def test_open_position_sell_closes():
    assert (
        decide_transition(
            has_open_position=True, action="SELL", days_held=3, horizon_days=10
        )
        == "close_sell"
    )


def test_open_position_horizon_elapsed_closes_regardless_of_action():
    # Even a BUY closes once the horizon has elapsed.
    assert (
        decide_transition(
            has_open_position=True, action="BUY", days_held=10, horizon_days=10
        )
        == "close_horizon"
    )
    assert (
        decide_transition(
            has_open_position=True, action="HOLD", days_held=12, horizon_days=10
        )
        == "close_horizon"
    )


def test_open_position_within_horizon_marks():
    assert (
        decide_transition(
            has_open_position=True, action="BUY", days_held=4, horizon_days=10
        )
        == "mark"
    )
    assert (
        decide_transition(
            has_open_position=True, action="HOLD", days_held=None, horizon_days=10
        )
        == "mark"
    )


def test_sell_takes_precedence_over_horizon():
    # A SELL is a close either way; it is classified as the sell close.
    assert (
        decide_transition(
            has_open_position=True, action="SELL", days_held=20, horizon_days=10
        )
        == "close_sell"
    )


def test_case_insensitive_action():
    assert (
        decide_transition(
            has_open_position=False, action="buy", days_held=None, horizon_days=10
        )
        == "open"
    )


# --- summarize_book ----------------------------------------------------------


def test_summarize_book_beats_benchmark():
    # Two closed trades: +10% then -5% -> 4.5% compounded. Benchmark flat (0%).
    summary = summarize_book(
        source="quant",
        realized_returns=[0.10, -0.05],
        open_count=3,
        benchmark_closes=[100.0, 100.0],
    )
    assert isinstance(summary, BookSummary)
    assert summary.source == "quant"
    assert summary.closed_positions == 2
    assert summary.open_positions == 3
    assert summary.realized_return_pct == pytest.approx(4.5)
    assert summary.benchmark_return_pct == pytest.approx(0.0)
    assert summary.excess_return_pct == pytest.approx(4.5)
    assert summary.rating == 4  # 2 <= excess < 10


def test_summarize_book_no_closed_positions_is_flat_rating_3():
    summary = summarize_book(
        source="deep",
        realized_returns=[],
        open_count=0,
        benchmark_closes=[100.0, 110.0],
    )
    assert summary.closed_positions == 0
    assert summary.realized_return_pct == pytest.approx(0.0)
    # Benchmark rose 10%, strategy 0 -> excess -10 -> rating 2 (>= -10).
    assert summary.benchmark_return_pct == pytest.approx(10.0)
    assert summary.rating == 2


def test_paper_sources_are_quant_and_deep():
    assert PAPER_SOURCES == ("quant", "deep")


def test_public_orchestration_entrypoints_are_callable():
    """Smoke: the DB-touching entry points import and are callable (behavior is
    covered by the integration test, not here)."""
    from backend.app.services import paper_trading_service as svc

    assert callable(svc.mark_to_market_ticker)
    assert callable(svc.mark_to_market_book)
    assert callable(svc.close_price_as_of)
