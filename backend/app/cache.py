"""
Redis caching utilities for BSE Stock Investment Tracker.

Provides async Redis client, get/set/delete helpers with TTL support,
and domain-specific cache invalidation patterns.
"""

import json
from typing import Any

import redis.asyncio as redis

from backend.app.config import settings

# TTL constants (in seconds)
STOCK_PRICE_TTL = 60          # Stock prices: 60 seconds
SEARCH_RESULTS_TTL = 300      # Search results: 5 minutes
DASHBOARD_TTL = 60            # Dashboard data: 60 seconds
TREND_SCORE_TTL = 86400       # Trend scores: 24 hours
OHLCV_INTRADAY_TTL = 300      # OHLCV intraday bars: 5 minutes (churn intra-session)
OHLCV_DAILY_TTL = 86400       # OHLCV daily bars: 24 hours (stable once closed)

# Cache key prefixes
PREFIX_STOCK_PRICE = "stock:price:"
PREFIX_SEARCH = "stock:search:"
PREFIX_DASHBOARD = "dashboard:"
PREFIX_TREND = "stock:trend:"
PREFIX_OHLCV = "ohlcv:"       # ohlcv:{resolved_symbol}:{interval}:{start_norm}:{end_norm}

# Redis connection pool (initialized lazily)
_redis_pool: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    """Get or create the async Redis client connection."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_pool


async def close_redis() -> None:
    """Close the Redis connection pool. Call on application shutdown."""
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.close()
        _redis_pool = None


# --- Generic cache helpers ---


async def cache_get(key: str) -> Any | None:
    """
    Retrieve a value from cache by key.

    Returns the deserialized value, or None if key doesn't exist or is expired.
    """
    client = await get_redis()
    value = await client.get(key)
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


async def cache_set(key: str, value: Any, ttl: int | None = None) -> None:
    """
    Store a value in cache with optional TTL.

    Args:
        key: Cache key string.
        value: Value to cache (will be JSON-serialized).
        ttl: Time-to-live in seconds. If None, the key won't expire.
    """
    client = await get_redis()
    serialized = json.dumps(value)
    if ttl is not None:
        await client.setex(key, ttl, serialized)
    else:
        await client.set(key, serialized)


async def cache_delete(key: str) -> None:
    """Delete a single cache key."""
    client = await get_redis()
    await client.delete(key)


async def cache_delete_pattern(pattern: str) -> None:
    """
    Delete all cache keys matching a glob pattern.

    Uses SCAN to avoid blocking on large key spaces.
    """
    client = await get_redis()
    cursor = 0
    while True:
        cursor, keys = await client.scan(cursor=cursor, match=pattern, count=100)
        if keys:
            await client.delete(*keys)
        if cursor == 0:
            break


# --- OHLCV frame (de)serialization (design §4.5, byte-faithful, G2) ---
#
# The frame cells are numpy float64 scalars which json.dumps cannot serialize
# directly. We cast each cell np.float64 -> Python float FIRST (lossless: both
# are IEEE-754 binary64), so json.dumps emits the shortest round-tripping decimal
# and json.loads reconstructs the identical double. No round()/reduced-precision
# step is ever reached — a lossy round would break §4.4b and is forbidden here.

_OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def serialize_ohlcv(df) -> str:
    """Serialize an OHLCV DataFrame to a JSON string (byte-faithful float64).

    Produces JSON records: a tz-naive ISO timestamp per row plus OHLCV as JSON
    numbers that are the EXACT IEEE-754 doubles of the frame's float64 cells.
    """
    import pandas as pd  # local import keeps cache.py importable without pandas

    records = []
    for ts, row in df.iterrows():
        rec = {"ts": pd.Timestamp(ts).isoformat()}
        for col in _OHLCV_COLUMNS:
            if col in df.columns:
                rec[col] = float(row[col])  # np.float64 -> Python float (lossless)
        records.append(rec)
    return json.dumps(records)


def deserialize_ohlcv(payload: str):
    """Deserialize a JSON string back to the EXACT float64 OHLCV frame.

    Returns a DataFrame with a tz-naive DatetimeIndex named ``Datetime`` and the
    columns present in the payload, cast to float64. ``float(repr(x)) == x`` so
    the round-trip is bit-identical to the frame that was cached.
    """
    import pandas as pd

    records = json.loads(payload)
    if not records:
        df = pd.DataFrame(columns=_OHLCV_COLUMNS, dtype="float64")
        df.index = pd.DatetimeIndex([], name="Datetime")
        return df

    index = pd.DatetimeIndex([pd.Timestamp(r["ts"]) for r in records], name="Datetime")
    cols = [c for c in _OHLCV_COLUMNS if c in records[0]]
    data = {c: [r[c] for r in records] for c in cols}
    df = pd.DataFrame(data, index=index)
    return df.astype("float64")


# --- Domain-specific cache operations ---


async def get_stock_price(stock_id: int) -> Any | None:
    """Get cached stock price data."""
    key = f"{PREFIX_STOCK_PRICE}{stock_id}"
    return await cache_get(key)


async def set_stock_price(stock_id: int, price_data: Any) -> None:
    """Cache stock price data with 60s TTL."""
    key = f"{PREFIX_STOCK_PRICE}{stock_id}"
    await cache_set(key, price_data, ttl=STOCK_PRICE_TTL)


async def invalidate_stock_price(stock_id: int) -> None:
    """Invalidate cached stock price for a specific stock."""
    key = f"{PREFIX_STOCK_PRICE}{stock_id}"
    await cache_delete(key)


async def invalidate_all_stock_prices() -> None:
    """Invalidate all cached stock prices."""
    await cache_delete_pattern(f"{PREFIX_STOCK_PRICE}*")


async def get_search_results(query_hash: str) -> Any | None:
    """Get cached search results."""
    key = f"{PREFIX_SEARCH}{query_hash}"
    return await cache_get(key)


async def set_search_results(query_hash: str, results: Any) -> None:
    """Cache search results with 5-minute TTL."""
    key = f"{PREFIX_SEARCH}{query_hash}"
    await cache_set(key, results, ttl=SEARCH_RESULTS_TTL)


async def invalidate_search_results() -> None:
    """Invalidate all cached search results."""
    await cache_delete_pattern(f"{PREFIX_SEARCH}*")


async def get_dashboard_data(user_id: int) -> Any | None:
    """Get cached dashboard data for a user."""
    key = f"{PREFIX_DASHBOARD}{user_id}"
    return await cache_get(key)


async def set_dashboard_data(user_id: int, data: Any) -> None:
    """Cache dashboard data with 60s TTL."""
    key = f"{PREFIX_DASHBOARD}{user_id}"
    await cache_set(key, data, ttl=DASHBOARD_TTL)


async def invalidate_dashboard(user_id: int) -> None:
    """Invalidate cached dashboard data for a specific user."""
    key = f"{PREFIX_DASHBOARD}{user_id}"
    await cache_delete(key)


async def invalidate_all_dashboards() -> None:
    """Invalidate all cached dashboard data."""
    await cache_delete_pattern(f"{PREFIX_DASHBOARD}*")


async def get_trend_score(stock_id: int) -> Any | None:
    """Get cached trend score data."""
    key = f"{PREFIX_TREND}{stock_id}"
    return await cache_get(key)


async def set_trend_score(stock_id: int, trend_data: Any) -> None:
    """Cache trend score data with 24h TTL."""
    key = f"{PREFIX_TREND}{stock_id}"
    await cache_set(key, trend_data, ttl=TREND_SCORE_TTL)


async def invalidate_trend_score(stock_id: int) -> None:
    """Invalidate cached trend score for a specific stock."""
    key = f"{PREFIX_TREND}{stock_id}"
    await cache_delete(key)


async def invalidate_all_trend_scores() -> None:
    """Invalidate all cached trend scores."""
    await cache_delete_pattern(f"{PREFIX_TREND}*")
