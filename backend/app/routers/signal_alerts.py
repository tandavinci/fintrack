"""Read-only system-generated signal-alerts endpoint (Step 5 / G5).

Serves the recent SYSTEM-generated alerts (AI / RISK / NEWS) produced by the
nightly generator. It is deliberately:
  * read-only — it reads the ``signal_alerts`` table; it NEVER generates alerts;
  * additive — a NEW router mounted at ``/api/alerts/signals``, DISTINCT from the
    existing user-configured alert router at ``/api/v1/alerts`` (that one is
    untouched);
  * unauthenticated — these are not user-specific rows (no ``user_id``), so there
    is nothing to scope to a caller.

``?kind=`` / ``?ticker=`` optionally filter; ``?limit=`` caps the page (default
50, hard cap 200).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.dependencies import get_db
from backend.app.models.signal_alert import SignalAlert
from backend.app.repositories.signal_alert_repository import SignalAlertRepository

router = APIRouter(prefix="/api/alerts", tags=["signal-alerts"])

_LIMIT_DEFAULT = 50
_LIMIT_CAP = 200


class SignalAlertOut(BaseModel):
    """One system-generated alert row, as served to the UI."""

    id: int
    ticker: str | None = None
    alert_kind: str
    severity: str
    title: str
    detail: str
    as_of: date
    created_at: datetime | None = None


class SignalAlertsResponse(BaseModel):
    """A page of recent system-generated alerts (newest first)."""

    alerts: list[SignalAlertOut]
    count: int


def _to_out(row: SignalAlert) -> SignalAlertOut:
    return SignalAlertOut(
        id=row.id,
        ticker=row.ticker,
        alert_kind=row.alert_kind,
        severity=row.severity,
        title=row.title,
        detail=row.detail,
        as_of=row.as_of,
        created_at=row.created_at,
    )


@router.get(
    "/signals",
    response_model=SignalAlertsResponse,
    summary="Recent system-generated signal alerts (AI/RISK/NEWS), read-only",
)
async def list_signal_alerts(
    kind: Optional[Literal["AI", "RISK", "NEWS"]] = Query(
        default=None, description="Filter by alert kind."
    ),
    ticker: Optional[str] = Query(
        default=None, description="Filter by ticker symbol."
    ),
    limit: int = Query(
        default=_LIMIT_DEFAULT, ge=1, le=_LIMIT_CAP, description="Max rows (cap 200)."
    ),
    db: AsyncSession = Depends(get_db),
) -> SignalAlertsResponse:
    """Return recent system-generated alerts, newest first, with optional filters."""
    ticker_norm = ticker.strip().upper() if ticker else None
    repo = SignalAlertRepository(db)
    rows = await repo.recent(limit=limit, kind=kind, ticker=ticker_norm)
    alerts = [_to_out(r) for r in rows]
    return SignalAlertsResponse(alerts=alerts, count=len(alerts))
