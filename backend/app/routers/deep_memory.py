"""Read-only Deep Memory endpoint (Step 5 / G5 "Agent Debate viewer" page).

``GET /api/deep/memory`` and ``GET /api/deep/memory/{ticker}`` return recent
rows from the ``deep_memory`` table — the durable mirror of TradingAgents' own
memory log (the decision + reflection text per past deep run). It is the Agent
Debate viewer's data source and is deliberately:
  * read-only — it SELECTs recent rows; it never writes;
  * additive — a new router file; it does not modify any existing router;
  * boundary-safe — it NEVER imports ``deep.adapter`` and never calls
    ``analyze``. The deep lane is a queued Celery job, never inside a web
    request (STEERING rule 6) — the same structural boundary as
    ``deep_progress``, enforced by a structural test that greps this source.

Reading the mirror table is pure DB I/O (via ``DeepMemoryRepository.recent``);
it does not touch the gateway, yfinance, or the TradingAgents graph.
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.dependencies import get_db
from backend.app.models.deep_memory import DeepMemoryEntry
from backend.app.repositories.deep_memory_repository import DeepMemoryRepository

router = APIRouter(prefix="/api/deep", tags=["deep"])

# Default + hard cap on how many entries a single request may return.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


class DeepMemoryOut(BaseModel):
    """One deep-memory entry for the Agent Debate viewer (mirrors the columns)."""

    ticker: str
    trade_date: date
    rating: str | None = None
    pending: bool
    raw_return: str | None = None
    alpha_return: str | None = None
    holding_days: str | None = None
    resolved_date: date | None = None
    decision: str
    reflection: str
    created_at: datetime | None = None


class DeepMemoryResponse(BaseModel):
    """Recent deep-memory entries, newest first, with the applied filters."""

    ticker: str | None = None
    count: int
    entries: list[DeepMemoryOut]


def _to_out(row: DeepMemoryEntry) -> DeepMemoryOut:
    """Map a ``deep_memory`` ORM row to the response model."""
    return DeepMemoryOut(
        ticker=row.ticker,
        trade_date=row.trade_date,
        rating=row.rating,
        pending=row.pending,
        raw_return=row.raw_return,
        alpha_return=row.alpha_return,
        holding_days=row.holding_days,
        resolved_date=row.resolved_date,
        decision=row.decision,
        reflection=row.reflection,
        created_at=row.created_at,
    )


async def _recent(
    db: AsyncSession, limit: int, ticker: str | None
) -> DeepMemoryResponse:
    """Shared read: fetch recent entries (optionally for one ticker)."""
    repo = DeepMemoryRepository(db)
    rows = await repo.recent(limit=limit, ticker=ticker)
    return DeepMemoryResponse(
        ticker=ticker,
        count=len(rows),
        entries=[_to_out(r) for r in rows],
    )


@router.get(
    "/memory",
    response_model=DeepMemoryResponse,
    summary="Recent deep-memory entries, newest first (read-only)",
)
async def get_deep_memory(
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    ticker: str | None = Query(None, description="Optional ticker filter"),
    db: AsyncSession = Depends(get_db),
) -> DeepMemoryResponse:
    """Return recent deep-memory entries (optional ``?ticker=`` + ``?limit=``).

    Newest first (trade_date desc, created_at desc). An empty table is a NORMAL
    state (the overnight deep run populates it), returned as ``count: 0``.
    """
    norm = ticker.strip().upper() if ticker else None
    return await _recent(db, limit=limit, ticker=norm or None)


@router.get(
    "/memory/{ticker}",
    response_model=DeepMemoryResponse,
    summary="Recent deep-memory entries for one ticker, newest first (read-only)",
)
async def get_deep_memory_for_ticker(
    ticker: str,
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
) -> DeepMemoryResponse:
    """Return recent deep-memory entries for ``ticker`` (path-param variant)."""
    return await _recent(db, limit=limit, ticker=ticker.strip().upper())
