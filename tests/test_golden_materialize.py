"""Golden-set materialization (design §9.3).

For each proposed ``.BO`` ticker (tests/golden/README.md) fetch 200-day daily
bars THROUGH ``MarketDataService`` and SAVE them to
``tests/golden/<TICKER>_1d.parquet`` (point-in-time, rule 7). Parquet preserves
float64 dtypes + the tz-naive ``DatetimeIndex`` losslessly so a loaded frame
reproduces ``compute_snapshot_id`` exactly.

This is an ``@pytest.mark.integration`` test: it is SKIPPED (not failed) when
Postgres/Redis/yfinance is down, so the parquet set is (re)materialized only
when the real stack is up. The committed parquet files are the fast offline
fixtures consumed by ``test_golden_loader.py`` and the offline G2 check.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.app.services.data_service import MarketDataService
from backend.app.services.ticker_resolution import resolve_market_ticker

from tests.integration_helpers import (  # noqa: F401 (fixtures)
    GOLDEN_TICKERS,
    require_postgres,
    require_redis,
    require_yfinance,
)

pytestmark = pytest.mark.integration

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN_LOOKBACK_DAYS = 200
MIN_GOLDEN_ROWS = 60


def _golden_path(ticker: str) -> Path:
    """tests/golden/<RESOLVED>_1d.parquet for a proposed ticker."""
    resolved = resolve_market_ticker(ticker)
    return GOLDEN_DIR / f"{resolved}_1d.parquet"


@pytest.mark.parametrize("ticker", GOLDEN_TICKERS)
def test_materialize_golden_parquet(
    ticker, require_postgres, require_redis, require_yfinance
):
    """Fetch 200-day daily bars through the service and save the parquet fixture."""
    svc = MarketDataService()
    df = asyncio.run(svc.get_ohlcv(ticker, "1d", lookback_days=GOLDEN_LOOKBACK_DAYS))

    # A real golden ticker returns a usable window; a thin fetch (e.g. a
    # delisted/suspended symbol) is a skip, not a hard failure of the suite.
    if df.empty or len(df) < MIN_GOLDEN_ROWS:
        pytest.skip(f"{ticker}: only {len(df)} rows fetched (< {MIN_GOLDEN_ROWS}); not materialized")

    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df.index.name == "Datetime"
    assert df.index.tz is None, "golden index must be tz-naive"
    for col in df.columns:
        assert str(df[col].dtype) == "float64"

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_golden_path(ticker), engine="pyarrow")
