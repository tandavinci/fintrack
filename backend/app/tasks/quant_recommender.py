"""Celery task: nightly quant fast-lane signal generation (Step 3 / G3).

``generate_nightly_signals`` runs post-market (queue ``quant``, concurrency 1)
over the nightly universe — the distinct tickers across Holdings and
WatchlistItem, resolved to their ``Stock.ticker_symbol`` — and persists a
rule-based quant Recommendation for each via the sync
``persist_quant_recommendation`` service.

Each ticker is wrapped in its own try/except so one failure (a dead symbol, a
thin frame) never aborts the whole run; it is logged and counted as an error.
The task returns a summary ``{processed, skipped, errors}``.

Decorator pattern (bind=True, retries, autoretry_for) mirrors
``app.tasks.insight_calculator.recalculate_insights``. Uses the shared
synchronous session (``create_sync_session``) for the universe query, matching
the rest of the sync facade.
"""

import logging
from datetime import date

from celery import shared_task
from sqlalchemy import select, union

from backend.app.database import create_sync_session
from backend.app.models.portfolio import Holding, WatchlistItem
from backend.app.models.stock import Stock
from backend.app.services.recommendation_service import persist_quant_recommendation

logger = logging.getLogger(__name__)


def _nightly_universe(session) -> list[str]:
    """Distinct ticker symbols across Holdings + WatchlistItem, both joined to Stock.

    Returns a de-duplicated list of ``Stock.ticker_symbol`` values.
    """
    holdings_q = (
        select(Stock.ticker_symbol)
        .join(Holding, Holding.stock_id == Stock.id)
    )
    watchlist_q = (
        select(Stock.ticker_symbol)
        .join(WatchlistItem, WatchlistItem.stock_id == Stock.id)
    )
    stmt = select(union(holdings_q, watchlist_q).subquery().c.ticker_symbol)
    rows = session.execute(stmt).scalars().all()
    # Preserve a stable, de-duplicated order.
    seen: set[str] = set()
    universe: list[str] = []
    for ticker in rows:
        if ticker and ticker not in seen:
            seen.add(ticker)
            universe.append(ticker)
    return universe


@shared_task(
    bind=True,
    name="app.tasks.quant_recommender.generate_nightly_signals",
    max_retries=3,
    default_retry_delay=120,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
)
def generate_nightly_signals(self) -> dict:
    """Generate + persist quant recommendations for the nightly universe.

    For each distinct holdings/watchlist ticker, call
    ``persist_quant_recommendation(ticker, as_of=date.today())``. Per-ticker
    failures are isolated (logged, counted) so the run always completes.

    Returns:
        Summary dict ``{processed, skipped, errors}``.
    """
    as_of = date.today()
    logger.info("Starting nightly quant signal generation (as_of=%s)", as_of.isoformat())

    session = create_sync_session()
    try:
        universe = _nightly_universe(session)
    finally:
        session.close()

    logger.info("Nightly universe: %d distinct tickers", len(universe))

    processed = 0
    skipped = 0
    errors = 0

    for ticker in universe:
        try:
            persist_quant_recommendation(ticker, as_of=as_of)
            processed += 1
        except Exception:
            errors += 1
            logger.exception("nightly quant recommendation failed for %s", ticker)

    summary = {"processed": processed, "skipped": skipped, "errors": errors}
    logger.info("Nightly quant signal generation complete: %s", summary)
    return summary
