"""Read-only nightly-pipeline status endpoint (Step 5 / G5 unattended-day audit).

Serves the most recent ``PipelineRun`` rows so the frontend (the Model Status
page, or a small widget) can show "last nightly run: completed at 03:12,
quant 12 / deep 5 / alerts 7". It is deliberately:
  * read-only — it reads the ``pipeline_runs`` table; it NEVER runs the pipeline;
  * additive — a NEW router at ``/api/pipeline``; it modifies no existing router;
  * unauthenticated — these are system rows (not user-scoped), like the other
    G5 read-only status endpoints (signal-alerts, model-status).

``?limit=`` caps the page (default 7 — roughly a trading week; hard cap 60).
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.dependencies import get_db
from backend.app.models.pipeline_run import PipelineRun

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])

_LIMIT_DEFAULT = 7
_LIMIT_CAP = 60


class PipelineRunOut(BaseModel):
    """One nightly pipeline run, as served to the UI."""

    run_date: date
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stages: dict


class PipelineStatusResponse(BaseModel):
    """Recent nightly pipeline runs (newest first)."""

    runs: list[PipelineRunOut]
    count: int


def _to_out(row: PipelineRun) -> PipelineRunOut:
    return PipelineRunOut(
        run_date=row.run_date,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        stages=row.stages or {},
    )


@router.get(
    "/status",
    response_model=PipelineStatusResponse,
    summary="Recent nightly-pipeline runs with per-stage summaries, read-only",
)
async def get_pipeline_status(
    limit: int = Query(
        default=_LIMIT_DEFAULT, ge=1, le=_LIMIT_CAP, description="Max rows (cap 60)."
    ),
    db: AsyncSession = Depends(get_db),
) -> PipelineStatusResponse:
    """Return the most recent pipeline runs, newest first."""
    stmt = (
        select(PipelineRun)
        .order_by(PipelineRun.run_date.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    runs = [_to_out(r) for r in rows]
    return PipelineStatusResponse(runs=runs, count=len(runs))
