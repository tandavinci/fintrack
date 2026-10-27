"""Read-only recommendation fusion endpoint (Step 5 / G5 product unification).

Serves the latest STORED quant and deep recommendations for a ticker side by
side, with a conflict flag. It is deliberately:
  * read-only — it reads the ``recommendations`` table; it NEVER runs an engine
    (no analyze/recommend, no yfinance, no gateway call);
  * additive — a new router file; it does not modify any existing router;
  * contract-faithful — each stored row maps back to the single Pydantic
    ``Recommendation`` contract, and the conflict flag comes from the shared
    ``fuse()`` helper (store both, never average — STEERING rule 4).

Deep may legitimately be absent until the overnight run, so one-source results
are normal (and cannot conflict — one action can't disagree with itself).
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.dependencies import get_db
from backend.app.models.recommendation import RecommendationRecord
from backend.app.repositories.recommendation_repository import RecommendationRepository
from backend.app.schemas.fusion import fuse
from backend.app.schemas.recommendation import Action, Recommendation

router = APIRouter(prefix="/api/fusion", tags=["fusion"])

# Ordering of sources in the fused list (quant first, then deep, then advisor).
_SOURCE_ORDER = ("quant", "deep", "advisor")


class FusionResponse(BaseModel):
    """The quant + deep recommendations side by side, plus a conflict flag.

    ``quant``/``deep`` are the latest stored rows for each source (or null when a
    source has nothing stored yet). ``conflict`` is True only when both are
    present AND their actions disagree — a single present source can't conflict.
    """

    ticker: str
    quant: Recommendation | None = None
    deep: Recommendation | None = None
    conflict: bool = False
    as_of_quant: date | None = None
    as_of_deep: date | None = None


def _to_contract(row: RecommendationRecord) -> Recommendation:
    """Map a stored ORM row back to the single Pydantic Recommendation contract.

    ``action`` is the plain stored string -> ``Action``; ``evidence`` is already
    a JSONB list; dates pass through unchanged.
    """
    return Recommendation(
        ticker=row.ticker,
        as_of=row.as_of,
        action=Action(row.action),
        confidence=row.confidence,
        horizon_days=row.horizon_days,
        rationale=row.rationale,
        evidence=list(row.evidence),
        source=row.source,
        model_id=row.model_id,
        prompt_version=row.prompt_version,
        snapshot_id=row.snapshot_id,
    )


@router.get(
    "/{ticker}",
    response_model=FusionResponse,
    summary="Latest stored quant + deep recommendations for a ticker (read-only)",
)
async def get_fusion(
    ticker: str, db: AsyncSession = Depends(get_db)
) -> FusionResponse:
    """Return the latest stored quant/deep recommendations for ``ticker``.

    404 when NEITHER source has a stored row. When only one source exists it is
    returned with the other null and ``conflict=False``.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=422, detail="ticker is required")

    repo = RecommendationRepository(db)
    by_source = await repo.latest_by_source(ticker)

    if not by_source:
        raise HTTPException(
            status_code=404,
            detail=f"no stored recommendations for {ticker}",
        )

    # Map present sources to the contract in a stable order (quant, deep, advisor).
    recs = {
        src: _to_contract(by_source[src])
        for src in _SOURCE_ORDER
        if src in by_source
    }

    # Conflict flag comes from the shared fuse() helper (never reimplemented).
    fused = fuse(recs.values())

    quant = recs.get("quant")
    deep = recs.get("deep")
    return FusionResponse(
        ticker=ticker,
        quant=quant,
        deep=deep,
        conflict=fused["conflict"],
        as_of_quant=quant.as_of if quant else None,
        as_of_deep=deep.as_of if deep else None,
    )
