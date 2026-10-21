"""Stock service for business logic around stock search, detail, and market data.

Provides async methods for searching stocks, retrieving details, price history,
trend scores, market summaries, and current prices with Redis caching.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.cache import (
    get_stock_price,
    set_stock_price,
    get_trend_score as cache_get_trend_score,
    set_trend_score as cache_set_trend_score,
)
from backend.app.database import async_session_factory
from backend.app.models.stock import PriceHistory, Sector, Stock, TrendData
from backend.app.repositories.stock_repository import StockRepository
from backend.app.schemas.stock_schemas import (
    MarketSummary,
    PriceQuote,
    PriceRecord,
    StockDetail,
    StockFilter,
    StockSummary,
    TimePeriod,
    TrendDataSchema,
)


# Mapping from TimePeriod enum to number of days for date range calculation
_PERIOD_DAYS: dict[TimePeriod, int] = {
    TimePeriod.ONE_MONTH: 30,
    TimePeriod.THREE_MONTHS: 90,
    TimePeriod.SIX_MONTHS: 180,
    TimePeriod.ONE_YEAR: 365,
    TimePeriod.FIVE_YEARS: 1825,
}

# Threshold for highlighting a stock (absolute day change > 5%)
_HIGHLIGHT_THRESHOLD = Decimal("5")


def _is_highlighted(day_change_pct: Optional[Decimal]) -> bool:
    """Determine if a stock should be highlighted based on daily change percentage.

    A stock is highlighted when abs(day_change_pct) > 5.
    """
    if day_change_pct is None:
        return False
    return abs(day_change_pct) > _HIGHLIGHT_THRESHOLD


class StockService:
    """Service layer for stock-related business operations."""

    def __init__(self, session: Optional[AsyncSession] = None):
        """Initialize StockService with an optional database session.

        Args:
            session: Optional async session. If not provided, sessions are created
                     from the session factory per operation.
        """
        self._session = session
        self._repository = StockRepository(session=session)

    async def _get_session(self) -> AsyncSession:
        """Get the current session or create a new one."""
        if self._session:
            return self._session
        return async_session_factory()

    async def search(
        self,
        query: Optional[str] = None,
        filters: Optional[StockFilter] = None,
    ) -> list[StockSummary]:
        """Search stocks by name/ticker/sector with optional filters.

        Combines text search with filtering by sector, market cap range, and
        price range. Returns results within 2 seconds for standard datasets.

        Args:
            query: Search term to match against name, ticker, or sector.
            filters: Optional filter criteria (sector, market_cap, price ranges).

        Returns:
            List of StockSummary objects matching the search criteria.
        """
        effective_query = query
        if filters and filters.query:
            effective_query = filters.query

        # Determine if we need text search, filter-only, or combined
        has_text_query = effective_query is not None and effective_query.strip() != ""
        has_filters = filters is not None and any([
            filters.sector_id,
            filters.market_cap_min,
            filters.market_cap_max,
            filters.price_min,
            filters.price_max,
        ])

        stocks: Sequence[Stock] = []

        if has_text_query and has_filters:
            # Text search first, then apply filters in-memory for simplicity
            # The repository search already handles text matching
            search_results = await self._repository.search_stocks(
                query=effective_query,
                limit=200,  # Fetch more to allow filtering
            )
            stocks = self._apply_filters(search_results, filters)
        elif has_text_query:
            stocks = await self._repository.search_stocks(
                query=effective_query,
                limit=20,
            )
        elif has_filters:
            stocks = await self._repository.filter_stocks(
                sector_id=filters.sector_id,
                market_cap_min=filters.market_cap_min,
                market_cap_max=filters.market_cap_max,
                price_min=filters.price_min,
                price_max=filters.price_max,
                limit=50,
            )
        else:
            # No query and no filters — return a default set of active stocks
            stocks = await self._repository.filter_stocks(limit=20)

        return [self._to_stock_summary(stock) for stock in stocks]

    async def get_detail(self, stock_id: int) -> Optional[StockDetail]:
        """Get full stock information including price, volume, 52-week range, and market cap.

        Args:
            stock_id: The stock's primary key.

        Returns:
            StockDetail with all fields populated, or None if stock not found.
        """
        stock = await self._repository.get_by_id(stock_id)
        if stock is None:
            return None

        sector_name = stock.sector.name if stock.sector else "Unknown"

        return StockDetail(
            id=stock.id,
            bse_code=stock.bse_code,
            ticker_symbol=stock.ticker_symbol,
            company_name=stock.company_name,
            sector_id=stock.sector_id,
            sector_name=sector_name,
            market_cap=stock.market_cap,
            current_price=stock.current_price,
            day_change_pct=stock.day_change_pct,
            week_52_high=stock.week_52_high,
            week_52_low=stock.week_52_low,
            volume=stock.volume,
            last_updated=stock.last_updated,
            is_active=stock.is_active,
            is_highlighted=_is_highlighted(stock.day_change_pct),
        )

    async def get_history(
        self,
        stock_id: int,
        period: TimePeriod = TimePeriod.ONE_YEAR,
    ) -> list[PriceRecord]:
        """Get price history for a stock over a given time period.

        Args:
            stock_id: The stock's primary key.
            period: Time period for historical data (e.g., 1m, 3m, 6m, 1y, 5y).

        Returns:
            List of PriceRecord objects ordered by date descending.
        """
        days = _PERIOD_DAYS.get(period, 365)
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        records = await self._repository.get_price_history(
            stock_id=stock_id,
            start_date=start_date,
            end_date=end_date,
            limit=days,
        )

        return [
            PriceRecord(
                id=record.id,
                stock_id=record.stock_id,
                date=record.date,
                open_price=record.open_price,
                high_price=record.high_price,
                low_price=record.low_price,
                close_price=record.close_price,
                volume=record.volume,
            )
            for record in records
        ]

    async def get_trend_score(self, stock_id: int) -> Optional[TrendDataSchema]:
        """Get the latest trend data for a stock.

        Checks Redis cache first, falls back to database query.

        Args:
            stock_id: The stock's primary key.

        Returns:
            TrendDataSchema with the latest trend score, or None if unavailable.
        """
        # Check cache first
        cached = await cache_get_trend_score(stock_id)
        if cached is not None:
            return TrendDataSchema(**cached)

        # Fallback to database
        session = await self._get_session()
        try:
            stmt = (
                select(TrendData)
                .where(TrendData.stock_id == stock_id)
                .order_by(desc(TrendData.fetched_at))
                .limit(1)
            )
            result = await session.execute(stmt)
            trend = result.scalar_one_or_none()

            if trend is None:
                return None

            trend_schema = TrendDataSchema(
                id=trend.id,
                stock_id=trend.stock_id,
                trend_score=trend.trend_score,
                fetched_at=trend.fetched_at,
                period_start=trend.period_start,
                period_end=trend.period_end,
            )

            # Cache the result for future requests
            await cache_set_trend_score(stock_id, trend_schema.model_dump(mode="json"))

            return trend_schema
        finally:
            if not self._session:
                await session.close()

    async def get_market_summary(self) -> MarketSummary:
        """Get BSE market summary including Sensex value, daily change, and top movers.

        Returns:
            MarketSummary with sensex value, change percentage, top gainers, and top losers.
        """
        session = await self._get_session()
        try:
            # Get top gainers (highest positive day_change_pct)
            gainers_stmt = (
                select(Stock)
                .where(Stock.is_active.is_(True))
                .where(Stock.day_change_pct.isnot(None))
                .order_by(desc(Stock.day_change_pct))
                .limit(5)
            )
            gainers_result = await session.execute(gainers_stmt)
            top_gainers = gainers_result.scalars().all()

            # Get top losers (lowest/most negative day_change_pct)
            losers_stmt = (
                select(Stock)
                .where(Stock.is_active.is_(True))
                .where(Stock.day_change_pct.isnot(None))
                .order_by(Stock.day_change_pct.asc())
                .limit(5)
            )
            losers_result = await session.execute(losers_stmt)
            top_losers = losers_result.scalars().all()

            # Calculate Sensex approximation from stock data
            # In production, this would come from BSE API directly
            # For now, compute a weighted market summary from available stocks
            sensex_stmt = (
                select(Stock)
                .where(Stock.is_active.is_(True))
                .where(Stock.current_price.isnot(None))
                .where(Stock.day_change_pct.isnot(None))
                .order_by(desc(Stock.market_cap))
                .limit(30)
            )
            sensex_result = await session.execute(sensex_stmt)
            sensex_stocks = sensex_result.scalars().all()

            # Approximate Sensex value and change from top market cap stocks
            if sensex_stocks:
                total_market_cap = sum(
                    s.market_cap for s in sensex_stocks if s.market_cap
                )
                weighted_change = Decimal("0")
                for s in sensex_stocks:
                    if s.market_cap and s.day_change_pct and total_market_cap > 0:
                        weight = s.market_cap / total_market_cap
                        weighted_change += weight * s.day_change_pct

                sensex_value = total_market_cap / Decimal("1000000")  # Normalize
                sensex_change_pct = weighted_change
            else:
                sensex_value = Decimal("0")
                sensex_change_pct = Decimal("0")

            # Determine if market is open (IST: 9:15 AM - 3:30 PM, Mon-Fri)
            now_utc = datetime.now(timezone.utc)
            ist_offset = timedelta(hours=5, minutes=30)
            now_ist = now_utc + ist_offset
            is_market_open = (
                now_ist.weekday() < 5  # Mon-Fri
                and (now_ist.hour > 9 or (now_ist.hour == 9 and now_ist.minute >= 15))
                and (now_ist.hour < 15 or (now_ist.hour == 15 and now_ist.minute <= 30))
            )

            return MarketSummary(
                sensex_value=sensex_value,
                sensex_change_pct=sensex_change_pct,
                last_updated=now_utc,
                top_gainers=[self._to_stock_summary(s) for s in top_gainers],
                top_losers=[self._to_stock_summary(s) for s in top_losers],
                is_market_open=is_market_open,
            )
        finally:
            if not self._session:
                await session.close()

    async def get_current_price(self, stock_id: int) -> Optional[PriceQuote]:
        """Get the current price for a stock, checking Redis cache first.

        If cached data exists in Redis, returns it immediately.
        Otherwise falls back to the database and caches the result.

        Args:
            stock_id: The stock's primary key.

        Returns:
            PriceQuote with current price data, or None if stock not found.
        """
        # Check Redis cache first
        cached = await get_stock_price(stock_id)
        if cached is not None:
            return PriceQuote(**cached)

        # Fallback to database
        stock = await self._repository.get_by_id(stock_id)
        if stock is None:
            return None

        price_quote = PriceQuote(
            stock_id=stock.id,
            ticker_symbol=stock.ticker_symbol,
            company_name=stock.company_name,
            current_price=stock.current_price or Decimal("0"),
            day_change_pct=stock.day_change_pct,
            volume=stock.volume,
            last_updated=stock.last_updated,
            is_stale=False,
        )

        # Cache the result for future requests
        await set_stock_price(stock_id, price_quote.model_dump(mode="json"))

        return price_quote

    # --- Private helper methods ---

    def _to_stock_summary(self, stock: Stock) -> StockSummary:
        """Convert a Stock model to a StockSummary schema.

        Args:
            stock: The Stock ORM model instance.

        Returns:
            StockSummary with is_highlighted computed from day_change_pct.
        """
        sector_name = stock.sector.name if stock.sector else "Unknown"

        return StockSummary(
            id=stock.id,
            bse_code=stock.bse_code,
            ticker_symbol=stock.ticker_symbol,
            company_name=stock.company_name,
            sector_name=sector_name,
            current_price=stock.current_price,
            day_change_pct=stock.day_change_pct,
            is_highlighted=_is_highlighted(stock.day_change_pct),
        )

    def _apply_filters(
        self,
        stocks: Sequence[Stock],
        filters: Optional[StockFilter],
    ) -> list[Stock]:
        """Apply filter criteria to a sequence of stocks in memory.

        Used when combining text search results with additional filters.

        Args:
            stocks: Sequence of Stock objects to filter.
            filters: Filter criteria to apply.

        Returns:
            Filtered list of Stock objects satisfying all criteria.
        """
        if filters is None:
            return list(stocks)

        result = []
        for stock in stocks:
            if filters.sector_id is not None and stock.sector_id != filters.sector_id:
                continue
            if filters.market_cap_min is not None and (
                stock.market_cap is None or stock.market_cap < filters.market_cap_min
            ):
                continue
            if filters.market_cap_max is not None and (
                stock.market_cap is None or stock.market_cap > filters.market_cap_max
            ):
                continue
            if filters.price_min is not None and (
                stock.current_price is None or stock.current_price < filters.price_min
            ):
                continue
            if filters.price_max is not None and (
                stock.current_price is None or stock.current_price > filters.price_max
            ):
                continue
            result.append(stock)

        return result
