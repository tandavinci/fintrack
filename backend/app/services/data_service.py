"""The single unified market-data service (design §4).

``MarketDataService`` is the ONE public surface for OHLCV reads across all three
lanes (Tracker product shell, quant, deep). Read path:

    get_ohlcv -> resolve symbol -> normalize window (§4.4c)
              -> Redis GET (short-circuit)
              -> Postgres SELECT (two-regime coverage, §4.4a)
              -> yfinance fetch (DataFetcher-equivalent, §4.6) + upsert + warm

The returned frame is ONE canonical float64 frame on every path (cold / DB /
Redis), byte-faithful so ``compute_snapshot_id`` is identical across lanes (G2,
§4.4b). The service returns RAW OHLCV only — indicator enrichment stays in
``quant.TechnicalIndicators`` (unchanged, G2 single indicator code path).

A synchronous facade ``get_ohlcv_sync`` wraps the async core for the plain
synchronous quant/deep callers.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

from backend.app import cache as cache_mod
from backend.app.cache import (
    OHLCV_DAILY_TTL,
    OHLCV_INTRADAY_TTL,
    PREFIX_OHLCV,
    deserialize_ohlcv,
    serialize_ohlcv,
)
from backend.app.repositories.price_bar_repository import (
    PriceBarRepository,
    SyncPriceBarRepository,
)
from backend.app.services.ticker_resolution import resolve_market_ticker
from quant.config import Config

logger = logging.getLogger(__name__)

# Coverage-decision constants (design §4.4a).
FRESH_WINDOW_DAYS = 2          # a window ending within this many days of today is "recent"
DB_COVERAGE_MIN_RATIO = 0.90   # recent window must have >= this fraction of expected rows

_OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class DataServiceError(Exception):
    """Raised when a DB/network data operation fails unrecoverably (design §4.7)."""


def _ttl_for(interval: str) -> int:
    return OHLCV_INTRADAY_TTL if Config.is_intraday(interval) else OHLCV_DAILY_TTL


def _bar_to_frame_rows(bars) -> pd.DataFrame:
    """Build a byte-faithful float64 OHLCV frame from stored PriceBar rows."""
    if not bars:
        df = pd.DataFrame(columns=_OHLCV_COLUMNS, dtype="float64")
        df.index = pd.DatetimeIndex([], name="Datetime")
        return df
    index = pd.DatetimeIndex([b.ts for b in bars], name="Datetime")
    data = {
        "Open": [float(b.open) for b in bars],
        "High": [float(b.high) for b in bars],
        "Low": [float(b.low) for b in bars],
        "Close": [float(b.close) for b in bars],
        "Volume": [float(b.volume) for b in bars],
    }
    return pd.DataFrame(data, index=index).astype("float64")


def _frame_to_bar_rows(df: pd.DataFrame, symbol: str, interval: str) -> list[dict]:
    """Build upsert row dicts from an OHLCV frame (volume -> int, OHLC float)."""
    rows: list[dict] = []
    for ts, row in df.iterrows():
        rows.append(
            {
                "ticker": symbol,
                "interval": interval,
                "ts": pd.Timestamp(ts).to_pydatetime(),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row["Volume"]),
                "source": "yfinance",
            }
        )
    return rows


class MarketDataService:
    """Unified async market-data service (design §4.2)."""

    def __init__(
        self,
        repository: Optional[PriceBarRepository] = None,
        sync_repository: Optional[SyncPriceBarRepository] = None,
    ):
        self._repo = repository
        self._sync_repo = sync_repository

    @property
    def repository(self) -> PriceBarRepository:
        if self._repo is None:
            self._repo = PriceBarRepository()
        return self._repo

    @property
    def sync_repository(self) -> SyncPriceBarRepository:
        if self._sync_repo is None:
            self._sync_repo = SyncPriceBarRepository()
        return self._sync_repo

    # --- window normalization (design §4.4c) -------------------------------

    @staticmethod
    def _normalize_window(
        interval: str,
        start: date | None,
        end: date | None,
        lookback_days: int | None,
    ) -> tuple[date, date, int]:
        """Return concrete (start, end, lookback_days); end is EXCLUSIVE.

        Mirrors ``DataFetcher.fetch_historical`` clamp order EXACTLY: clamp
        ``lookback_days`` first (intraday -> 60), then default ``end`` to today,
        then derive ``start = end - (lookback_days or DEFAULT_LOOKBACK_DAYS)``.
        """
        lb = lookback_days if lookback_days is not None else Config.DEFAULT_LOOKBACK_DAYS
        if Config.is_intraday(interval) and lb > Config.INTRADAY_MAX_LOOKBACK_DAYS:
            lb = Config.INTRADAY_MAX_LOOKBACK_DAYS
        if end is None:
            end = date.today()
        if start is None:
            start = end - timedelta(days=lb)
        return start, end, lb

    @staticmethod
    def _redis_key(symbol: str, interval: str, start: date, end: date) -> str:
        return f"{PREFIX_OHLCV}{symbol}:{interval}:{start.isoformat()}:{end.isoformat()}"

    # --- input validation (design §4.8) ------------------------------------

    @staticmethod
    def _validate(ticker: str, interval: str, lookback_days: int | None, asset_class: str) -> None:
        if ticker is None or not str(ticker).strip():
            raise ValueError("ticker must be a non-empty string")
        valid_intervals = set(Config.INTRADAY_INTERVALS) | {"1d"}
        if interval not in valid_intervals:
            raise ValueError(f"interval must be one of {sorted(valid_intervals)}; got {interval!r}")
        if lookback_days is not None and lookback_days <= 0:
            raise ValueError("lookback_days must be a positive integer")
        if asset_class not in Config.ASSET_CLASSES:
            raise ValueError(f"asset_class must be one of {Config.ASSET_CLASSES}; got {asset_class!r}")

    # --- yfinance leg (design §4.6, byte-for-byte DataFetcher parity) ------

    def _fetch_yfinance(
        self, symbol: str, interval: str, start: date, end: date, lookback_days: int
    ) -> pd.DataFrame:
        """Reproduce DataFetcher's period-vs-start/end branch exactly.

        Intraday: ``history(period=f"{lookback_days}d", interval=)``.
        Daily:    ``history(start=start, end=end, interval=)`` with EXCLUSIVE end.
        Post-process: keep OHLCV only, tz_localize(None), index.name="Datetime".
        """
        stock = yf.Ticker(symbol)
        if Config.is_intraday(interval):
            df = stock.history(period=f"{lookback_days}d", interval=interval)
        else:
            df = stock.history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval=interval,
            )
        if df.empty:
            raise ValueError(
                f"No data returned for {symbol} (interval={interval}, lookback={lookback_days}d). "
                f"Verify the ticker is valid for this asset class."
            )
        df = df[_OHLCV_COLUMNS].copy()
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df.index.name = "Datetime"
        return df.astype("float64")

    # --- coverage decision (design §4.4a) ----------------------------------

    @staticmethod
    def _db_is_authoritative(end: date, stored_count: int, start: date) -> bool:
        """Two-regime coverage: historical short-circuit vs recent-ratio.

        Historical (``end < today - FRESH_WINDOW_DAYS``): >=1 stored row is
        authoritative (past windows are immutable; no refetch). Recent: require
        ``stored_count >= business_days * DB_COVERAGE_MIN_RATIO``.
        """
        today = date.today()
        if end < today - timedelta(days=FRESH_WINDOW_DAYS):
            return stored_count >= 1
        # Recent window: expected-row-count ratio (business days in [start, end)).
        expected = _business_days(start, end)
        if expected <= 0:
            return stored_count >= 1
        return stored_count >= expected * DB_COVERAGE_MIN_RATIO

    # --- async public API --------------------------------------------------

    async def get_ohlcv(
        self,
        ticker: str,
        interval: str,
        start: date | None = None,
        end: date | None = None,
        lookback_days: int | None = None,
        asset_class: str = "stock",
    ) -> pd.DataFrame:
        """Return the canonical OHLCV frame (see module docstring / §4.6)."""
        self._validate(ticker, interval, lookback_days, asset_class)
        symbol = resolve_market_ticker(ticker, asset_class)
        start_n, end_n, lb = self._normalize_window(interval, start, end, lookback_days)
        if start is not None and end is not None and not (start_n < end_n):
            raise ValueError("start must be strictly before end")

        key = self._redis_key(symbol, interval, start_n, end_n)

        # 1. Redis short-circuit (non-critical; a failure just bypasses cache).
        cached = await self._redis_get(key)
        if cached is not None:
            return cached

        start_dt = datetime(start_n.year, start_n.month, start_n.day)
        end_dt = datetime(end_n.year, end_n.month, end_n.day)

        # 2. Postgres read with two-regime coverage.
        try:
            stored_count = await self.repository.count_bars(symbol, interval, start_dt, end_dt)
        except Exception as exc:  # DB down -> fatal for this call
            raise DataServiceError(f"price_bars read failed for {symbol}: {exc}") from exc

        if self._db_is_authoritative(end_n, stored_count, start_n):
            bars = await self.repository.read_bars(symbol, interval, start_dt, end_dt)
            df = _bar_to_frame_rows(bars)
            await self._redis_set(key, df, _ttl_for(interval))
            return df

        # 3. yfinance cold fetch -> upsert -> warm Redis.
        df = self._fetch_yfinance(symbol, interval, start_n, end_n, lb)
        try:
            await self.repository.upsert_bars(_frame_to_bar_rows(df, symbol, interval))
        except Exception as exc:
            logger.warning("price_bars upsert failed for %s: %s", symbol, exc)
        await self._redis_set(key, df, _ttl_for(interval))
        return df

    # --- synchronous facade (design §4.6) ----------------------------------

    def get_ohlcv_sync(
        self,
        ticker: str,
        interval: str,
        start: date | None = None,
        end: date | None = None,
        lookback_days: int | None = None,
        asset_class: str = "stock",
    ) -> pd.DataFrame:
        """Synchronous facade over the async core for quant/deep callers.

        Uses the sync repository + a sync Redis client. Same signature, same
        normalized window, same yfinance call shape, same byte-faithful frame.
        """
        self._validate(ticker, interval, lookback_days, asset_class)
        symbol = resolve_market_ticker(ticker, asset_class)
        start_n, end_n, lb = self._normalize_window(interval, start, end, lookback_days)
        if start is not None and end is not None and not (start_n < end_n):
            raise ValueError("start must be strictly before end")

        key = self._redis_key(symbol, interval, start_n, end_n)

        cached = self._redis_get_sync(key)
        if cached is not None:
            return cached

        start_dt = datetime(start_n.year, start_n.month, start_n.day)
        end_dt = datetime(end_n.year, end_n.month, end_n.day)

        try:
            stored_count = self.sync_repository.count_bars(symbol, interval, start_dt, end_dt)
        except Exception as exc:
            raise DataServiceError(f"price_bars read failed for {symbol}: {exc}") from exc

        if self._db_is_authoritative(end_n, stored_count, start_n):
            bars = self.sync_repository.read_bars(symbol, interval, start_dt, end_dt)
            df = _bar_to_frame_rows(bars)
            self._redis_set_sync(key, df, _ttl_for(interval))
            return df

        df = self._fetch_yfinance(symbol, interval, start_n, end_n, lb)
        try:
            self.sync_repository.upsert_bars(_frame_to_bar_rows(df, symbol, interval))
        except Exception as exc:
            logger.warning("price_bars upsert failed for %s: %s", symbol, exc)
        self._redis_set_sync(key, df, _ttl_for(interval))
        return df

    # --- Redis helpers (failures are non-critical; DB is source of truth) ---

    async def _redis_get(self, key: str) -> pd.DataFrame | None:
        try:
            client = await cache_mod.get_redis()
            raw = await client.get(key)
        except Exception as exc:
            logger.warning("Redis GET failed for %s: %s", key, exc)
            return None
        if raw is None:
            return None
        try:
            return deserialize_ohlcv(raw)
        except Exception as exc:
            logger.warning("Redis OHLCV deserialize failed for %s: %s", key, exc)
            return None

    async def _redis_set(self, key: str, df: pd.DataFrame, ttl: int) -> None:
        try:
            client = await cache_mod.get_redis()
            await client.setex(key, ttl, serialize_ohlcv(df))
        except Exception as exc:
            logger.warning("Redis SET failed for %s: %s", key, exc)

    def _redis_get_sync(self, key: str) -> pd.DataFrame | None:
        try:
            client = _get_sync_redis()
            raw = client.get(key)
        except Exception as exc:
            logger.warning("Redis GET (sync) failed for %s: %s", key, exc)
            return None
        if raw is None:
            return None
        try:
            return deserialize_ohlcv(raw)
        except Exception as exc:
            logger.warning("Redis OHLCV deserialize (sync) failed for %s: %s", key, exc)
            return None

    def _redis_set_sync(self, key: str, df: pd.DataFrame, ttl: int) -> None:
        try:
            client = _get_sync_redis()
            client.setex(key, ttl, serialize_ohlcv(df))
        except Exception as exc:
            logger.warning("Redis SET (sync) failed for %s: %s", key, exc)


def _business_days(start: date, end: date) -> int:
    """Count business days (Mon-Fri) in the half-open range ``[start, end)``."""
    if end <= start:
        return 0
    days = 0
    cur = start
    one = timedelta(days=1)
    while cur < end:
        if cur.weekday() < 5:
            days += 1
        cur += one
    return days


def _get_sync_redis():
    """Create a synchronous Redis client (mirrors SRC-T insight_calculator)."""
    import redis

    from backend.app.config import settings

    return redis.from_url(settings.redis_url, decode_responses=True)
