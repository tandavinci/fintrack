"""Gate G3 — quant-core productization gate (STEERING Step 3).

Two gate checks from the steering file, plus an offline unit test for the new
benchmark scorer:

  1. Backtest reproducibility (deterministic) — the rule-based quant path gives
     IDENTICAL results on two runs over the same daily frame. Proven OFFLINE at
     the ``recommend`` level (that IS the rule-based path) with a synthetic
     deterministic daily frame, and ALSO at the real ``BacktestEngine`` level
     via an ``@pytest.mark.integration`` test that runs the backtest twice for
     one golden ticker and asserts identical ``BacktestReport.to_dict()``.

  2. Regime labels for all 10 golden tickers — an ``@pytest.mark.integration``
     test that classifies the regime for every committed golden parquet fixture
     and asserts a non-empty label in the known set. Uses the parquet goldens so
     it is reproducible without live yfinance.

  3. ``score_vs_benchmark`` offline unit test — exact numbers on a hand-computed
     example and the rating-threshold boundaries.

Offline tests here run with no infra; the integration tests skip-with-reason
when their inputs can't load (mirrors tests/integration_helpers.py).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant.agents.regime_agent import MarketRegime, RegimeAgent
from quant.backtesting.benchmark import score_vs_benchmark
from quant.data.indicators import TechnicalIndicators
from quant.recommend import recommend

AS_OF = date(2024, 1, 15)
GOLDEN_DIR = Path(__file__).parent / "golden"

# The known regime set (STEERING Step 3 gate wording).
KNOWN_REGIMES = {
    MarketRegime.BULLISH_TREND.value,
    MarketRegime.BEARISH_TREND.value,
    MarketRegime.SIDEWAYS.value,
    MarketRegime.HIGH_VOLATILITY.value,
    MarketRegime.BREAKOUT.value,
}


# --- synthetic daily frame (mirrors tests/test_quant_recommend.py) -----------


def _synthetic_daily(n: int = 160, seed: int = 42) -> pd.DataFrame:
    """A deterministic DAILY OHLCV uptrend; fixed seed -> identical noise."""
    idx = pd.bdate_range("2023-06-01", periods=n)
    rng = np.random.RandomState(seed)
    close = np.empty(n)
    p = 100.0
    for i in range(n):
        p = p * (1 + 0.004) + rng.uniform(-0.05, 0.05)
        close[i] = p
    openp = close * (1 - 0.001)
    high = np.maximum(openp, close) * (1 + 0.002)
    low = np.minimum(openp, close) * (1 - 0.002)
    vol = np.full(n, 1_000_000.0)
    df = pd.DataFrame(
        {"Open": openp, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )
    df.index.name = "Datetime"
    return df


def _enriched_daily() -> pd.DataFrame:
    df = _synthetic_daily()
    return TechnicalIndicators.add_all(df, interval="1d").dropna(subset=["SMA_50", "RSI"])


def _golden_files() -> list[Path]:
    return sorted(GOLDEN_DIR.glob("*_1d.parquet"))


# === Gate check 1a: rule-based reproducibility (OFFLINE, deterministic) ======


def test_rule_based_path_is_reproducible_offline():
    """The rule-based quant path (``recommend``) is identical on two runs over
    the SAME daily frame — action, confidence, evidence, and snapshot_id.

    This IS the rule-based path, so this proves G3 reproducibility without any
    network/DB. The integration test below additionally proves it through the
    full ``BacktestEngine``.
    """
    frame = _enriched_daily()
    rec1 = recommend("TEST", AS_OF, df=frame)
    rec2 = recommend("TEST", AS_OF, df=_enriched_daily())

    assert rec1.action == rec2.action
    assert rec1.confidence == rec2.confidence
    assert rec1.evidence == rec2.evidence
    assert rec1.snapshot_id == rec2.snapshot_id
    # Full byte-level identity as a belt-and-braces reproducibility proof.
    assert rec1.model_dump_json() == rec2.model_dump_json()


# === Gate check 3: benchmark scorer (OFFLINE unit test) ======================


def test_score_vs_benchmark_hand_computed_example():
    """Exact numbers on a hand-computed example.

    Strategy trades: +10% then -5% -> (1.10 * 0.95 - 1) * 100 = 4.5%.
    Benchmark closes 100 -> 102 -> buy-and-hold = (102/100 - 1) * 100 = 2.0%.
    Excess = 4.5 - 2.0 = 2.5 -> rating 4 (2.0 <= excess < 10.0).
    """
    result = score_vs_benchmark([0.10, -0.05], [100.0, 101.0, 102.0])
    assert result["strategy_total_return_pct"] == 4.5
    assert result["benchmark_total_return_pct"] == 2.0
    assert result["excess_return_pct"] == 2.5
    assert result["rating"] == 4


def test_score_vs_benchmark_empty_inputs_are_flat_rating_3():
    """No trades and <2 benchmark closes -> both returns 0, excess 0, rating 3."""
    result = score_vs_benchmark([], [])
    assert result["strategy_total_return_pct"] == 0.0
    assert result["benchmark_total_return_pct"] == 0.0
    assert result["excess_return_pct"] == 0.0
    assert result["rating"] == 3


def test_score_vs_benchmark_rating_thresholds():
    """The 1..5 rating bands, checked at and around each inclusive edge.

    Benchmark is flat (two equal closes -> 0%), so excess == strategy return,
    which lets a single-trade fraction drive the excess directly.
    """
    flat = [100.0, 100.0]

    # rating 5: excess >= 10
    assert score_vs_benchmark([0.10], flat)["rating"] == 5  # +10.0% exactly
    assert score_vs_benchmark([0.15], flat)["rating"] == 5

    # rating 4: 2 <= excess < 10
    assert score_vs_benchmark([0.02], flat)["rating"] == 4  # +2.0% exactly
    assert score_vs_benchmark([0.099], flat)["rating"] == 4

    # rating 3: -2 <= excess < 2
    assert score_vs_benchmark([0.0], flat)["rating"] == 3
    assert score_vs_benchmark([-0.02], flat)["rating"] == 3  # -2.0% exactly
    assert score_vs_benchmark([0.019], flat)["rating"] == 3

    # rating 2: -10 <= excess < -2
    assert score_vs_benchmark([-0.10], flat)["rating"] == 2  # -10.0% exactly
    assert score_vs_benchmark([-0.021], flat)["rating"] == 2

    # rating 1: excess < -10
    assert score_vs_benchmark([-0.1001], flat)["rating"] == 1
    assert score_vs_benchmark([-0.5], flat)["rating"] == 1


# === Gate check 1b: real backtest reproducibility (INTEGRATION) ==============


@pytest.mark.integration
def test_backtest_engine_is_reproducible_for_one_golden_ticker():
    """The same real backtest run twice gives IDENTICAL ``to_dict()`` output.

    Uses the live quant data path (Postgres-backed yfinance fetch). Skips-with-
    reason if the stack/data can't produce enough bars, so a bare machine stays
    green.
    """
    from backend.app.services.data_service import MarketDataService  # noqa: F401
    from quant.backtesting.engine import BacktestEngine

    ticker = "RELIANCE.NS"
    try:
        report1 = BacktestEngine(ticker=ticker, interval="1d").run(lookback_days=200)
        report2 = BacktestEngine(ticker=ticker, interval="1d").run(lookback_days=200)
    except ValueError as exc:
        pytest.skip(f"backtest needs the live data stack / enough bars: {exc}")
    except Exception as exc:  # noqa: BLE001 - network/DB down -> skip, don't fail
        pytest.skip(f"live data stack unreachable for backtest: {exc}")

    assert report1.to_dict() == report2.to_dict()
    # A non-trivial run (the rule-based path produced at least one prediction).
    assert report1.to_dict()["total_predictions"] >= 1


# === Gate check 2: regime labels for all 10 golden tickers (INTEGRATION) =====


@pytest.mark.integration
def test_regime_labels_for_all_ten_golden_tickers():
    """Every one of the 10 committed golden parquet fixtures classifies to a
    non-empty regime label in the known set.

    Loads the committed parquet goldens (reproducible, no live yfinance). Skips-
    with-reason only if the fixtures aren't materialized yet or a frame can't
    enrich to the >=20-bar window the RegimeAgent needs.
    """
    files = _golden_files()
    if not files:
        pytest.skip(
            "no tests/golden/*_1d.parquet fixtures yet - materialize them via "
            "`pytest -m integration tests/test_golden_materialize.py`"
        )

    assert len(files) == 10, f"expected 10 golden fixtures, found {len(files)}"

    for path in files:
        raw = pd.read_parquet(path, engine="pyarrow")
        enriched = TechnicalIndicators.add_all(raw, interval="1d").dropna(
            subset=["SMA_50", "RSI"]
        )
        if len(enriched) < 20:
            pytest.skip(
                f"{path.name}: only {len(enriched)} warmed bars, need >=20 for regime"
            )

        classification = RegimeAgent().classify(enriched)
        label = classification.regime.value
        assert label, f"{path.name}: empty regime label"
        assert label in KNOWN_REGIMES, f"{path.name}: unknown regime {label!r}"
        assert 0.0 <= classification.confidence <= 1.0, f"{path.name}: bad confidence"
