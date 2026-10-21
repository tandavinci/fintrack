"""No-infra unit tests for the unified MarketDataService (design §9.4).

Everything here runs OFFLINE (no Postgres / no Redis / no network). yfinance and
the price-bar repositories are stubbed/spied, so the tests assert the pure
behaviour of the service: ticker-resolution idempotency, window normalization +
Redis-key / yfinance-call-shape parity across the (end+lookback) and
(explicit-start) forms with an EXCLUSIVE end, the two-regime coverage decision,
and the sync-facade wrapper over a stubbed core.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from backend.app.services import data_service as ds
from backend.app.services.data_service import (
    DB_COVERAGE_MIN_RATIO,
    FRESH_WINDOW_DAYS,
    MarketDataService,
)
from backend.app.services.ticker_resolution import resolve_market_ticker
from quant.config import Config


# --- ticker resolution idempotency -------------------------------------------


def test_resolve_market_ticker_appends_bo_for_bare_stock():
    assert resolve_market_ticker("RELIANCE") == "RELIANCE.BO"
    assert resolve_market_ticker(" reliance ") == "RELIANCE.BO"


def test_resolve_market_ticker_is_idempotent():
    for raw, asset in [
        ("RELIANCE", "stock"),
        ("RELIANCE.BO", "stock"),
        ("TCS.NS", "stock"),
        ("EURUSD", "forex"),
        ("GOLD", "commodity"),
    ]:
        once = resolve_market_ticker(raw, asset)
        assert resolve_market_ticker(once, asset) == once


def test_resolve_market_ticker_rejects_empty():
    with pytest.raises(ValueError):
        resolve_market_ticker("   ")


# --- helpers: stub the yfinance leg & repositories ---------------------------


def _fake_daily_frame(n: int = 10) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-01", periods=n)
    df = pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(n)],
            "High": [101.0 + i for i in range(n)],
            "Low": [99.0 + i for i in range(n)],
            "Close": [100.5 + i for i in range(n)],
            "Volume": [1_000_000 + i for i in range(n)],
        },
        index=idx,
    )
    df.index.name = "Datetime"
    return df


class _SpyTicker:
    """Captures the exact yf.Ticker(...).history(**kwargs) call shape."""

    calls: list = []

    def __init__(self, symbol):
        self.symbol = symbol

    def history(self, **kwargs):
        _SpyTicker.calls.append({"symbol": self.symbol, **kwargs})
        return _fake_daily_frame()


class _StubSyncRepo:
    """Sync repository stub: fixed stored count, records upserts."""

    def __init__(self, stored_count: int = 0):
        self.stored_count = stored_count
        self.upserts: list = []

    def count_bars(self, ticker, interval, start, end):
        return self.stored_count

    def read_bars(self, ticker, interval, start, end):
        return []

    def upsert_bars(self, rows):
        self.upserts.append(rows)
        return len(rows)


@pytest.fixture
def spy_yf(monkeypatch):
    _SpyTicker.calls = []
    monkeypatch.setattr(ds.yf, "Ticker", _SpyTicker)
    # Neutralize the sync Redis client so the facade never touches a server.
    monkeypatch.setattr(MarketDataService, "_redis_get_sync", lambda self, key: None)
    monkeypatch.setattr(MarketDataService, "_redis_set_sync", lambda self, key, df, ttl: None)
    return _SpyTicker


# --- window normalization + key / fetch-shape parity (EXCLUSIVE end) ---------


def test_daily_key_and_fetch_parity_end_lookback_vs_explicit_start(spy_yf):
    """end+lookback and explicit-start forms derive the SAME normalized window,
    the SAME Redis key, and the SAME daily history(start=,end=,interval=) call
    with an EXCLUSIVE end."""
    repo_a = _StubSyncRepo(stored_count=0)
    repo_b = _StubSyncRepo(stored_count=0)
    svc_a = MarketDataService(sync_repository=repo_a)
    svc_b = MarketDataService(sync_repository=repo_b)

    end = date(2024, 6, 3)
    start = end - timedelta(days=200)

    key_a = MarketDataService._redis_key("RELIANCE.BO", "1d", *MarketDataService._normalize_window("1d", None, end, 200)[:2])
    key_b = MarketDataService._redis_key("RELIANCE.BO", "1d", *MarketDataService._normalize_window("1d", start, end, None)[:2])
    assert key_a == key_b

    svc_a.get_ohlcv_sync("RELIANCE", "1d", end=end, lookback_days=200)
    svc_b.get_ohlcv_sync("RELIANCE", "1d", start=start, end=end)

    assert len(spy_yf.calls) == 2
    call_a, call_b = spy_yf.calls
    # Same daily start/end/interval call shape, EXCLUSIVE end == the as_of date.
    assert call_a == call_b
    assert call_a["symbol"] == "RELIANCE.BO"
    assert call_a["start"] == start.strftime("%Y-%m-%d")
    assert call_a["end"] == end.strftime("%Y-%m-%d")  # exclusive upper bound
    assert call_a["interval"] == "1d"
    assert "period" not in call_a  # daily uses start/end, never period


def test_intraday_clamp_and_period_fetch_shape(spy_yf):
    """Intraday clamps lookback to 60 and uses history(period='60d', interval=)."""
    repo = _StubSyncRepo(stored_count=0)
    svc = MarketDataService(sync_repository=repo)

    svc.get_ohlcv_sync("RELIANCE", "30m", lookback_days=200)

    assert len(spy_yf.calls) == 1
    call = spy_yf.calls[0]
    assert call["symbol"] == "RELIANCE.BO"
    assert call["period"] == f"{Config.INTRADAY_MAX_LOOKBACK_DAYS}d"
    assert call["period"] == "60d"
    assert call["interval"] == "30m"
    assert "start" not in call and "end" not in call


def test_normalize_window_mirrors_fetcher_clamp_order():
    # Daily: start derived from DEFAULT_LOOKBACK_DAYS when lookback is None.
    end = date(2024, 6, 3)
    s, e, lb = MarketDataService._normalize_window("1d", None, end, None)
    assert e == end
    assert lb == Config.DEFAULT_LOOKBACK_DAYS
    assert s == end - timedelta(days=Config.DEFAULT_LOOKBACK_DAYS)
    # Intraday clamp applied to lookback before deriving start.
    s2, e2, lb2 = MarketDataService._normalize_window("5m", None, end, 500)
    assert lb2 == Config.INTRADAY_MAX_LOOKBACK_DAYS
    assert s2 == end - timedelta(days=Config.INTRADAY_MAX_LOOKBACK_DAYS)


# --- two-regime coverage decision (design §4.4a) -----------------------------


def test_coverage_historical_single_row_is_authoritative():
    """Historical window (end well before today): >=1 stored row short-circuits."""
    today = date.today()
    hist_end = today - timedelta(days=FRESH_WINDOW_DAYS + 30)
    hist_start = hist_end - timedelta(days=200)
    assert MarketDataService._db_is_authoritative(hist_end, stored_count=1, start=hist_start) is True
    assert MarketDataService._db_is_authoritative(hist_end, stored_count=0, start=hist_start) is False


def test_coverage_recent_uses_ratio_floor():
    """Recent window: below the expected-count ratio floor => refetch (not authoritative)."""
    today = date.today()
    recent_end = today
    recent_start = today - timedelta(days=10)
    expected = ds._business_days(recent_start, recent_end)
    floor = expected * DB_COVERAGE_MIN_RATIO
    assert MarketDataService._db_is_authoritative(recent_end, stored_count=expected, start=recent_start) is True
    assert MarketDataService._db_is_authoritative(recent_end, stored_count=0, start=recent_start) is False
    # Just below the floor -> not authoritative.
    below = max(0, int(floor) - 1)
    assert MarketDataService._db_is_authoritative(recent_end, stored_count=below, start=recent_start) is False


def test_recent_window_authoritative_serves_from_db_no_fetch(spy_yf):
    """When the DB is authoritative, the sync facade serves from the DB and
    does NOT hit yfinance."""
    today = date.today()
    # Historical window with a stored row -> authoritative, read_bars path.
    end = today - timedelta(days=FRESH_WINDOW_DAYS + 30)

    class _AuthRepo(_StubSyncRepo):
        def count_bars(self, ticker, interval, start, end):
            return 5

        def read_bars(self, ticker, interval, start, end):
            # Return two fake PriceBar-like rows.
            class _Bar:
                def __init__(self, ts, o, h, l, c, v):
                    self.ts, self.open, self.high, self.low, self.close, self.volume = ts, o, h, l, c, v

            return [
                _Bar(datetime(2024, 1, 2), 1.0, 2.0, 0.5, 1.5, 100),
                _Bar(datetime(2024, 1, 3), 1.5, 2.5, 1.0, 2.0, 200),
            ]

    svc = MarketDataService(sync_repository=_AuthRepo())
    df = svc.get_ohlcv_sync("RELIANCE", "1d", end=end, lookback_days=200)
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(df) == 2
    assert df.index.name == "Datetime"
    assert str(df["Close"].dtype) == "float64"
    assert spy_yf.calls == []  # DB-authoritative => no yfinance fetch


# --- sync-facade over a stubbed core + upsert-on-cold-fetch ------------------


def test_sync_facade_cold_fetch_upserts_and_returns_float_frame(spy_yf):
    """A cold fetch (empty DB, recent window) fetches, upserts, returns float64."""
    repo = _StubSyncRepo(stored_count=0)
    svc = MarketDataService(sync_repository=repo)

    df = svc.get_ohlcv_sync("RELIANCE", "1d", end=date.today(), lookback_days=30)

    assert len(spy_yf.calls) == 1  # went to yfinance
    assert len(repo.upserts) == 1  # upserted the cold frame
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df.index.name == "Datetime"
    for col in df.columns:
        assert str(df[col].dtype) == "float64"
    # Upsert rows carry the resolved symbol + integral volume.
    row = repo.upserts[0][0]
    assert row["ticker"] == "RELIANCE.BO"
    assert row["interval"] == "1d"
    assert isinstance(row["volume"], int)


def test_input_validation(spy_yf):
    svc = MarketDataService(sync_repository=_StubSyncRepo())
    with pytest.raises(ValueError):
        svc.get_ohlcv_sync("", "1d")
    with pytest.raises(ValueError):
        svc.get_ohlcv_sync("RELIANCE", "7x")
    with pytest.raises(ValueError):
        svc.get_ohlcv_sync("RELIANCE", "1d", lookback_days=0)
    with pytest.raises(ValueError):
        svc.get_ohlcv_sync("RELIANCE", "1d", asset_class="crypto")
    with pytest.raises(ValueError):
        svc.get_ohlcv_sync(
            "RELIANCE", "1d", start=date(2024, 6, 3), end=date(2024, 6, 1)
        )
