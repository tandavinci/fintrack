"""Celery task for updating stock prices from BSE India.

Fetches latest prices using the bsedata library during market hours
and updates stock records in the database. Invalidates Redis price cache
after each successful update. Handles BSE API unavailability by serving
cached prices with a stale indicator.

Schedule: Every 60 seconds during market hours (9:15 AM - 3:30 PM IST, Mon-Fri).
"""

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from celery import shared_task
from sqlalchemy import select, update

from backend.app.cache import (
    get_stock_price,
    invalidate_stock_price,
    set_stock_price,
)
from backend.app.celery_app import is_market_hours
from backend.app.database import async_session_factory
from backend.app.models.stock import Stock

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async coroutine from synchronous Celery task context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If an event loop is already running, create a new one
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _fetch_bse_quote(scrip_code: str) -> dict[str, Any] | None:
    """Fetch stock quote from BSE using bsedata library.

    Args:
        scrip_code: BSE scrip code for the stock.

    Returns:
        Dictionary with quote data or None if fetch fails.
    """
    try:
        from bsedata.bse import BSE

        bse = BSE()
        quote = bse.getQuote(scrip_code)
        if quote and isinstance(quote, dict):
            return quote
        return None
    except Exception as exc:
        logger.warning("Failed to fetch BSE quote for scrip %s: %s", scrip_code, exc)
        return None


def _parse_price_data(quote: dict[str, Any]) -> dict[str, Any] | None:
    """Parse relevant fields from BSE quote response.

    Args:
        quote: Raw quote dictionary from bsedata.

    Returns:
        Parsed price data dict with current_price, day_change_pct, volume,
        or None if parsing fails.
    """
    try:
        # bsedata returns fields like 'currentValue', 'pChange', 'totalTradedQuantity'
        current_price_str = quote.get("currentValue", "").replace(",", "")
        pct_change_str = quote.get("pChange", "0")
        volume_str = quote.get("totalTradedQuantity", "0").replace(",", "")

        current_price = Decimal(current_price_str) if current_price_str else None
        day_change_pct = Decimal(pct_change_str) if pct_change_str else Decimal("0")

        try:
            volume = int(volume_str) if volume_str else 0
        except (ValueError, TypeError):
            volume = 0

        if current_price is None or current_price <= 0:
            return None

        return {
            "current_price": current_price,
            "day_change_pct": day_change_pct,
            "volume": volume,
        }
    except (InvalidOperation, ValueError, TypeError) as exc:
        logger.warning("Failed to parse BSE quote data: %s", exc)
        return None


