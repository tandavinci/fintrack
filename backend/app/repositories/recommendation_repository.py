"""Recommendation repository (Step 3 / G3 persistence backbone).

Async upsert + read of ``recommendations``, plus sync variants for the
synchronous facade the quant/deep lanes (and Celery) call. Upsert uses Postgres
``INSERT ... ON CONFLICT (ticker, as_of, source) DO UPDATE`` so re-running a day
overwrites the stored recommendation in place rather than creating a duplicate.

The upsert consumes the single Pydantic contract
``backend.app.schemas.recommendation.Recommendation`` directly — it is mapped to
columns here, never redefined. ``action`` is stored as ``rec.action.value`` (the
plain "BUY"/"HOLD"/"SELL" string).

Session lifecycle mirrors ``price_bar_repository``: an injected session is
reused and left open; a session the repo opens itself is closed in ``finally``.
"""

from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.app.database import async_session_factory, create_sync_session
from backend.app.models.recommendation import RecommendationRecord
from backend.app.schemas.recommendation import Recommendation

# Columns updated on conflict (identity columns ticker/as_of/source excluded).
_UPSERT_UPDATE_COLS = (
    "action",
    "confidence",
    "horizon_days",
    "rationale",
    "evidence",
    "model_id",
    "prompt_version",
    "snapshot_id",
)


def _row_values(rec: Recommendation) -> dict:
    """Map the Pydantic contract to a column dict for the recommendations table.

    ``action`` is persisted as its plain string value (``rec.action.value``).
    """
    return {
        "ticker": rec.ticker,
        "as_of": rec.as_of,
        "action": rec.action.value,
        "confidence": rec.confidence,
        "horizon_days": rec.horizon_days,
        "rationale": rec.rationale,
        "evidence": list(rec.evidence),
        "source": rec.source,
        "model_id": rec.model_id,
        "prompt_version": rec.prompt_version,
        "snapshot_id": rec.snapshot_id,
    }


def _upsert_statement(rec: Recommendation):
    """Build the ON CONFLICT DO UPDATE statement for one recommendation."""
    stmt = pg_insert(RecommendationRecord).values(_row_values(rec))
    set_ = {col: getattr(stmt.excluded, col) for col in _UPSERT_UPDATE_COLS}
    set_["created_at"] = func.now()
    return stmt.on_conflict_do_update(
        index_elements=["ticker", "as_of", "source"],
        set_=set_,
    )


class RecommendationRepository:
    """Repository for recommendations DB access (async core + sync variants)."""

    def __init__(self, session: Optional[AsyncSession] = None):
        self._session = session

    def _get_session(self) -> AsyncSession:
        return self._session if self._session is not None else async_session_factory()

    async def save(self, rec: Recommendation) -> None:
        """Upsert one recommendation keyed on (ticker, as_of, source)."""
        session = self._get_session()
        owns = self._session is None
        try:
            await session.execute(_upsert_statement(rec))
            await session.commit()
        finally:
            if owns:
                await session.close()

    async def recent_for_ticker(
        self, ticker: str, limit: int = 20
    ) -> Sequence[RecommendationRecord]:
        """Return recent rows for ``ticker`` ordered by ``as_of`` desc."""
        session = self._get_session()
        owns = self._session is None
        try:
            stmt = (
                select(RecommendationRecord)
                .where(RecommendationRecord.ticker == ticker)
                .order_by(RecommendationRecord.as_of.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return result.scalars().all()
        finally:
            if owns:
                await session.close()


class SyncRecommendationRepository:
    """Synchronous variant of :class:`RecommendationRepository`."""

    def __init__(self, session: Optional[Session] = None):
        self._session = session

    def _get_session(self) -> Session:
        return self._session if self._session is not None else create_sync_session()

    def save(self, rec: Recommendation) -> None:
        session = self._get_session()
        owns = self._session is None
        try:
            session.execute(_upsert_statement(rec))
            session.commit()
        finally:
            if owns:
                session.close()

    def recent_for_ticker(
        self, ticker: str, limit: int = 20
    ) -> Sequence[RecommendationRecord]:
        session = self._get_session()
        owns = self._session is None
        try:
            stmt = (
                select(RecommendationRecord)
                .where(RecommendationRecord.ticker == ticker)
                .order_by(RecommendationRecord.as_of.desc())
                .limit(limit)
            )
            return session.execute(stmt).scalars().all()
        finally:
            if owns:
                session.close()
