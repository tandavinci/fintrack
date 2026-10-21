"""Celery tasks for insight score recalculation and stale data cleanup.

- recalculate_insights: Daily post-market task that recalculates insight scores
  for all active stocks based on price performance and trend momentum.
- cleanup_stale_data: Weekly task that archives old data and cleans stale cache entries.

Uses synchronous SQLAlchemy sessions for Celery worker context.

Requirements: 7.1, 7.2, 7.5
"""

import logging
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import redis
from celery import shared_task
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import settings
from backend.app.models.insight import InsightScore, InsightSignalEnum
from backend.app.models.stock import PriceHistory, Stock, TrendData

logger = logging.getLogger(__name__)

# ─── Insight Score Thresholds ──────────────────────────────────────────────────
OPPORTUNITY_THRESHOLD: float = 70.0
CAUTION_THRESHOLD: float = 30.0

# ─── Score Weights ─────────────────────────────────────────────────────────────
HISTORICAL_WEIGHT: float = 0.6
TREND_WEIGHT: float = 0.4

# ─── Cleanup Configuration ─────────────────────────────────────────────────────
INSIGHT_RETENTION_DAYS: int = 90  # Keep insight scores for 90 days
PRICE_HISTORY_RETENTION_YEARS: int = 7  # Keep price history for 7 years per Req 12.4
TREND_DATA_RETENTION_DAYS: int = 365  # Keep trend data for 1 year

# ─── Sync Database Engine ──────────────────────────────────────────────────────

def _get_sync_database_url() -> str:
    """Convert async database URL to sync for Celery worker context."""
    url = settings.database_url
    # Replace asyncpg driver with psycopg2 for synchronous access
    if "asyncpg" in url:
        return url.replace("postgresql+asyncpg", "postgresql+psycopg2")
    return url


def _create_sync_session() -> Session:
    """Create a synchronous SQLAlchemy session for Celery task execution."""
    sync_engine = create_engine(
        _get_sync_database_url(),
        pool_size=5,
        max_overflow=2,
        pool_pre_ping=True,
    )
    SyncSessionFactory = sessionmaker(bind=sync_engine, expire_on_commit=False)
    return SyncSessionFactory()


def _get_sync_redis():
    """Create a synchronous Redis client for cache operations in Celery."""
    return redis.from_url(settings.redis_url, decode_responses=True)


# ─── Historical Score Computation ──────────────────────────────────────────────

def _compute_historical_score(session: Session, stock_id: int) -> float | None:
    """Compute a historical performance score (0-100) from price data.

    The score combines:
    - Recent returns (30-day price change normalized to 0-100 scale)
    - Volatility metric (lower volatility = higher score)

    Returns None if insufficient price data is available.
    """
    # Fetch last 60 days of price history for computing metrics
    cutoff_date = date.today() - timedelta(days=60)
    stmt = (
        select(PriceHistory.close_price, PriceHistory.date)
        .where(
            PriceHistory.stock_id == stock_id,
            PriceHistory.date >= cutoff_date,
        )
        .order_by(PriceHistory.date.asc())
    )
    result = session.execute(stmt)
    rows = result.all()

    if len(rows) < 5:
        # Insufficient data to compute a meaningful score
        return None

    prices = [float(row[0]) for row in rows]

    # --- Component 1: Recent return (30-day normalized) ---
    # Use the last 30 days of available prices or all if less
    recent_prices = prices[-30:] if len(prices) >= 30 else prices
    if recent_prices[0] == 0:
        return_pct = 0.0
    else:
        return_pct = ((recent_prices[-1] - recent_prices[0]) / recent_prices[0]) * 100

    # Normalize return to 0-100 scale: clamp to [-50%, +50%] range, then scale
    clamped_return = max(-50.0, min(50.0, return_pct))
    return_score = (clamped_return + 50.0)  # Maps -50..+50 to 0..100

    # --- Component 2: Volatility metric ---
    # Lower daily volatility = higher score
    if len(prices) >= 2:
        daily_returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] != 0:
                daily_returns.append((prices[i] - prices[i - 1]) / prices[i - 1])

        if daily_returns:
            mean_return = sum(daily_returns) / len(daily_returns)
            variance = sum((r - mean_return) ** 2 for r in daily_returns) / len(daily_returns)
            volatility = math.sqrt(variance) * 100  # Annualize roughly
        else:
            volatility = 0.0
    else:
        volatility = 0.0

    # Normalize volatility to 0-100 (inverse): high volatility = low score
    # Typical daily volatility is 0-5%; clamp to [0, 5]
    clamped_vol = max(0.0, min(5.0, volatility))
    volatility_score = 100.0 - (clamped_vol * 20.0)  # 0% vol = 100, 5% vol = 0

    # Combine: 70% weight on returns, 30% on stability
    historical_score = return_score * 0.7 + volatility_score * 0.3
    return max(0.0, min(100.0, historical_score))


