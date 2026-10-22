"""Gate G2 — cross-lane element-wise equality (design §9.2 #3, THE GATE).

For every materialized golden ticker, obtain the enriched daily frame through
three independent lanes:

  (a) the quant ``DataPipeline`` (what ``quant.recommend`` reads),
  (b) the deep adapter's pipeline build (what ``deep.analyze`` reads),
  (c) the Tracker ``MarketDataService`` path (raw bars enriched by the ONE
      ``TechnicalIndicators`` code path).

All three MUST agree element-wise on the canonical columns AND produce an equal
``compute_snapshot_id``. No per-lane tolerances — exactness is the design
guarantee (one DataFetcher seam + one indicator path, §9.4).

Integration-marked: requires Postgres + Redis + live yfinance and the golden
parquet fixtures (so the ticker list matches what was materialized). Skips with
a reason when a source is down or the fixtures are absent.
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from backend.app.schemas.snapshot import compute_snapshot_id
from backend.app.services.data_service import MarketDataService
from backend.app.services.ticker_resolution import resolve_market_ticker
from quant.data.indicators import TechnicalIndicators
from quant.data.pipeline import DataPipeline

from tests.integration_helpers import (  # noqa: F401 (fixtures)
    GOLDEN_TICKERS,
    TRADING_DAY_AS_OF,
    clean_price_bars,
    flush_ohlcv_cache,
    require_postgres,
    require_redis,
    require_yfinance,
)

pytestmark = pytest.mark.integration

GOLDEN_DIR = Path(__file__).parent / "golden"
LOOKBACK_DAYS = 200
_CANONICAL = ["Open", "High", "Low", "Close", "Volume", "SMA_20", "SMA_50", "RSI", "MACD", "ATR"]


def _materialized_tickers() -> list[str]:
    """The golden tickers that actually have a committed parquet fixture."""
    out = []
    for t in GOLDEN_TICKERS:
        resolved = resolve_market_ticker(t)
        if (GOLDEN_DIR / f"{resolved}_1d.parquet").exists():
            out.append(t)
    return out


def _enriched(raw: pd.DataFrame) -> pd.DataFrame:
    """The ONE enrichment code path + the 1d warmup dropna (quant/deep share it)."""
    e = TechnicalIndicators.add_all(raw, interval="1d")
    return e.dropna(subset=["SMA_50", "RSI"])


def test_gate_g2_cross_lane_frame_and_snapshot_equality(
    require_postgres, require_redis, require_yfinance, clean_price_bars, flush_ohlcv_cache
):
    """The gate: tracker == quant == deep enriched frame + snapshot_id, per golden."""
    tickers = _materialized_tickers()
    if not tickers:
        pytest.skip(
            "no materialized golden fixtures — run `pytest -m integration "
            "tests/test_golden_materialize.py` with the stack up first"
        )

    as_of = TRADING_DAY_AS_OF
    end_iso = as_of.isoformat()

    for ticker in tickers:
        # Warm the DB/cache once so all three lanes read the SAME stored bars.
        flush_ohlcv_cache()

        # (a) quant DataPipeline enriched frame.
        quant_pipe = DataPipeline(ticker=ticker, interval="1d")
        quant_df = quant_pipe.get_enriched_data(
            end_date=end_iso, interval="1d", lookback_days=LOOKBACK_DAYS, use_cache=False
        )

        # (b) deep adapter's pipeline build (same construction deep.analyze uses).
        deep_pipe = DataPipeline(ticker=ticker, interval="1d")
        deep_df = deep_pipe.get_enriched_data(
            end_date=end_iso, interval="1d", lookback_days=LOOKBACK_DAYS, use_cache=False
        )

        # (c) Tracker MarketDataService path: raw bars -> the ONE enrichment path.
        svc = MarketDataService()
        raw = asyncio.run(
            svc.get_ohlcv(ticker, "1d", end=as_of, lookback_days=LOOKBACK_DAYS)
        )
        tracker_df = _enriched(raw)

        cols = [c for c in _CANONICAL if c in quant_df.columns]
        pd.testing.assert_frame_equal(quant_df[cols], deep_df[cols])
        pd.testing.assert_frame_equal(quant_df[cols], tracker_df[cols])

        q_id = compute_snapshot_id(ticker, as_of, quant_df)
        d_id = compute_snapshot_id(ticker, as_of, deep_df)
        t_id = compute_snapshot_id(ticker, as_of, tracker_df)
        assert q_id == d_id == t_id, f"{ticker}: snapshot_id diverged across lanes"
