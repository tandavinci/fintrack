"""Deep recommendation persistence service (Step 4 / G4 persistence backbone).

``persist_deep_recommendation(ticker, as_of, portfolio=None) -> Recommendation``
is the deep-lane analog of ``persist_quant_recommendation``: a SYNCHRONOUS
callable driven by the Celery ``deep`` worker (or an offline script), NEVER
inside a web request (STEERING rule 6 — the deep lane is a queued job).

Order matters for the FK, exactly as in the quant lane: the snapshot row MUST
exist before the recommendation references it. So this:

1. builds+stores the point-in-time snapshot via ``build_and_store_snapshot``
   (the FK target row), then
2. asks the deep adapter for a :class:`Recommendation`, then
3. upserts it (keyed on ticker/as_of/source) via ``SyncRecommendationRepository``.

The deep adapter (``deep.adapter.analyze``) builds its OWN snapshot internally
from the SAME 200-day enriched frame and computes the SAME canonical
``compute_snapshot_id``, so the returned recommendation's ``snapshot_id`` matches
the row written in step 1 (same data -> same id) and the FK resolves.

``DeepValidationError`` / ``NumberCheckError`` are allowed to propagate to the
caller (the Celery task decides how to count them) — this service stays simple
and sync, mirroring ``persist_quant_recommendation``.
"""

from __future__ import annotations

import logging
from datetime import date

from backend.app.repositories.recommendation_repository import SyncRecommendationRepository
from backend.app.schemas.recommendation import Recommendation
from backend.app.services.deep_memory_service import mirror_memory_for_ticker
from backend.app.services.snapshot_service import build_and_store_snapshot
from deep.adapter import analyze, deep_memory_log_path

logger = logging.getLogger(__name__)


def persist_deep_recommendation(
    ticker: str,
    as_of: date,
    portfolio: dict | None = None,
) -> Recommendation:
    """Build+store the snapshot, run the deep adapter, persist its recommendation.

    Args:
        ticker: Non-empty symbol (e.g. ``RELIANCE.NS``).
        as_of: The date the recommendation is anchored to.
        portfolio: Optional mapping of ticker -> numeric holding, threaded into
            the deep adapter's number-check allowable set.

    Returns:
        The persisted :class:`Recommendation` (``source='deep'``).

    Raises:
        DeepValidationError / NumberCheckError: propagated from the adapter so
            the caller can count the failure (not swallowed here).
    """
    # 1. Snapshot first so the FK target row exists. The deep adapter computes
    #    the SAME snapshot_id over the SAME 200-day frame, so the recommendation
    #    it returns references exactly this row.
    build_and_store_snapshot(ticker, as_of)

    # 2. Deep adapter (slow; runs the real graph). May raise DeepValidationError
    #    / NumberCheckError — let it propagate to the caller.
    rec = analyze(ticker, as_of, portfolio=portfolio)

    # 3. Upsert keyed on (ticker, as_of, source).
    SyncRecommendationRepository().save(rec)

    # 4. Write-as-you-go memory mirror: copy THIS ticker's entries from
    #    TradingAgents' own markdown log into deep_memory (cheap — a few rows).
    #    A mirror failure must NEVER fail the recommendation (STEERING: the
    #    markdown remains the backup export), so isolate it here.
    try:
        mirrored = mirror_memory_for_ticker(deep_memory_log_path(), ticker)
        logger.info("mirrored %d deep_memory entries for %s", mirrored, ticker)
    except Exception as exc:  # noqa: BLE001 — mirror is best-effort
        logger.warning("deep_memory mirror failed for %s: %s", ticker, exc)

    logger.info(
        "persisted deep recommendation %s %s action=%s snapshot=%s",
        ticker,
        as_of.isoformat(),
        rec.action.value,
        rec.snapshot_id,
    )
    return rec
