"""PriceBar repository (design §5.3).

Async upsert + point-in-time read of ``price_bars``, plus sync variants for the
synchronous facade the quant/deep lanes call. Upsert uses Postgres
``INSERT ... ON CONFLICT (ticker, interval, ts) DO UPDATE`` so a re-fetch of the
same bar overwrites OHLCV with the latest yfinance values and bumps
``fetched_at`` without ever creating a duplicate row. Reads are EXCLUSIVE on the
upper bound (``ts < end``) — no lookahead, and the DB-served frame carries the
SAME rows a cold yfinance fetch would (design §4.4c/§5.3).
"""

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.app.database import async_session_factory, create_sync_session
from backend.app.models.price_bar import PriceBar

# Columns updated on conflict (identity columns ticker/interval/ts are excluded).
_UPSERT_UPDATE_COLS = ("open", "high", "low", "close", "volume", "source")


def _upsert_statement(rows: list[dict]):
    """Build the ON CONFLICT DO UPDATE statement for a batch of bar dicts."""
    stmt = pg_insert(PriceBar).values(rows)
    set_ = {col: getattr(stmt.excluded, col) for col in _UPSERT_UPDATE_COLS}
    set_["fetched_at"] = func.now()
    return stmt.on_conflict_do_update(
        index_elements=["ticker", "interval", "ts"],
        set_=set_,
    )


class PriceBarRepository:
    """Repository for price_bars DB access (async core + sync variants)."""

    def __init__(self, session: Optional[AsyncSession] = None):
        self._session = session

    def _get_session(self) -> AsyncSession:
        return self._session if self._session is not None else async_session_factory()

    # --- async API ---------------------------------------------------------

    async def upsert_bars(self, rows: list[dict]) -> int:
        """Upsert a batch of bar dicts. Returns the number of input rows."""
        if not rows:
            return 0
        session = self._get_session()
        owns = self._session is None
        try:
            await session.execute(_upsert_statement(rows))
            await session.commit()
            return len(rows)
        finally:
            if owns:
                await session.close()

    async def read_bars(
        self, ticker: str, interval: str, start: datetime, end: datetime
    ) -> Sequence[PriceBar]:
        """Point-in-time read: ``ts >= start AND ts < end`` (EXCLUSIVE end)."""
        session = self._get_session()
        owns = self._session is None
        try:
            stmt = (
                select(PriceBar)
                .where(
                    PriceBar.ticker == ticker,
                    PriceBar.interval == interval,
                    PriceBar.ts >= start,
                    PriceBar.ts < end,
                )
                .order_by(PriceBar.ts)
            )
            result = await session.execute(stmt)
            return result.scalars().all()
        finally:
            if owns:
                await session.close()

    async def count_bars(
        self, ticker: str, interval: str, start: datetime, end: datetime
    ) -> int:
        """Stored-row-count in ``[start, end)`` — drives the coverage decision."""
        session = self._get_session()
        owns = self._session is None
        try:
            stmt = (
                select(func.count())
                .select_from(PriceBar)
                .where(
                    PriceBar.ticker == ticker,
                    PriceBar.interval == interval,
                    PriceBar.ts >= start,
                    PriceBar.ts < end,
                )
            )
            result = await session.execute(stmt)
            return int(result.scalar_one())
        finally:
            if owns:
                await session.close()


class SyncPriceBarRepository:
    """Synchronous variant of :class:`PriceBarRepository` for the sync facade."""

    def __init__(self, session: Optional[Session] = None):
        self._session = session

    def _get_session(self) -> Session:
        return self._session if self._session is not None else create_sync_session()

    def upsert_bars(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        session = self._get_session()
        owns = self._session is None
        try:
            session.execute(_upsert_statement(rows))
            session.commit()
            return len(rows)
        finally:
            if owns:
                session.close()

    def read_bars(
        self, ticker: str, interval: str, start: datetime, end: datetime
    ) -> Sequence[PriceBar]:
        session = self._get_session()
        owns = self._session is None
        try:
            stmt = (
                select(PriceBar)
                .where(
                    PriceBar.ticker == ticker,
                    PriceBar.interval == interval,
                    PriceBar.ts >= start,
                    PriceBar.ts < end,
                )
                .order_by(PriceBar.ts)
            )
            return session.execute(stmt).scalars().all()
        finally:
            if owns:
                session.close()

    def count_bars(
        self, ticker: str, interval: str, start: datetime, end: datetime
    ) -> int:
        session = self._get_session()
        owns = self._session is None
        try:
            stmt = (
                select(func.count())
                .select_from(PriceBar)
                .where(
                    PriceBar.ticker == ticker,
                    PriceBar.interval == interval,
                    PriceBar.ts >= start,
                    PriceBar.ts < end,
                )
            )
            return int(session.execute(stmt).scalar_one())
        finally:
            if owns:
                session.close()
