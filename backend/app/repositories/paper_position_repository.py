"""PaperPosition repository (Step 6 / G6 deliverable 4 — paper trading).

A sync write/read path for the mark-to-market Celery task and an async read path
for the read-only API, mirroring the split in ``signal_alert_repository`` /
``recommendation_repository``.

The sync side carries the full lifecycle the job needs: find the OPEN position
for a (ticker, source), OPEN a new one (idempotent on
``(ticker, source, opened_as_of)``), MARK an open position to market, and CLOSE
it (realize P&L). Session lifecycle matches the sibling repos: an injected
session is reused and left open; a self-opened session is closed in ``finally``.
"""

from datetime import date
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.app.database import async_session_factory, create_sync_session
from backend.app.models.paper_position import PAPER_NOTIONAL, PaperPosition
from backend.app.services.paper_trading_service import paper_quantity


class SyncPaperPositionRepository:
    """Synchronous write/read path for the mark-to-market Celery task."""

    def __init__(self, session: Optional[Session] = None):
        self._session = session

    def _get_session(self) -> Session:
        return self._session if self._session is not None else create_sync_session()

    def get_open(self, ticker: str, source: str) -> Optional[PaperPosition]:
        """Return the single OPEN position for (ticker, source), or None."""
        session = self._get_session()
        owns = self._session is None
        try:
            stmt = (
                select(PaperPosition)
                .where(
                    PaperPosition.ticker == ticker,
                    PaperPosition.source == source,
                    PaperPosition.status == "open",
                )
                .order_by(PaperPosition.opened_as_of.desc())
                .limit(1)
            )
            return session.execute(stmt).scalars().first()
        finally:
            if owns:
                session.close()

    def open_position(
        self,
        *,
        ticker: str,
        source: str,
        opened_as_of: date,
        entry_price: float,
        notional: float = PAPER_NOTIONAL,
    ) -> bool:
        """Open a paper position; idempotent on (ticker, source, opened_as_of).

        Returns True iff a NEW row was inserted (a same-day re-run is a no-op via
        ON CONFLICT DO NOTHING). Entry quantity is the fixed-notional sizing.
        """
        session = self._get_session()
        owns = self._session is None
        try:
            values = {
                "ticker": ticker,
                "source": source,
                "opened_as_of": opened_as_of,
                "entry_price": entry_price,
                "notional": notional,
                "quantity": paper_quantity(entry_price, notional),
                "status": "open",
            }
            stmt = pg_insert(PaperPosition).values(values).on_conflict_do_nothing(
                index_elements=["ticker", "source", "opened_as_of"]
            )
            result = session.execute(stmt)
            session.commit()
            return int(result.rowcount or 0) > 0
        finally:
            if owns:
                session.close()

    def mark_open(
        self,
        position_id: int,
        *,
        mark_date: date,
        mark_price: float,
        unrealized_pnl_pct: float,
    ) -> None:
        """Update an open position's latest mark + unrealized P&L in place."""
        session = self._get_session()
        owns = self._session is None
        try:
            pos = session.get(PaperPosition, position_id)
            if pos is None or pos.status != "open":
                return
            pos.last_mark_date = mark_date
            pos.last_mark_price = mark_price
            pos.unrealized_pnl_pct = unrealized_pnl_pct
            session.commit()
        finally:
            if owns:
                session.close()

    def close_position(
        self,
        position_id: int,
        *,
        closed_as_of: date,
        exit_price: float,
        realized_pnl_pct: float,
        close_reason: str,
    ) -> None:
        """Close an open position, realizing its P&L."""
        session = self._get_session()
        owns = self._session is None
        try:
            pos = session.get(PaperPosition, position_id)
            if pos is None or pos.status != "open":
                return
            pos.status = "closed"
            pos.closed_as_of = closed_as_of
            pos.exit_price = exit_price
            pos.realized_pnl_pct = realized_pnl_pct
            pos.close_reason = close_reason
            # A closed position's last mark is its exit.
            pos.last_mark_date = closed_as_of
            pos.last_mark_price = exit_price
            pos.unrealized_pnl_pct = realized_pnl_pct
            session.commit()
        finally:
            if owns:
                session.close()

    def list_by_source(
        self, source: str, status: Optional[str] = None
    ) -> Sequence[PaperPosition]:
        """All positions for a source, optionally filtered by status."""
        session = self._get_session()
        owns = self._session is None
        try:
            stmt = select(PaperPosition).where(PaperPosition.source == source)
            if status is not None:
                stmt = stmt.where(PaperPosition.status == status)
            stmt = stmt.order_by(PaperPosition.opened_as_of.asc())
            return session.execute(stmt).scalars().all()
        finally:
            if owns:
                session.close()


class PaperPositionRepository:
    """Async read path for the read-only API."""

    def __init__(self, session: Optional[AsyncSession] = None):
        self._session = session

    def _get_session(self) -> AsyncSession:
        return self._session if self._session is not None else async_session_factory()

    async def by_source(
        self, source: str, status: Optional[str] = None, limit: int = 200
    ) -> Sequence[PaperPosition]:
        """Positions for a source (optionally by status), newest open first."""
        session = self._get_session()
        owns = self._session is None
        try:
            stmt = select(PaperPosition).where(PaperPosition.source == source)
            if status is not None:
                stmt = stmt.where(PaperPosition.status == status)
            stmt = stmt.order_by(PaperPosition.opened_as_of.desc()).limit(limit)
            result = await session.execute(stmt)
            return result.scalars().all()
        finally:
            if owns:
                await session.close()