def _get_trend_momentum(session: Session, stock_id: int) -> float | None:
    """Get the latest trend momentum score for a stock from TrendData.

    Returns the most recent trend_score (0-100), or None if no trend data exists.
    """
    stmt = (
        select(TrendData.trend_score)
        .where(TrendData.stock_id == stock_id)
        .order_by(TrendData.fetched_at.desc())
        .limit(1)
    )
    result = session.execute(stmt)
    row = result.scalar_one_or_none()

    if row is not None:
        return float(row)
    return None


def _classify_signal(combined_score: float) -> str:
    """Classify the insight signal based on combined score thresholds.

    - OPPORTUNITY when combined_score > 70
    - CAUTION when combined_score < 30
    - NEUTRAL otherwise
    """
    if combined_score > OPPORTUNITY_THRESHOLD:
        return InsightSignalEnum.OPPORTUNITY.value
    elif combined_score < CAUTION_THRESHOLD:
        return InsightSignalEnum.CAUTION.value
    return InsightSignalEnum.NEUTRAL.value


# ─── Celery Tasks ──────────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    name="app.tasks.insight_calculator.recalculate_insights",
    max_retries=3,
    default_retry_delay=120,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
)
def recalculate_insights(self) -> dict:
    """Daily post-market task that recalculates insight scores for all active stocks.

    For each active stock:
    1. Compute historical_score from price performance (recent returns + volatility)
    2. Get trend_momentum from the latest TrendData.trend_score
    3. Compute combined_score = historical_score × 0.6 + trend_momentum × 0.4
    4. Classify signal (OPPORTUNITY / CAUTION / NEUTRAL) based on thresholds
    5. Store InsightScore record in the database

    Returns:
        Summary dict with counts of processed, skipped, and scored stocks.
    """
    logger.info("Starting daily insight score recalculation")

    session = _create_sync_session()
    processed = 0
    skipped = 0
    opportunities = 0
    cautions = 0
    neutrals = 0

    try:
        # Get all active stocks
        stmt = select(Stock.id).where(Stock.is_active == True)  # noqa: E712
        result = session.execute(stmt)
        stock_ids = [row[0] for row in result.all()]

        logger.info("Found %d active stocks to process", len(stock_ids))

        for stock_id in stock_ids:
            # Compute historical score from price performance
            historical_score = _compute_historical_score(session, stock_id)
            if historical_score is None:
                skipped += 1
                continue

            # Get trend momentum from latest TrendData
            trend_momentum = _get_trend_momentum(session, stock_id)
            if trend_momentum is None:
                # If no trend data, use neutral score of 50
                trend_momentum = 50.0

            # Compute combined score: historical × 0.6 + trend × 0.4
            combined_score = historical_score * HISTORICAL_WEIGHT + trend_momentum * TREND_WEIGHT

            # Classify signal based on thresholds
            signal = _classify_signal(combined_score)

            # Store InsightScore record
            insight = InsightScore(
                stock_id=stock_id,
                historical_score=Decimal(str(round(historical_score, 2))),
                trend_momentum=Decimal(str(round(trend_momentum, 2))),
                combined_score=Decimal(str(round(combined_score, 2))),
                signal=signal,
            )
            session.add(insight)
            processed += 1

            # Track signal distribution
            if signal == InsightSignalEnum.OPPORTUNITY.value:
                opportunities += 1
            elif signal == InsightSignalEnum.CAUTION.value:
                cautions += 1
            else:
                neutrals += 1

        session.commit()

        summary = {
            "processed": processed,
            "skipped": skipped,
            "opportunities": opportunities,
            "cautions": cautions,
            "neutrals": neutrals,
            "total_active_stocks": len(stock_ids),
        }
        logger.info(
            "Insight recalculation complete: %d processed, %d skipped, "
            "%d opportunities, %d cautions, %d neutral",
            processed,
            skipped,
            opportunities,
            cautions,
            neutrals,
        )
        return summary

    except Exception:
        session.rollback()
        logger.exception("Error during insight recalculation")
        raise
    finally:
        session.close()


