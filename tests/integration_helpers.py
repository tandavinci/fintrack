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

GOLDEN_TICKERS = [
    "RELIANCE.BO",
    "TCS.BO",
    "HDFCBANK.BO",
    "INFY.BO",
    "ICICIBANK.BO",
    "CUMMINSIND.BO",
    "PERSISTENT.BO",
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


def yfinance_up(symbol: str = "RELIANCE.BO") -> bool:
    """True only when a tiny yfinance daily fetch returns at least one row."""
    try:
        import yfinance as yf

        end = date.today()
        start = end - timedelta(days=10)
        df = yf.Ticker(symbol).history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval="1d",
        )
        return not df.empty
    except Exception:
        return False


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# --- pytest fixtures ----------------------------------------------------------


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
