"""Portfolio service implementing business logic for holdings, watchlist, and metrics.

Provides portfolio management operations including adding/updating/removing holdings,
watchlist management, transaction audit trails, and metric calculations.
Uses PortfolioRepository for data access and accepts AsyncSession via dependency injection.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.portfolio import Holding, Portfolio, Transaction, WatchlistItem
from backend.app.models.stock import Stock
from backend.app.repositories.portfolio_repository import OptimisticLockError, PortfolioRepository
from backend.app.schemas.portfolio_schemas import (
    HoldingCreate,
    HoldingResponse,
    HoldingUpdate,
    PortfolioMetrics,
    PortfolioSummary,
    WatchlistItemResponse,
)


class PortfolioServiceError(Exception):
    """Base exception for portfolio service errors."""

    pass


class HoldingNotFoundError(PortfolioServiceError):
    """Raised when a holding cannot be found."""

    pass


class StockNotFoundError(PortfolioServiceError):
    """Raised when a referenced stock cannot be found."""

    pass


class WatchlistItemNotFoundError(PortfolioServiceError):
    """Raised when a watchlist item cannot be found."""

    pass


class PortfolioService:
    """Service layer for portfolio management operations.

    Handles business logic for holdings, watchlist, and portfolio metrics.
    All methods accept an AsyncSession for transactional control.
    """

    def __init__(self, repository: PortfolioRepository | None = None) -> None:
        """Initialize PortfolioService with a repository instance.

        Args:
            repository: PortfolioRepository instance. Defaults to a new instance if not provided.
        """
        self.repository = repository or PortfolioRepository()

    # --- Holding operations ---

    async def add_holding(
        self, session: AsyncSession, user_id: int, holding_data: HoldingCreate
    ) -> Holding:
        """Add a new holding to the user's portfolio with an initial BUY transaction.

        Creates the holding, records the initial transaction, and computes the average price.
        An audit trail entry (transaction record) is automatically created.

        Args:
            session: Async database session.
            user_id: The user adding the holding.
            holding_data: Holding creation data (stock_id, quantity, price_per_unit, transaction_date).

        Returns:
            The newly created Holding instance.

        Raises:
            StockNotFoundError: If the stock_id does not reference a valid stock.
        """
        # Validate the stock exists
        await self._validate_stock_exists(session, holding_data.stock_id)

        # Get or create user's portfolio
        portfolio = await self.repository.get_portfolio_by_user(session, user_id)

        # Create the holding with initial transaction (repository handles average_price)
        holding = await self.repository.create_holding(
            session=session,
            portfolio_id=portfolio.id,
            stock_id=holding_data.stock_id,
            quantity=holding_data.quantity,
            price=holding_data.price_per_unit,
            transaction_date=holding_data.transaction_date,
        )

        return holding

    async def update_holding(
        self,
        session: AsyncSession,
        holding_id: int,
        update_data: HoldingUpdate,
        expected_updated_at: datetime | None = None,
    ) -> Holding:
        """Update an existing holding's quantity/price and recalculate metrics.

        Records a new BUY transaction for the additional shares and recalculates
        the weighted average price. An audit trail entry is created automatically.

        Args:
            session: Async database session.
            holding_id: The holding to update.
            update_data: Fields to update (quantity, price_per_unit, transaction_date).
            expected_updated_at: For optimistic locking concurrency control.

        Returns:
            The updated Holding instance.

        Raises:
            HoldingNotFoundError: If the holding does not exist.
            OptimisticLockError: If the holding was modified concurrently.
        """
        # Ensure holding exists before attempting update
        existing = await self.repository.get_holding(session, holding_id)
        if existing is None:
            raise HoldingNotFoundError(f"Holding with id {holding_id} not found")

        # Determine values for the update transaction
        quantity = update_data.quantity if update_data.quantity is not None else 0
        price = update_data.price_per_unit if update_data.price_per_unit is not None else existing.average_price
        transaction_date = update_data.transaction_date if update_data.transaction_date is not None else date.today()

        if quantity <= 0:
            raise PortfolioServiceError("Quantity must be greater than zero for an update")

        try:
            holding = await self.repository.update_holding(
                session=session,
                holding_id=holding_id,
                quantity=quantity,
                price=price,
                transaction_date=transaction_date,
                expected_updated_at=expected_updated_at,
            )
        except NoResultFound:
            raise HoldingNotFoundError(f"Holding with id {holding_id} not found")

        return holding

    async def remove_holding(self, session: AsyncSession, holding_id: int) -> None:
        """Remove a holding and all its transactions from the portfolio.

        Records a SELL transaction for audit purposes before deletion,
        then removes the holding and updates portfolio totals.

        Args:
            session: Async database session.
            holding_id: The holding to remove.

        Raises:
            HoldingNotFoundError: If the holding does not exist.
        """
        existing = await self.repository.get_holding(session, holding_id)
        if existing is None:
            raise HoldingNotFoundError(f"Holding with id {holding_id} not found")

        # Create a SELL transaction as audit trail before removal
        await self.repository.create_transaction(
            session=session,
            holding_id=holding_id,
            transaction_type="SELL",
            quantity=existing.total_quantity,
            price=existing.average_price,
            transaction_date=date.today(),
        )

        try:
            await self.repository.delete_holding(session, holding_id)
        except NoResultFound:
            raise HoldingNotFoundError(f"Holding with id {holding_id} not found")

    # --- Portfolio metrics ---

    async def calculate_portfolio_metrics(
        self, session: AsyncSession, user_id: int
    ) -> PortfolioMetrics:
        """Compute portfolio performance metrics.

        Calculates:
        - total_invested = Σ(average_price × quantity) for all holdings
        - current_value = Σ(current_price × quantity) for all holdings
        - profit_loss = current_value - total_invested
        - pct_return = (profit_loss / total_invested) × 100 when total_invested > 0

        Also identifies the top gainer and top loser stocks by percentage.

        Args:
            session: Async database session.
            user_id: The user whose metrics to compute.

        Returns:
            PortfolioMetrics with computed values.
        """
        portfolio = await self.repository.get_portfolio_by_user(session, user_id)
        holdings = await self.repository.get_holdings(session, portfolio.id)

        total_invested = Decimal("0.00")
        current_value = Decimal("0.00")
        top_gainer_stock_id: int | None = None
        top_loser_stock_id: int | None = None
        top_gainer_pct = Decimal("-999999")
        top_loser_pct = Decimal("999999")

        for holding in holdings:
            if holding.total_quantity <= 0:
                continue

            invested = holding.average_price * holding.total_quantity
            total_invested += invested

            # Get current stock price
            current_price = await self._get_stock_current_price(session, holding.stock_id)
            holding_value = current_price * holding.total_quantity
            current_value += holding_value

            # Track top gainer/loser
            if holding.average_price > 0:
                pct_change = ((current_price - holding.average_price) / holding.average_price) * 100
                if pct_change > top_gainer_pct:
                    top_gainer_pct = pct_change
                    top_gainer_stock_id = holding.stock_id
                if pct_change < top_loser_pct:
                    top_loser_pct = pct_change
                    top_loser_stock_id = holding.stock_id

        profit_loss = current_value - total_invested
        pct_return = (
            (profit_loss / total_invested) * 100 if total_invested > 0 else Decimal("0.00")
        )

        return PortfolioMetrics(
            total_invested=total_invested,
            current_value=current_value,
            profit_loss=profit_loss,
            pct_return=pct_return,
            top_gainer_stock_id=top_gainer_stock_id,
            top_loser_stock_id=top_loser_stock_id,
        )

    async def get_summary(self, session: AsyncSession, user_id: int) -> PortfolioSummary:
        """Get a portfolio summary including metrics.

        Args:
            session: Async database session.
            user_id: The user whose portfolio summary to retrieve.

        Returns:
            PortfolioSummary with portfolio info and computed metrics.
        """
        portfolio = await self.repository.get_portfolio_by_user(session, user_id)
        holdings = await self.repository.get_holdings(session, portfolio.id)
        metrics = await self.calculate_portfolio_metrics(session, user_id)

        return PortfolioSummary(
            id=portfolio.id,
            name=portfolio.name,
            total_invested=metrics.total_invested,
            current_value=metrics.current_value,
            profit_loss=metrics.profit_loss,
            pct_return=metrics.pct_return,
            holdings_count=len(holdings),
            created_at=portfolio.created_at,
            updated_at=portfolio.updated_at,
        )

    # --- Watchlist operations ---

    async def get_watchlist(self, session: AsyncSession, user_id: int) -> list[WatchlistItem]:
        """Get all watchlist items for a user.

        Args:
            session: Async database session.
            user_id: The user's ID.

        Returns:
            List of WatchlistItem instances.
        """
        return await self.repository.get_watchlist(session, user_id)

    async def add_to_watchlist(
        self, session: AsyncSession, user_id: int, stock_id: int
    ) -> WatchlistItem:
        """Add a stock to the user's watchlist.

        Args:
            session: Async database session.
            user_id: The user's ID.
            stock_id: The stock to add to the watchlist.

        Returns:
            The WatchlistItem instance (new or existing if already watched).

        Raises:
            StockNotFoundError: If the stock_id does not reference a valid stock.
        """
        await self._validate_stock_exists(session, stock_id)
        return await self.repository.add_watchlist_item(session, user_id, stock_id)

    async def remove_from_watchlist(self, session: AsyncSession, item_id: int) -> None:
        """Remove a stock from the user's watchlist.

        Args:
            session: Async database session.
            item_id: The watchlist item ID to remove.

        Raises:
            WatchlistItemNotFoundError: If the watchlist item does not exist.
        """
        try:
            await self.repository.remove_watchlist_item(session, item_id)
        except NoResultFound:
            raise WatchlistItemNotFoundError(f"WatchlistItem with id {item_id} not found")

    # --- Transaction history ---

    async def get_transactions(
        self, session: AsyncSession, holding_id: int
    ) -> list[Transaction]:
        """Get transaction audit trail for a holding.

        Args:
            session: Async database session.
            holding_id: The holding to get transactions for.

        Returns:
            List of Transaction instances ordered by date descending.

        Raises:
            HoldingNotFoundError: If the holding does not exist.
        """
        existing = await self.repository.get_holding(session, holding_id)
        if existing is None:
            raise HoldingNotFoundError(f"Holding with id {holding_id} not found")

        return await self.repository.get_transactions(session, holding_id)

    # --- Internal helpers ---

    async def _validate_stock_exists(self, session: AsyncSession, stock_id: int) -> Stock:
        """Validate that a stock exists in the database.

        Args:
            session: Async database session.
            stock_id: The stock ID to validate.

        Returns:
            The Stock instance.

        Raises:
            StockNotFoundError: If the stock does not exist.
        """
        stmt = select(Stock).where(Stock.id == stock_id)
        result = await session.execute(stmt)
        stock = result.scalars().first()

        if stock is None:
            raise StockNotFoundError(f"Stock with id {stock_id} not found")

        return stock

    async def _get_stock_current_price(
        self, session: AsyncSession, stock_id: int
    ) -> Decimal:
        """Get the current price for a stock from the database.

        Args:
            session: Async database session.
            stock_id: The stock ID.

        Returns:
            The current price as Decimal. Returns 0.00 if price is unavailable.
        """
        stmt = select(Stock.current_price).where(Stock.id == stock_id)
        result = await session.execute(stmt)
        price = result.scalar_one_or_none()

        return price if price is not None else Decimal("0.00")
