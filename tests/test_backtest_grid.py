"""Offline tests for the G6 backtest grid (``quant/backtesting/grid.py``).

FAST, NO infra: these run WITHOUT Postgres / Redis / Ollama / the gateway. They
drive the quant + buy-and-hold + benchmark lanes entirely from the committed
golden parquet frames and the committed Nifty benchmark fixture.

The deep lane is NOT exercised here (it needs the gateway + Ollama). A single
``@pytest.mark.integration`` test asserts the deep code path is wired and
skips-with-reason when Ollama is down (mirrors tests/integration_helpers.py).
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from quant.backtesting import grid

EXPECTED_TICKERS = grid.GOLDEN_TICKERS


def _fixtures_present() -> bool:
    if not grid.BENCHMARK_PARQUET.exists():
        return False
    return all((grid.GOLDEN_DIR / f"{t}_1d.parquet").exists() for t in EXPECTED_TICKERS)


def _require_fixtures() -> None:
    if not _fixtures_present():
        pytest.skip(
            "golden stock fixtures and/or the benchmark fixture "
            "(tests/golden/benchmark/NSEI_1d.parquet) are not materialized"
        )


def test_benchmark_fixture_loads_with_enough_rows():
    """The committed Nifty benchmark fixture loads with >=60 closes."""
    _require_fixtures()
    closes = grid.load_benchmark_closes()
    assert len(closes) >= 60, f"benchmark fixture has only {len(closes)} rows"
    assert all(math.isfinite(c) for c in closes)


def test_grid_runs_fully_offline_with_a_row_per_ticker(tmp_path):
    """The quant+BH+benchmark grid runs offline and yields one row per golden."""
    _require_fixtures()
    result = grid.run_grid(out_dir=tmp_path)

    assert [r.ticker for r in result.rows] == EXPECTED_TICKERS
    assert len(result.rows) == 10
    assert result.deep is None  # no deep lane when deep_ticker is not given

    # Both output files were written.
    assert (tmp_path / "backtest_grid.csv").exists()
    assert (tmp_path / "backtest_grid.md").exists()


def test_each_rating_is_in_1_to_5_and_returns_are_finite(tmp_path):
    """Every quant rating is a valid 1..5 band and every reported float is finite."""
    _require_fixtures()
    result = grid.run_grid(out_dir=tmp_path)

    for row in result.rows:
        assert row.quant_rating in {1, 2, 3, 4, 5}, f"{row.ticker}: bad rating"
        assert math.isfinite(row.quant_return_pct), f"{row.ticker}: quant NaN/inf"
        assert math.isfinite(row.buy_and_hold_pct), f"{row.ticker}: BH NaN/inf"
        assert math.isfinite(row.benchmark_pct), f"{row.ticker}: bench NaN/inf"
        assert row.quant_signals >= 1, f"{row.ticker}: no signals"


def test_walk_forward_returns_are_finite_fractions():
    """Each per-anchor fractional return is a finite float (sanity on the lane)."""
    _require_fixtures()
    for ticker in EXPECTED_TICKERS:
        enriched = grid.load_enriched_golden(ticker)
        returns = grid.walk_forward_quant_returns(ticker, enriched)
        assert returns, f"{ticker}: walk-forward produced no returns"
        assert all(math.isfinite(r) for r in returns), f"{ticker}: non-finite return"


def test_offline_grid_is_byte_reproducible(tmp_path):
    """G3-style determinism applied to the grid: the deterministic table
    (CSV + MD) is byte-identical across two offline runs.

    The deep cell is excluded from the CSV and separated in the MD, so the
    reproducible table is unaffected by any (absent, here) deep output.
    """
    _require_fixtures()
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"

    grid.run_grid(out_dir=out_a)
    grid.run_grid(out_dir=out_b)

    csv_a = (out_a / "backtest_grid.csv").read_bytes()
    csv_b = (out_b / "backtest_grid.csv").read_bytes()
    assert csv_a == csv_b, "CSV not byte-identical across two runs"

    md_a = (out_a / "backtest_grid.md").read_bytes()
    md_b = (out_b / "backtest_grid.md").read_bytes()
    assert md_a == md_b, "MD not byte-identical across two runs"


def test_in_memory_rows_are_identical_across_two_runs(tmp_path):
    """The computed rows themselves (not just the files) match on two runs."""
    _require_fixtures()
    r1 = grid.run_grid(out_dir=tmp_path / "1")
    r2 = grid.run_grid(out_dir=tmp_path / "2")
    assert [row.as_dict() for row in r1.rows] == [row.as_dict() for row in r2.rows]


def test_deep_ticker_not_in_grid_is_recorded_without_running(tmp_path):
    """A deep_ticker outside the grid is recorded as an error, not executed.

    This exercises the deep-cell wiring (the branch that records the cell)
    WITHOUT importing the deep adapter / needing Ollama.
    """
    _require_fixtures()
    result = grid.run_grid(deep_ticker="NOTREAL.NS", out_dir=tmp_path)
    assert result.deep is not None
    assert result.deep.error is not None
    assert "not in the grid tickers" in result.deep.error


# --- the single integration test (skips-with-reason when Ollama is down) -----


@pytest.mark.integration
def test_deep_cell_wired_and_runs_or_skips():
    """The ONE deep cell imports cleanly and either returns or errors gracefully.

    Needs the gateway + Ollama live. Skips-with-reason otherwise so a bare
    machine stays green (mirrors tests/integration_helpers.py skip pattern).
    This does NOT assert a particular action — the deep lane may legitimately
    return a neutral HOLD; it only asserts the wiring produced a populated cell.
    """
    import socket

    def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    if not _port_open("localhost", 11434):
        pytest.skip("Ollama unreachable at localhost:11434")
    if not _port_open("localhost", 8001):
        pytest.skip("gateway unreachable at localhost:8001")

    cell = grid.run_deep_cell("RELIANCE.NS")
    assert cell.ticker == "RELIANCE.NS"
    # Either it produced an action or recorded an error string; never both empty.
    assert (cell.action is not None) or (cell.error is not None)
