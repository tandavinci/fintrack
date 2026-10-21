"""Celery task for fetching Google Trends data using pytrends.

Refreshes Trend_Score for all stocks in user portfolios and watchlists.
Handles rate limiting (~10 requests/minute) with backoff/delays between requests.
Computes sector-level trend aggregation (arithmetic mean of stock trends in sector).
Invalidates trend cache on update.

Schedule: once every 24 hours (configured in celery_app beat schedule at 6:00 AM IST).
"""

import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Set

from celery import shared_task
from pytrends.request import TrendReq
from sqlalchemy import select

from backend.app.celery_app import celery_app
from backend.app.database import async_session_factory
from backend.app.models.portfolio import Holding, Portfolio, WatchlistItem
from backend.app.models.stock import Sector, Stock, TrendData

logger = logging.getLogger(__name__)

# Rate limiting configuration: ~10 requests/minute → ~6 seconds between requests
REQUEST_DELAY_SECONDS = 6.0
# Maximum retries on rate limit (HTTP 429) errors
MAX_RATE_LIMIT_RETRIES = 3
# Backoff multiplier for rate limit retries
RATE_LIMIT_BACKOFF_MULTIPLIER = 2.0
# Initial backoff wait in seconds on rate limit
RATE_LIMIT_INITIAL_WAIT = 30.0
# pytrends timeframe: last 90 days (gives meaningful trend data)
TREND_TIMEFRAME = "today 3-m"


def _get_trend_req() -> TrendReq:
    """Create a pytrends TrendReq instance with default settings."""
    return TrendReq(hl="en-US", tz=330)  # tz=330 for IST (UTC+5:30 in minutes)