async def _get_active_stocks() -> list[dict[str, Any]]:
    """Retrieve all active stocks from the database.

    Returns:
        List of dicts with id and bse_code for active stocks.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(Stock.id, Stock.bse_code).where(Stock.is_active == True)
        )
        return [{"id": row.id, "bse_code": row.bse_code} for row in result.all()]


async def _update_stock_price(stock_id: int, price_data: dict[str, Any]) -> None:
    """Update stock price in the database.

    Args:
        stock_id: The stock's primary key.
        price_data: Dict containing current_price, day_change_pct, volume.
    """
    async with async_session_factory() as session:
        await session.execute(
            update(Stock)
            .where(Stock.id == stock_id)
            .values(
                current_price=price_data["current_price"],
                day_change_pct=price_data["day_change_pct"],
                volume=price_data["volume"],
                last_updated=datetime.now(timezone.utc),
            )
        )
        await session.commit()


async def _invalidate_and_cache_price(stock_id: int, price_data: dict[str, Any]) -> None:
    """Invalidate old cache and set new price data in Redis.

    Args:
        stock_id: The stock's primary key.
        price_data: Dict containing current_price, day_change_pct, volume.
    """
    # Invalidate the old cached price
    await invalidate_stock_price(stock_id)

    # Cache the new price data with fresh TTL
    cache_data = {
        "current_price": str(price_data["current_price"]),
        "day_change_pct": str(price_data["day_change_pct"]),
        "volume": price_data["volume"],
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "is_stale": False,
    }
    await set_stock_price(stock_id, cache_data)


async def _mark_price_stale(stock_id: int) -> None:
    """Mark cached price as stale when BSE API is unavailable.

    If cached data exists, updates the stale indicator. If no cache exists,
    creates a minimal stale entry from the database.

    Args:
        stock_id: The stock's primary key.
    """
    cached = await get_stock_price(stock_id)

    if cached is not None:
        # Mark existing cached data as stale
        if isinstance(cached, dict):
            cached["is_stale"] = True
            await set_stock_price(stock_id, cached)
    else:
        # No cache exists — fetch from DB and cache with stale indicator
        async with async_session_factory() as session:
            result = await session.execute(
                select(Stock.current_price, Stock.day_change_pct, Stock.volume, Stock.last_updated)
                .where(Stock.id == stock_id)
            )
            row = result.first()
            if row and row.current_price is not None:
                stale_data = {
                    "current_price": str(row.current_price),
                    "day_change_pct": str(row.day_change_pct) if row.day_change_pct else "0",
                    "volume": row.volume or 0,
                    "last_updated": row.last_updated.isoformat() if row.last_updated else None,
                    "is_stale": True,
                }
                await set_stock_price(stock_id, stale_data)


async def _publish_price_update(stock_id: int, price_data: dict[str, Any]) -> None:
    """Publish a price update to Redis pub/sub for WebSocket clients.

    Args:
        stock_id: The stock's primary key.
        price_data: Dict containing current_price, day_change_pct, volume.
    """
    from backend.app.routers.websocket import publish_price_update

    # Convert Decimal values to strings for JSON serialization
    serializable_data = {
        "current_price": str(price_data["current_price"]),
        "day_change_pct": str(price_data["day_change_pct"]),
        "volume": price_data["volume"],
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    await publish_price_update(stock_id, serializable_data)


@shared_task(
    bind=True,
    name="app.tasks.price_updater.update_stock_prices",
    max_retries=2,
    default_retry_delay=10,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_backoff=True,
    retry_backoff_max=30,
    expires=55,
)
def update_stock_prices(self) -> dict[str, Any]:
    """Celery task to fetch and update stock prices from BSE.

    Skips execution if outside market hours. Fetches prices for all active
    stocks, updates the database, and invalidates/refreshes the Redis cache.
    Handles BSE API unavailability by marking prices as stale.

    Returns:
        Summary dict with counts of updated, failed, and skipped stocks.
    """
    # Skip if outside market hours
    if not is_market_hours():
        logger.info("Skipping price update: outside market hours")
        return {"status": "skipped", "reason": "outside_market_hours"}

    # Get all active stocks
    stocks = _run_async(_get_active_stocks())

    if not stocks:
        logger.info("No active stocks found for price update")
        return {"status": "completed", "updated": 0, "failed": 0, "total": 0}

    updated_count = 0
    failed_count = 0

    for stock in stocks:
        stock_id = stock["id"]
        bse_code = stock["bse_code"]

        # Fetch quote from BSE
        quote = _fetch_bse_quote(bse_code)

        if quote is None:
            # BSE API unavailable for this stock — mark as stale
            logger.warning(
                "BSE API unavailable for stock %s (id=%d), marking price as stale",
                bse_code,
                stock_id,
            )
            _run_async(_mark_price_stale(stock_id))
            failed_count += 1
            continue

        # Parse the price data
        price_data = _parse_price_data(quote)

        if price_data is None:
            logger.warning(
                "Failed to parse price data for stock %s (id=%d)",
                bse_code,
                stock_id,
            )
            _run_async(_mark_price_stale(stock_id))
            failed_count += 1
            continue

        # Update database
        try:
            _run_async(_update_stock_price(stock_id, price_data))
        except Exception as exc:
            logger.error(
                "Database update failed for stock %s (id=%d): %s",
                bse_code,
                stock_id,
                exc,
            )
            failed_count += 1
            continue

        # Invalidate cache and set new price
        try:
            _run_async(_invalidate_and_cache_price(stock_id, price_data))
        except Exception as exc:
            # Cache invalidation failure is non-critical
            logger.warning(
                "Cache invalidation failed for stock %s (id=%d): %s",
                bse_code,
                stock_id,
                exc,
            )

        # Publish price update to Redis pub/sub for WebSocket clients
        try:
            _run_async(_publish_price_update(stock_id, price_data))
        except Exception as exc:
            # Pub/sub failure is non-critical
            logger.warning(
                "Redis pub/sub publish failed for stock %s (id=%d): %s",
                bse_code,
                stock_id,
                exc,
            )

        updated_count += 1

    logger.info(
        "Price update completed: %d updated, %d failed, %d total",
        updated_count,
        failed_count,
        len(stocks),
    )

    return {
        "status": "completed",
        "updated": updated_count,
        "failed": failed_count,
        "total": len(stocks),
    }
