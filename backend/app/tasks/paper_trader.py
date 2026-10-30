"""Celery task: nightly paper-trading mark-to-market (Step 6 / G6 deliverable 4).

``mark_paper_books`` runs post-market (queue ``quant``) and advances the PAPER
books for BOTH engine sources ("quant" and "deep" — the dual book) across the
nightly universe (the distinct tickers across Holdings and WatchlistItem). For
each ticker/source it reads the latest stored recommendation and opens / marks /
closes a paper position accordingly. "No order execution, paper positions only"
(STEERING rule 9).

Each ticker is isolated inside ``mark_to_market_book`` so one failure never
aborts the run. The task returns a per-source summary dict so it slots cleanly
into the nightly pipeline's ``_run_stage`` (which expects a dict).

Decorator pattern (bind=True, retries, autoretry_for) mirrors
``app.tasks.quant_recommender.generate_nightly_signals``. Uses the shared sync
session for the universe query, matching the rest of the sync facade.
"""

import logging
from datetime import date

from celery import shared_task
from sqlalchemy import select, union

from backend.app.database import create_sync_session
from backend.app.models.portfolio import Holding, WatchlistItem
from backend.app.models.stock import Stock
from backend.app.services.paper_trading_service import (
    PAPER_SOURCES,
    mark_to_market_book,
)

logger = logging.getLogger(__name__)


def _nightly_universe(session) -> list[str]:
    """Distinct ticker symbols across Holdings + WatchlistItem (joined to Stock).

    Same universe the quant fast-lane uses, kept here as a small local helper so
    the paper task has no import dependency on the quant task module.
    """
    holdings_q = select(Stock.ticker_symbol).join(Holding, Holding.stock_id == Stock.id)
    watchlist_q = select(Stock.ticker_symbol).join(
        WatchlistItem, WatchlistItem.stock_id == Stock.id
    )
    stmt = select(union(holdings_q, watchlist_q).subquery().c.ticker_symbol)
    rows = session.execute(stmt).scalars().all()
    seen: set[str] = set()
    universe: list[str] = []
    for ticker in rows:
        if ticker and ticker not in seen:
            seen.add(ticker)
            universe.append(ticker)
    return universe


@shared_task(
    bind=True,
    name="app.tasks.paper_trader.mark_paper_books",
    max_retries=3,
    default_retry_delay=120,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
)
def mark_paper_books(self) -> dict:
    """Advance the quant + deep paper books over the nightly universe.

    Returns a summary ``{as_of, universe, books: {quant: {...}, deep: {...}}}``.
    """
    as_of = date.today()
    logger.info("Starting paper-trading mark-to-market (as_of=%s)", as_of.isoformat())

    session = create_sync_session()
    try:
        universe = _nightly_universe(session)
    finally:
        session.close()

    logger.info("Paper universe: %d distinct tickers", len(universe))

    books: dict[str, dict] = {}
    for source in PAPER_SOURCES:
        books[source] = mark_to_market_book(
            source=source, tickers=universe, as_of=as_of
        )

    summary = {"as_of": as_of.isoformat(), "universe": len(universe), "books": books}
    logger.info("Paper-trading mark-to-market complete: %s", summary)
    return summary