def _fetch_trend_score_for_keyword(pytrends: TrendReq, keyword: str) -> int | None:
    """Fetch the average interest_over_time score for a single keyword.

    Uses the pytrends interest_over_time API. Returns the mean of daily interest
    values (0-100 scale) over the configured timeframe, or None if data is unavailable.

    Handles rate limiting with exponential backoff.
    """
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        try:
            pytrends.build_payload([keyword], timeframe=TREND_TIMEFRAME)
            df = pytrends.interest_over_time()

            if df is None or df.empty or keyword not in df.columns:
                logger.info("No trend data available for keyword: %s", keyword)
                return None

            # Compute mean of the interest values (drop the isPartial column if present)
            values = df[keyword]
            trend_score = int(round(values.mean()))

            # Clamp to 0-100 range
            return max(0, min(100, trend_score))

        except Exception as exc:
            exc_str = str(exc).lower()
            # Check for rate limiting (HTTP 429 or "too many requests")
            if "429" in exc_str or "too many" in exc_str:
                if attempt < MAX_RATE_LIMIT_RETRIES:
                    wait_time = RATE_LIMIT_INITIAL_WAIT * (
                        RATE_LIMIT_BACKOFF_MULTIPLIER ** attempt
                    )
                    logger.warning(
                        "Rate limited fetching trend for '%s'. "
                        "Waiting %.1fs before retry %d/%d",
                        keyword,
                        wait_time,
                        attempt + 1,
                        MAX_RATE_LIMIT_RETRIES,
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(
                        "Rate limit retries exhausted for keyword '%s': %s",
                        keyword,
                        exc,
                    )
                    return None
            else:
                logger.error(
                    "Error fetching trend data for '%s': %s",
                    keyword,
                    exc,
                )
                return None

    return None


async def _get_portfolio_and_watchlist_stock_ids() -> Set[int]:
    """Retrieve distinct stock IDs from all user portfolios and watchlists."""
    async with async_session_factory() as session:
        # Get stock IDs from holdings (via portfolios)
        holdings_stmt = (
            select(Holding.stock_id)
            .join(Portfolio, Holding.portfolio_id == Portfolio.id)
            .distinct()
        )
        holdings_result = await session.execute(holdings_stmt)
        holding_stock_ids = {row[0] for row in holdings_result.all()}

        # Get stock IDs from watchlist items
        watchlist_stmt = select(WatchlistItem.stock_id).distinct()
        watchlist_result = await session.execute(watchlist_stmt)
        watchlist_stock_ids = {row[0] for row in watchlist_result.all()}

        return holding_stock_ids | watchlist_stock_ids


async def _get_stocks_by_ids(stock_ids: Set[int]) -> List[Stock]:
    """Fetch Stock objects for given IDs."""
    if not stock_ids:
        return []

    async with async_session_factory() as session:
        stmt = select(Stock).where(
            Stock.id.in_(stock_ids),
            Stock.is_active == True,  # noqa: E712
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def _store_trend_data(stock_id: int, trend_score: int) -> None:
    """Store a TrendData record for a stock."""
    today = date.today()
    period_start = today - timedelta(days=90)  # Matches the 3-month timeframe

    async with async_session_factory() as session:
        trend_record = TrendData(
            stock_id=stock_id,
            trend_score=trend_score,
            period_start=period_start,
            period_end=today,
        )
        session.add(trend_record)
        await session.commit()


async def _invalidate_trend_cache(stock_ids: Set[int]) -> None:
    """Invalidate Redis trend cache for updated stocks."""
    try:
        from backend.app.cache import invalidate_all_trend_scores, invalidate_trend_score

        # Invalidate individual stock trend caches
        for stock_id in stock_ids:
            await invalidate_trend_score(stock_id)

        logger.info("Invalidated trend cache for %d stocks", len(stock_ids))
    except Exception as exc:
        # Cache invalidation failure is non-critical; log and continue
        logger.warning("Failed to invalidate trend cache: %s", exc)


async def _compute_sector_aggregation(stock_ids: Set[int]) -> None:
    """Compute sector-level trend aggregation (arithmetic mean of stock trends).

    For each sector that has stocks with updated trend data, compute the
    arithmetic mean of the most recent individual stock trend scores.

    Validates: Requirements 5.6
    """
    async with async_session_factory() as session:
        # Get all sectors that have at least one stock in our updated set
        sector_stmt = (
            select(Sector.id, Sector.name)
            .join(Stock, Stock.sector_id == Sector.id)
            .where(Stock.id.in_(stock_ids))
            .distinct()
        )
        sector_result = await session.execute(sector_stmt)
        sectors = sector_result.all()

        for sector_id, sector_name in sectors:
            # Get all active stocks in this sector
            stocks_stmt = select(Stock.id).where(
                Stock.sector_id == sector_id,
                Stock.is_active == True,  # noqa: E712
            )
            stocks_result = await session.execute(stocks_stmt)
            sector_stock_ids = [row[0] for row in stocks_result.all()]

            if not sector_stock_ids:
                continue

            # Get the latest trend score for each stock in the sector
            latest_scores: List[float] = []
            for sid in sector_stock_ids:
                trend_stmt = (
                    select(TrendData.trend_score)
                    .where(TrendData.stock_id == sid)
                    .order_by(TrendData.fetched_at.desc())
                    .limit(1)
                )
                trend_result = await session.execute(trend_stmt)
                score = trend_result.scalar_one_or_none()
                if score is not None:
                    latest_scores.append(float(score))

            if latest_scores:
                sector_avg = sum(latest_scores) / len(latest_scores)
                logger.info(
                    "Sector '%s' trend aggregation: avg=%.2f (from %d stocks)",
                    sector_name,
                    sector_avg,
                    len(latest_scores),
                )


def _run_async(coro):
    """Run an async coroutine in a synchronous Celery task context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(coro)


@shared_task(
    bind=True,
    name="app.tasks.trend_fetcher.refresh_trend_scores",
    max_retries=2,
    default_retry_delay=300,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=600,
)
def refresh_trend_scores(self) -> Dict[str, int]:
    """Celery task: Refresh Google Trends data for all stocks in user portfolios/watchlists.

    Fetches trend data for each stock using pytrends, stores TrendData records,
    computes sector-level aggregation, and invalidates Redis cache.

    Respects rate limiting (~10 requests/minute) by spacing requests with delays.

    Returns:
        Dict with counts: {'processed': N, 'successful': M, 'failed': F}
    """
    logger.info("Starting trend score refresh task")

    # Step 1: Collect all stock IDs from portfolios and watchlists
    stock_ids = _run_async(_get_portfolio_and_watchlist_stock_ids())

    if not stock_ids:
        logger.info("No stocks found in portfolios or watchlists. Skipping trend refresh.")
        return {"processed": 0, "successful": 0, "failed": 0}

    logger.info("Found %d unique stocks to refresh trends for", len(stock_ids))

    # Step 2: Get stock details (company_name, ticker_symbol)
    stocks = _run_async(_get_stocks_by_ids(stock_ids))

    if not stocks:
        logger.info("No active stocks found. Skipping trend refresh.")
        return {"processed": 0, "successful": 0, "failed": 0}

    # Step 3: Fetch trends for each stock with rate limiting
    pytrends = _get_trend_req()
    successful = 0
    failed = 0
    updated_stock_ids: Set[int] = set()

    for i, stock in enumerate(stocks):
        # Use company_name as primary keyword, fall back to ticker_symbol
        keyword = stock.company_name or stock.ticker_symbol

        logger.info(
            "Fetching trend for stock %d/%d: '%s' (keyword='%s')",
            i + 1,
            len(stocks),
            stock.ticker_symbol,
            keyword,
        )

        trend_score = _fetch_trend_score_for_keyword(pytrends, keyword)

        if trend_score is not None:
            _run_async(_store_trend_data(stock.id, trend_score))
            updated_stock_ids.add(stock.id)
            successful += 1
            logger.info(
                "Stored trend score %d for stock '%s' (id=%d)",
                trend_score,
                stock.ticker_symbol,
                stock.id,
            )
        else:
            failed += 1
            logger.warning(
                "Failed to get trend data for stock '%s' (id=%d)",
                stock.ticker_symbol,
                stock.id,
            )

        # Rate limiting: wait between requests (skip delay after last request)
        if i < len(stocks) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)

    # Step 4: Compute sector-level trend aggregation
    if updated_stock_ids:
        logger.info("Computing sector-level trend aggregation...")
        _run_async(_compute_sector_aggregation(updated_stock_ids))

    # Step 5: Invalidate trend cache for updated stocks
    if updated_stock_ids:
        _run_async(_invalidate_trend_cache(updated_stock_ids))

    result = {
        "processed": len(stocks),
        "successful": successful,
        "failed": failed,
    }
    logger.info("Trend score refresh completed: %s", result)
    return result
