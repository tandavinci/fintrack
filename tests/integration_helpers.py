"""Shared probes + fixtures for the real-infra integration suite (design §9.2 #6).

Every integration test guards with one of these probes and ``pytest.skip`` with a
clear reason when the backing source (Postgres / Redis / yfinance) is unreachable,
so `pytest` on a bare machine stays GREEN with the integration tests SKIPPED (not
failed). This mirrors ``tests/test_gateway_live._ollama_up`` + ``pytest.skip``.

These helpers are imported by the ``@pytest.mark.integration`` test modules; they
are not themselves collected as tests.
"""

from __future__ import annotations

import socket
from datetime import date, datetime, timedelta

import pytest

from backend.app.database import create_sync_session, get_sync_database_url
from backend.app.models.data_snapshot import DataSnapshot
from backend.app.models.news_item import NewsItem
from backend.app.models.price_bar import PriceBar

# --- the golden set this FEAT materializes (tests/golden/README.md) ----------

# Golden set (user decision, Option A): 6 symbols switched .BO -> .NS because
# Yahoo's .BO coverage for them is dead (stable across retries) while every .NS
# equivalent returns real data; the 4 live .BO symbols are kept so the set
# exercises BOTH exchanges. See tests/golden/README.md for the full rationale.
GOLDEN_TICKERS = [
    "RELIANCE.NS",
    "TCS.BO",
    "HDFCBANK.NS",
    "INFY.NS",
    "ICICIBANK.NS",
    "CUMMINSIND.NS",
    "PERSISTENT.NS",
    "MPHASIS.BO",
    "YESBANK.BO",
    "IDEA.BO",
]

# A known BSE trading session (a Monday, not a holiday) used as the as_of anchor
# for the trading-day boundary tests (§9.2 #2a). Chosen well in the past so the
# window is historical (DB-authoritative) and reproducible.
TRADING_DAY_AS_OF = date(2024, 1, 15)  # Mon 2024-01-15, a BSE session


# --- reachability probes (skip-with-reason, never fail) ----------------------


def postgres_up() -> bool:
    """True only when a sync psycopg2 connection opens and a trivial SELECT runs."""
    try:
        session = create_sync_session()
        try:
            from sqlalchemy import text

            session.execute(text("SELECT 1"))
            return True
        finally:
            session.close()
    except Exception:
        return False


def redis_up() -> bool:
    """True only when the sync Redis client PINGs successfully."""
    try:
        import redis

        from backend.app.config import settings

        client = redis.from_url(settings.redis_url, decode_responses=True)
        return bool(client.ping())
    except Exception:
        return False


def yfinance_up(symbol: str | None = None) -> bool:
    """True when yfinance can fetch at least one golden ticker from Yahoo.

    The probe answers "is the yfinance -> Yahoo path working", NOT "is one
    specific symbol listed". A single golden symbol that Yahoo no longer
    serves (its BSE coverage for some symbols has gaps) must NOT make the whole
    integration suite skip while the data path is actually healthy. So when no
    explicit ``symbol`` is given, the probe tries the golden tickers in order
    over a 200-day window (the window the suite materializes against) and
    succeeds on the FIRST one that returns rows.
    """
    try:
        import yfinance as yf
    except Exception:
        return False

    candidates = [symbol] if symbol else GOLDEN_TICKERS
    end = date.today()
    start = end - timedelta(days=200)
    for sym in candidates:
        try:
            df = yf.Ticker(sym).history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval="1d",
            )
            if not df.empty:
                return True
        except Exception:
            continue
    return False


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# --- pytest fixtures ----------------------------------------------------------


_SHARED_LOOP = None


def _get_shared_loop():
    """One persistent event loop reused by every ``run_async`` in the process.

    The Tracker app caches an async SQLAlchemy engine (``backend.app.database``)
    and an async Redis client (``backend.app.cache``) at module scope. Under the
    real app this is correct — they live for the app's single, long-lived event
    loop. A bare ``asyncio.run(...)`` per call, by contrast, creates then CLOSES
    a fresh loop each time, so the module singletons (and their asyncpg pool /
    Redis connections) end up bound to a CLOSED loop; the next call then fails
    with "Event loop is closed" (Redis) or asyncpg's "'NoneType' object has no
    attribute 'send'" (dead connection), and the pool later errors terminating
    those connections on a different loop.

    Reusing ONE loop for the whole test session matches production's single-loop
    model exactly, so the cached engine/Redis are always driven by the loop that
    created them. Production code is untouched — only the integration tests use
    this helper.
    """
    import asyncio

    global _SHARED_LOOP
    if _SHARED_LOOP is None or _SHARED_LOOP.is_closed():
        _SHARED_LOOP = asyncio.new_event_loop()
    return _SHARED_LOOP


def run_async(coro):
    """Run a coroutine on the shared session event loop.

    Use this in the integration suite in place of a bare ``asyncio.run`` whenever
    more than one async call happens in a process (which is always, across
    parametrized cases and the cold/DB/Redis sequences). All calls share one loop
    so the module-level async engine + Redis client stay bound to a live loop."""
    import asyncio

    loop = _get_shared_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@pytest.fixture
def require_postgres():
    if not postgres_up():
        pytest.skip(f"Postgres unreachable at {get_sync_database_url()}")


@pytest.fixture
def require_redis():
    if not redis_up():
        pytest.skip("Redis unreachable")


@pytest.fixture
def require_yfinance():
    if not yfinance_up():
        pytest.skip("yfinance unreachable")


@pytest.fixture
def flush_ohlcv_cache():
    """Flush every ``ohlcv:*`` Redis key so a read is forced down to Postgres."""
    import redis

    from backend.app.cache import PREFIX_OHLCV
    from backend.app.config import settings

    client = redis.from_url(settings.redis_url, decode_responses=True)

    def _flush():
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor=cursor, match=f"{PREFIX_OHLCV}*", count=200)
            if keys:
                client.delete(*keys)
            if cursor == 0:
                break

    _flush()
    yield _flush
    _flush()


@pytest.fixture
def clean_price_bars():
    """Delete price_bars rows for the golden tickers before and after a test."""
    from sqlalchemy import delete

    def _clean():
        session = create_sync_session()
        try:
            session.execute(delete(PriceBar).where(PriceBar.ticker.in_(GOLDEN_TICKERS)))
            session.commit()
        finally:
            session.close()

    _clean()
    yield _clean
    _clean()


def count_price_bars(ticker: str, interval: str = "1d") -> int:
    """Count stored price_bars rows for a resolved ``ticker``/``interval``."""
    from sqlalchemy import func, select

    session = create_sync_session()
    try:
        stmt = (
            select(func.count())
            .select_from(PriceBar)
            .where(PriceBar.ticker == ticker, PriceBar.interval == interval)
        )
        return int(session.execute(stmt).scalar_one())
    finally:
        session.close()


def count_news_items() -> int:
    from sqlalchemy import func, select

    session = create_sync_session()
    try:
        return int(
            session.execute(select(func.count()).select_from(NewsItem)).scalar_one()
        )
    finally:
        session.close()


def count_snapshots(snapshot_id: str) -> int:
    from sqlalchemy import func, select

    session = create_sync_session()
    try:
        stmt = (
            select(func.count())
            .select_from(DataSnapshot)
            .where(DataSnapshot.snapshot_id == snapshot_id)
        )
        return int(session.execute(stmt).scalar_one())
    finally:
        session.close()