@shared_task(
    bind=True,
    name="app.tasks.insight_calculator.cleanup_stale_data",
    max_retries=2,
    default_retry_delay=300,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=900,
)
def cleanup_stale_data(self) -> dict:
    """Weekly task that archives old data and cleans stale cache entries.

    Actions performed:
    1. Delete insight scores older than INSIGHT_RETENTION_DAYS (90 days)
    2. Delete trend data older than TREND_DATA_RETENTION_DAYS (365 days)
    3. Clean stale Redis cache entries (invalidate all dashboards, old search results)

    Note: Price history is retained for 7 years per Requirement 12.4
    and is not cleaned by this task.

    Returns:
        Summary dict with counts of deleted records and cache operations.
    """
    logger.info("Starting weekly stale data cleanup")

    session = _create_sync_session()
    deleted_insights = 0
    deleted_trends = 0
    cache_cleaned = False

    try:
        now = datetime.now(timezone.utc)

        # 1. Delete old insight scores (older than 90 days)
        insight_cutoff = now - timedelta(days=INSIGHT_RETENTION_DAYS)
        insight_stmt = delete(InsightScore).where(
            InsightScore.computed_at < insight_cutoff
        )
        result = session.execute(insight_stmt)
        deleted_insights = result.rowcount
        logger.info("Deleted %d stale insight scores (older than %d days)",
                    deleted_insights, INSIGHT_RETENTION_DAYS)

        # 2. Delete old trend data (older than 365 days)
        trend_cutoff = now - timedelta(days=TREND_DATA_RETENTION_DAYS)
        trend_stmt = delete(TrendData).where(
            TrendData.fetched_at < trend_cutoff
        )
        result = session.execute(trend_stmt)
        deleted_trends = result.rowcount
        logger.info("Deleted %d stale trend data records (older than %d days)",
                    deleted_trends, TREND_DATA_RETENTION_DAYS)

        session.commit()

        # 3. Clean stale Redis cache entries
        try:
            redis_client = _get_sync_redis()

            # Clean all dashboard caches (they'll be refreshed on next access)
            cursor = 0
            dashboard_keys_deleted = 0
            while True:
                cursor, keys = redis_client.scan(
                    cursor=cursor, match="dashboard:*", count=100
                )
                if keys:
                    redis_client.delete(*keys)
                    dashboard_keys_deleted += len(keys)
                if cursor == 0:
                    break

            # Clean old search result caches
            cursor = 0
            search_keys_deleted = 0
            while True:
                cursor, keys = redis_client.scan(
                    cursor=cursor, match="stock:search:*", count=100
                )
                if keys:
                    redis_client.delete(*keys)
                    search_keys_deleted += len(keys)
                if cursor == 0:
                    break

            redis_client.close()
            cache_cleaned = True
            logger.info(
                "Cache cleanup: deleted %d dashboard keys, %d search keys",
                dashboard_keys_deleted,
                search_keys_deleted,
            )

        except redis.RedisError as e:
            # Cache cleanup is non-critical; log and continue
            logger.warning("Redis cache cleanup failed (non-critical): %s", e)
            cache_cleaned = False

        summary = {
            "deleted_insights": deleted_insights,
            "deleted_trends": deleted_trends,
            "cache_cleaned": cache_cleaned,
        }
        logger.info("Weekly cleanup complete: %s", summary)
        return summary

    except Exception:
        session.rollback()
        logger.exception("Error during stale data cleanup")
        raise
    finally:
        session.close()
