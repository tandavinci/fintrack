"""SignalAlert repository (Step 5 / G5 system-generated alerts).

A sync upsert path for the generator (a synchronous Celery task) and an async
read path for the read-only API, mirroring the split in
``recommendation_repository`` / ``news_repository``.

Upsert uses Postgres ``INSERT ... ON CONFLICT (dedup_hash) DO NOTHING`` so
re-running the generator for a day never duplicates an alert already stored —
same idempotent-ingest pattern as ``news_items``. ``upsert`` reports whether its
single row was actually inserted; ``upsert_many`` returns the count of new rows.

Session lifecycle matches the sibling repos: an injected session is reused and
left open; a session the repo opens itself is closed in ``finally``.
"""

from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.app.database import async_session_factory, create_sync_session
from backend.app.models.signal_alert import SignalAlert

# Columns carried on the dedup insert (dedup_hash is the conflict target).
_INSERT_COLS = ("ticker", "alert_kind", "severity", "title", "detail", "as_of", "dedup_hash")


def _row_values(alert: dict) -> dict:
    """Project an alert dict to the table columns (defensive defaults applied)."""
    return {
        "ticker": alert.get("ticker"),
        "alert_kind": alert["alert_kind"],
        "severity": alert.get("severity", "info"),
        "title": alert["title"],
        "detail": alert.get("detail", ""),
        "as_of": alert["as_of"],
        "dedup_hash": alert["dedup_hash"],
    }


def _insert_statement(rows: list[dict]):
    """Build the ON CONFLICT (dedup_hash) DO NOTHING statement for a batch."""
    stmt = pg_insert(SignalAlert).values([_row_values(r) for r in rows])
    return stmt.on_conflict_do_nothing(index_elements=["dedup_hash"])


def _recent_statement(limit: int, kind: Optional[str], ticker: Optional[str]):
    """Build the recent-alerts SELECT (as_of desc, created_at desc) + filters."""
    conditions = []
    if kind is not None:
        conditions.append(SignalAlert.alert_kind == kind)
    if ticker is not None:
        conditions.append(SignalAlert.ticker == ticker)
    stmt = select(SignalAlert)
    if conditions:
        stmt = stmt.where(*conditions)
    return stmt.order_by(
        SignalAlert.as_of.desc(), SignalAlert.created_at.desc()
    ).limit(limit)


class SyncSignalAlertRepository:
    """Synchronous upsert path for the generator (a sync Celery task)."""

    def __init__(self, session: Optional[Session] = None):
        self._session = session

    def _get_session(self) -> Session:
        return self._session if self._session is not None else create_sync_session()

    def upsert(self, alert: dict) -> bool:
        """Upsert one alert; return True iff a NEW row was inserted (not a dup)."""
        session = self._get_session()
        owns = self._session is None
        try:
            result = session.execute(_insert_statement([alert]))
            session.commit()
            return int(result.rowcount or 0) > 0
        finally:
            if owns:
                session.close()

    def upsert_many(self, alerts: list[dict]) -> int:
        """Upsert a batch; return the number of NEW rows inserted (new, non-dup)."""
        if not alerts:
            return 0
        session = self._get_session()
        owns = self._session is None
        try:
            result = session.execute(_insert_statement(alerts))
            session.commit()
            return int(result.rowcount or 0)
        finally:
            if owns:
                session.close()


class SignalAlertRepository:
    """Async read path for the read-only API."""

    def __init__(self, session: Optional[AsyncSession] = None):
        self._session = session

    def _get_session(self) -> AsyncSession:
        return self._session if self._session is not None else async_session_factory()

    async def recent(
        self,
        limit: int = 50,
        kind: Optional[str] = None,
        ticker: Optional[str] = None,
    ) -> Sequence[SignalAlert]:
        """Most recent alerts (as_of desc, created_at desc) with optional filters."""
        session = self._get_session()
        owns = self._session is None
        try:
            result = await session.execute(_recent_statement(limit, kind, ticker))
            return result.scalars().all()
        finally:
            if owns:
                await session.close()
