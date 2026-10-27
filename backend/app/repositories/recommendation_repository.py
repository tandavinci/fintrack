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

from typing import Iterable, Optional, Sequence

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


def _pick_latest_per_source(
    rows: Iterable[RecommendationRecord],
) -> dict[str, RecommendationRecord]:
    """Pure selection helper: keep the most-recent row for EACH source.

    "Most recent" is (max ``as_of``, tiebreak max ``created_at``). Works on any
    iterable of rows in any order (it does not assume the caller pre-ordered),
    so it is unit-testable offline with plain objects. Returns a dict keyed by
    source string ("quant" / "deep" / "advisor") — only sources present in
    ``rows`` appear in the result.
    """
    best: dict[str, RecommendationRecord] = {}
    for row in rows:
        current = best.get(row.source)
        if current is None or _recency_key(row) > _recency_key(current):
            best[row.source] = row
    return best


def _recency_key(row: RecommendationRecord):
    """Sort key for recency: (as_of, created_at). created_at may be None in
    unit tests built from plain objects — treat None as the oldest possible."""
    created = getattr(row, "created_at", None)
    return (row.as_of, created if created is not None else _MIN_CREATED)


# Sentinel "oldest" created_at for rows missing the column (offline unit rows).
from datetime import datetime, timezone  # noqa: E402 (local, keeps top imports tidy)

_MIN_CREATED = datetime.min.replace(tzinfo=timezone.utc)


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

    async def latest_by_source(
        self, ticker: str
    ) -> dict[str, RecommendationRecord]:
        """Return the most-recent row per source for ``ticker``.

        Keyed by source ("quant" / "deep" / "advisor"); only sources that have a
        stored row appear. "Most recent" is (max ``as_of``, tiebreak max
        ``created_at``). The DB orders the rows; the per-source pick is the pure
        :func:`_pick_latest_per_source` helper (simple + offline-testable).
        """
        session = self._get_session()
        owns = self._session is None
        try:
            stmt = (
                select(RecommendationRecord)
                .where(RecommendationRecord.ticker == ticker)
                .order_by(
                    RecommendationRecord.as_of.desc(),
                    RecommendationRecord.created_at.desc(),
                )
            )
            result = await session.execute(stmt)
            return _pick_latest_per_source(result.scalars().all())
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

    def latest_by_source(self, ticker: str) -> dict[str, RecommendationRecord]:
        """Synchronous variant of :meth:`RecommendationRepository.latest_by_source`."""
        session = self._get_session()
        owns = self._session is None
        try:
            stmt = (
                select(RecommendationRecord)
                .where(RecommendationRecord.ticker == ticker)
                .order_by(
                    RecommendationRecord.as_of.desc(),
                    RecommendationRecord.created_at.desc(),
                )
            )
            return _pick_latest_per_source(session.execute(stmt).scalars().all())
        finally:
            if owns:
                session.close()
