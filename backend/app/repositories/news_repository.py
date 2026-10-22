"""NewsItem repository (design §6.4).

Async + sync read/insert of ``news_items``. Inserts use Postgres
``INSERT ... ON CONFLICT (dedup_hash) DO NOTHING`` so a re-poll that re-sees the
same item adds no duplicate row (idempotent ingest, §6.3). The
``recent_for_ticker`` read is point-in-time (rule 7): it returns the most recent
headlines that are ticker-specific OR market-wide (``ticker IS NULL``), filtered
INCLUSIVELY by ``published_at <= as_of``.

Boundary asymmetry with bar reads is INTENTIONAL (§6.4): news uses an INCLUSIVE
``published_at <= as_of`` whereas price-bar reads use an EXCLUSIVE ``ts < as_of``.
A headline published ON ``as_of`` is legitimately known at ``as_of``; a bar dated
``as_of`` would be lookahead. Do NOT "reconcile" the two.
"""

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import nulls_last, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.app.database import async_session_factory, create_sync_session
from backend.app.models.news_item import NewsItem

# Columns carried on the dedup insert (dedup_hash is the conflict target).
_INSERT_COLS = ("ticker", "source", "headline", "url", "published_at", "dedup_hash")


def _insert_statement(rows: list[dict]):
    """Build the ON CONFLICT (dedup_hash) DO NOTHING statement for a batch."""
    stmt = pg_insert(NewsItem).values(rows)
    return stmt.on_conflict_do_nothing(index_elements=["dedup_hash"])


def _recent_statement(ticker: Optional[str], limit: int, as_of: Optional[datetime]):
    """Build the point-in-time recent-headlines SELECT (design §6.4)."""
    conditions = [(NewsItem.ticker == ticker) | (NewsItem.ticker.is_(None))]
    if as_of is not None:
        conditions.append(NewsItem.published_at <= as_of)
    return (
        select(NewsItem)
        .where(*conditions)
        .order_by(nulls_last(NewsItem.published_at.desc()))
        .limit(limit)
    )


class NewsRepository:
    """Repository for news_items DB access (async core + sync variants)."""

    def __init__(self, session: Optional[AsyncSession] = None):
        self._session = session

    def _get_session(self) -> AsyncSession:
        return self._session if self._session is not None else async_session_factory()

    async def insert_items(self, rows: list[dict]) -> int:
        """Insert a batch of news dicts (ON CONFLICT DO NOTHING).

        Returns the number of rows actually inserted (new, non-duplicate).
        """
        if not rows:
            return 0
        session = self._get_session()
        owns = self._session is None
        try:
            result = await session.execute(_insert_statement(rows))
            await session.commit()
            return int(result.rowcount or 0)
        finally:
            if owns:
                await session.close()

    async def recent_for_ticker(
        self,
        ticker: Optional[str],
        limit: int = 10,
        as_of: Optional[datetime] = None,
    ) -> Sequence[NewsItem]:
        """Most recent headlines for ``ticker`` + market-wide, ``published_at <= as_of``."""
        session = self._get_session()
        owns = self._session is None
        try:
            result = await session.execute(_recent_statement(ticker, limit, as_of))
            return result.scalars().all()
        finally:
            if owns:
                await session.close()


class SyncNewsRepository:
    """Synchronous variant of :class:`NewsRepository` for the snapshot service."""

    def __init__(self, session: Optional[Session] = None):
        self._session = session

    def _get_session(self) -> Session:
        return self._session if self._session is not None else create_sync_session()

    def insert_items(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        session = self._get_session()
        owns = self._session is None
        try:
            result = session.execute(_insert_statement(rows))
            session.commit()
            return int(result.rowcount or 0)
        finally:
            if owns:
                session.close()

    def recent_for_ticker(
        self,
        ticker: Optional[str],
        limit: int = 10,
        as_of: Optional[datetime] = None,
    ) -> Sequence[NewsItem]:
        session = self._get_session()
        owns = self._session is None
        try:
            return session.execute(_recent_statement(ticker, limit, as_of)).scalars().all()
        finally:
            if owns:
                session.close()
