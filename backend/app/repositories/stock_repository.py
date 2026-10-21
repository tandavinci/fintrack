"""Stock repository for database access operations.

Provides async methods for searching, filtering, and retrieving stock data
using SQLAlchemy queries against PostgreSQL.
"""

from datetime import date
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import async_session_factory
from backend.app.models.stock import PriceHistory, Sector, Stock


class StockRepository:
    """Repository for stock-related database operations."""

    def __init__(self, session: Optional[AsyncSession] = None):
        """Initialize repository with an optional session.

        If no session is provided, the repository will create one from the
        session factory for each operation.
        """
        self._session = session

    async def _get_session(self) -> AsyncSession:
        """Get the current session or create a new one."""
        if self._session:
            return self._session
        return async_session_factory()

    async def search_stocks(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[Stock]:
        """Search stocks by name, ticker symbol, or sector name using fuzzy matching.

        Uses PostgreSQL trigram similarity (pg_trgm) for fuzzy name matching when
        available, with ILIKE as a fallback approach.

        Args:
            query: Search term to match against company_name, ticker_symbol, or sector name.
            limit: Maximum number of results to return.
            offset: Number of results to skip for pagination.

        Returns:
            List of Stock objects matching the search criteria.
        """
        session = await self._get_session()
        try:
            search_term = f"%{query}%"

            # Build query with ILIKE matching on name, ticker, and sector name
            stmt = (
                select(Stock)
                .join(Sector, Stock.sector_id == Sector.id)
                .where(
                    or_(
                        Stock.company_name.ilike(search_term),
                        Stock.ticker_symbol.ilike(search_term),
                        Sector.name.ilike(search_term),
                    )
                )
                .where(Stock.is_active.is_(True))
            )

            # Try to use trigram similarity for better ranking when available
            # Falls back to alphabetical ordering if pg_trgm is not installed
            try:
                similarity_expr = func.similarity(Stock.company_name, query)
                stmt = stmt.order_by(similarity_expr.desc())
            except Exception:
                stmt = stmt.order_by(Stock.company_name.asc())

            stmt = stmt.offset(offset).limit(limit)

            result = await session.execute(stmt)
            return result.scalars().all()
        finally:
            if not self._session:
                await session.close()

    async def get_by_id(self, stock_id: int) -> Optional[Stock]:
        """Get a single stock by its primary key ID.

        Args:
            stock_id: The stock's primary key.

        Returns:
            The Stock object if found, None otherwise.
        """
        session = await self._get_session()
        try:
            stmt = select(Stock).where(Stock.id == stock_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()
        finally:
            if not self._session:
                await session.close()

    async def get_price_history(
        self,
        stock_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 365,
    ) -> Sequence[PriceHistory]:
        """Get historical price data for a stock within an optional date range.

        Args:
            stock_id: The stock's primary key.
            start_date: Start of the date range (inclusive). If None, no lower bound.
            end_date: End of the date range (inclusive). If None, no upper bound.
            limit: Maximum number of records to return.

        Returns:
            List of PriceHistory records ordered by date descending.
        """
        session = await self._get_session()
        try:
            stmt = select(PriceHistory).where(PriceHistory.stock_id == stock_id)

            if start_date is not None:
                stmt = stmt.where(PriceHistory.date >= start_date)
            if end_date is not None:
                stmt = stmt.where(PriceHistory.date <= end_date)

            stmt = stmt.order_by(PriceHistory.date.desc()).limit(limit)

            result = await session.execute(stmt)
            return result.scalars().all()
        finally:
            if not self._session:
                await session.close()

    async def get_sectors(self) -> Sequence[Sector]:
        """List all available sectors.

        Returns:
            List of all Sector objects ordered by name.
        """
        session = await self._get_session()
        try:
            stmt = select(Sector).order_by(Sector.name.asc())
            result = await session.execute(stmt)
            return result.scalars().all()
        finally:
            if not self._session:
                await session.close()

    async def filter_stocks(
        self,
        sector_id: Optional[int] = None,
        market_cap_min: Optional[Decimal] = None,
        market_cap_max: Optional[Decimal] = None,
        price_min: Optional[Decimal] = None,
        price_max: Optional[Decimal] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Stock]:
        """Filter stocks by sector, market cap range, and price range.

        All filters are optional and applied cumulatively (AND logic).
        Only active stocks are returned.

        Args:
            sector_id: Filter by sector ID.
            market_cap_min: Minimum market capitalization (inclusive).
            market_cap_max: Maximum market capitalization (inclusive).
            price_min: Minimum current price (inclusive).
            price_max: Maximum current price (inclusive).
            limit: Maximum number of results to return.
            offset: Number of results to skip for pagination.

        Returns:
            List of Stock objects satisfying all applied filters.
        """
        session = await self._get_session()
        try:
            stmt = select(Stock).where(Stock.is_active.is_(True))

            if sector_id is not None:
                stmt = stmt.where(Stock.sector_id == sector_id)

            if market_cap_min is not None:
                stmt = stmt.where(Stock.market_cap >= market_cap_min)

            if market_cap_max is not None:
                stmt = stmt.where(Stock.market_cap <= market_cap_max)

            if price_min is not None:
                stmt = stmt.where(Stock.current_price >= price_min)

            if price_max is not None:
                stmt = stmt.where(Stock.current_price <= price_max)

            stmt = stmt.order_by(Stock.company_name.asc()).offset(offset).limit(limit)

            result = await session.execute(stmt)
            return result.scalars().all()
        finally:
            if not self._session:
                await session.close()
