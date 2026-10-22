"""Real-infra integration tests for MarketDataService (design §9.2 #1,#2,#2a,#5a).

Requires Postgres + Redis + live yfinance. Each test skips-with-reason (never
fails) when a source is down. Run with the stack up and migrated:

    docker compose -f infra/docker-compose.yml up -d
    .venv\\Scripts\\python.exe -m alembic -c backend/alembic.ini upgrade head
    .venv\\Scripts\\python.exe -m pytest -m integration -q

These exercise the real Redis round-trip, the real Postgres UPSERT/SELECT with
the EXCLUSIVE-end predicate, and that DOUBLE PRECISION storage + exact-float
Redis serialization keep ``compute_snapshot_id`` identical on every read path
(the §4.4b precision half of the Gate G2 guarantee).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from backend.app.services import data_service as ds
from backend.app.services.data_service import MarketDataService
from backend.app.services.ticker_resolution import resolve_market_ticker
from quant.data.indicators import TechnicalIndicators
from backend.app.schemas.snapshot import compute_snapshot_id

from tests.integration_helpers import (  # noqa: F401 (fixtures)
    TRADING_DAY_AS_OF,
    clean_price_bars,
    count_price_bars,
    flush_ohlcv_cache,
    require_postgres,
    require_redis,
    require_yfinance,
    run_async,
)

pytestmark = pytest.mark.integration

GOLDEN = "RELIANCE.NS"
RESOLVED = resolve_market_ticker(GOLDEN)


def _svc() -> MarketDataService:
    """A service wired to the real (async + sync) repositories and Redis."""
    return MarketDataService()


# --- #1 real golden fetch lands rows in price_bars ---------------------------


def test_real_fetch_lands_rows_in_price_bars(
    require_postgres, require_redis, require_yfinance, clean_price_bars, flush_ohlcv_cache
):
    """get_ohlcv(golden,'1d',lookback_days=200) lands >=60 rows in price_bars
    under the RESOLVED ticker + interval."""
    svc = _svc()
    df = run_async(svc.get_ohlcv(GOLDEN, "1d", lookback_days=200))
    assert not df.empty
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert count_price_bars(RESOLVED, "1d") >= 60


# --- #2 second fetch served from cache/DB, yfinance NOT re-hit ---------------


def test_second_fetch_hits_no_network(
    require_postgres, require_redis, require_yfinance, clean_price_bars, flush_ohlcv_cache
):
    """A second identical get_ohlcv must NOT hit yfinance and must equal the
    first result (served from Redis/DB)."""
    svc = _svc()
    end = date.today()
    first = run_async(svc.get_ohlcv(GOLDEN, "1d", end=end, lookback_days=200))

    # Spy the yfinance leg: any call on the second fetch is a failure.
    hits = {"n": 0}
    real_fetch = MarketDataService._fetch_yfinance

    def _spy(self, *a, **k):
        hits["n"] += 1
        return real_fetch(self, *a, **k)

    import types

    svc2 = _svc()
    svc2._fetch_yfinance = types.MethodType(_spy, svc2)  # type: ignore[attr-defined]
    second = run_async(svc2.get_ohlcv(GOLDEN, "1d", end=end, lookback_days=200))

    assert hits["n"] == 0, "second fetch must be served from cache/DB, not yfinance"
    pd.testing.assert_frame_equal(first, second)


# --- #2a cold == DB == Redis snapshot_id on a TRADING-DAY as_of --------------


def test_cold_db_redis_snapshot_ids_equal_on_trading_day(
    require_postgres, require_redis, require_yfinance, clean_price_bars, flush_ohlcv_cache
):
    """The precision gate (§4.4b): the enriched snapshot_id is identical across
    a cold yfinance fetch, a DB-served read, and a Redis-served read, using a
    TRADING-DAY ``as_of`` so the exclusive-``end`` predicate (ts < as_of) is
    actually exercised at the boundary."""
    as_of = TRADING_DAY_AS_OF
    end = as_of  # EXCLUSIVE end == as_of
    start = end - timedelta(days=200)

    def _enriched(raw: pd.DataFrame) -> pd.DataFrame:
        e = TechnicalIndicators.add_all(raw, interval="1d")
        return e.dropna(subset=["SMA_50", "RSI"])

    svc = _svc()

    # (i) cold fetch: empty DB + flushed Redis -> yfinance, upsert, warm.
    flush_ohlcv_cache()
    cold = run_async(svc.get_ohlcv(GOLDEN, "1d", start=start, end=end, lookback_days=200))
    cold_id = compute_snapshot_id(GOLDEN, as_of, _enriched(cold))

    # (ii) flush Redis -> force a DB-served read of the same window.
    flush_ohlcv_cache()
    db_df = run_async(svc.get_ohlcv(GOLDEN, "1d", start=start, end=end, lookback_days=200))
    db_id = compute_snapshot_id(GOLDEN, as_of, _enriched(db_df))

    # (iii) warm Redis (the previous call warmed it) -> Redis-served read.
    redis_df = run_async(svc.get_ohlcv(GOLDEN, "1d", start=start, end=end, lookback_days=200))
    redis_id = compute_snapshot_id(GOLDEN, as_of, _enriched(redis_df))

    assert cold_id == db_id == redis_id
    # The frames themselves are byte-faithful on every path.
    pd.testing.assert_frame_equal(cold, db_df)
    pd.testing.assert_frame_equal(db_df, redis_df)


# --- #5a historical read is DB-authoritative, no refetch ---------------------


def test_historical_window_is_db_authoritative_no_refetch(
    require_postgres, require_redis, require_yfinance, clean_price_bars, flush_ohlcv_cache
):
    """A complete historical window (end < today - FRESH_WINDOW_DAYS) seeded in
    price_bars serves from the DB with ZERO yfinance hits and a frame equal to
    the seeded bars."""
    end = date.today() - timedelta(days=ds.FRESH_WINDOW_DAYS + 40)
    start = end - timedelta(days=200)

    svc = _svc()
    # Seed by one real fetch of the historical window.
    flush_ohlcv_cache()
    seeded = run_async(svc.get_ohlcv(GOLDEN, "1d", start=start, end=end, lookback_days=200))
    assert not seeded.empty

    # Now spy yfinance and read the same window again (flush Redis so the read
    # must go to the DB, not the cache).
    flush_ohlcv_cache()
    hits = {"n": 0}
    real_fetch = MarketDataService._fetch_yfinance

    def _spy(self, *a, **k):
        hits["n"] += 1
        return real_fetch(self, *a, **k)

    import types

    svc2 = _svc()
    svc2._fetch_yfinance = types.MethodType(_spy, svc2)  # type: ignore[attr-defined]
    served = run_async(svc2.get_ohlcv(GOLDEN, "1d", start=start, end=end, lookback_days=200))

    assert hits["n"] == 0, "historical read must not trigger a refetch"
    pd.testing.assert_frame_equal(seeded, served)
