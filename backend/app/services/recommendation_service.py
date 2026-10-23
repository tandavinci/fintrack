"""Quant recommendation persistence service (Step 3 / G3 persistence backbone).

``persist_quant_recommendation(ticker, as_of) -> Recommendation`` is a
SYNCHRONOUS callable (it drives the sync quant lane and the sync DB session,
matching how ``quant.recommend`` and ``snapshot_service.build_and_store_snapshot``
run — offline/Celery callables, never inside a web request).

Order matters for the FK: the snapshot row MUST exist before the recommendation
references it. So this:

1. builds+stores the point-in-time snapshot via the FEAT-C
   ``build_and_store_snapshot`` (returns ``(snapshot_id, payload)``),
2. asks the rule-based quant lane for a Recommendation,
3. upserts it (keyed on ticker/as_of/source) via ``SyncRecommendationRepository``,
4. returns the Recommendation.

``quant.recommend`` computes its own ``snapshot_id`` from the same canonical
``compute_snapshot_id`` the snapshot builder uses, so the recommendation's
``snapshot_id`` matches the row written in step 1 (same data -> same id).
"""

from __future__ import annotations

import logging
from datetime import date

from backend.app.repositories.recommendation_repository import SyncRecommendationRepository
from backend.app.schemas.recommendation import Recommendation
from backend.app.services.snapshot_service import build_and_store_snapshot
from quant.recommend import recommend

logger = logging.getLogger(__name__)


def persist_quant_recommendation(ticker: str, as_of: date) -> Recommendation:
    """Build+store the snapshot, generate a quant recommendation, persist it.

    Args:
        ticker: Non-empty symbol (e.g. ``RELIANCE.NS``).
        as_of: The date the recommendation is anchored to.

    Returns:
        The persisted :class:`Recommendation` (``source='quant'``).
    """
    # 1. Snapshot first so the FK target row exists.
    build_and_store_snapshot(ticker, as_of)

    # 2. Rule-based quant Recommendation (its snapshot_id matches step 1's row).
    rec = recommend(ticker, as_of)

    # 3. Upsert keyed on (ticker, as_of, source).
    SyncRecommendationRepository().save(rec)

    logger.info(
        "persisted quant recommendation %s %s action=%s snapshot=%s",
        ticker,
        as_of.isoformat(),
        rec.action.value,
        rec.snapshot_id,
    )
    return rec
