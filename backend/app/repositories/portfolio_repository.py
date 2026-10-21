"""Repository layer for portfolio, holdings, transactions, and watchlist CRUD operations.

Uses async SQLAlchemy with session passed as parameter (dependency injection pattern).
Implements optimistic locking for concurrent portfolio modifications using updated_at checks.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select, delete, and_
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.portfolio import Holding, Portfolio, Transaction, WatchlistItem


class OptimisticLockError(Exception):
    """Raised when a concurrent modification is detected via optimistic locking."""

    pass


class PortfolioRepository:
    """Data access layer for portfolio-related entities."""

    # --- Portfolio operations ---

    async def get_portfolio_by_user(self, session: AsyncSession, user_id: int) -> Portfolio:
        """Get the user's default portfolio, creating one if it doesn't exist.

        Args:
            session: Async database session.
            user_id: The ID of the user.

        Returns:
            The user's portfolio instance.
        """
        stmt = select(Portfolio).where(Portfolio.user_id == user_id)
        result = await session.execute(stmt)
        portfolio = result.scalars().first()

        if portfolio is None:
            portfolio = Portfolio(user_id=user_id, name="My Portfolio")
            session.add(portfolio)
            await session.flush()
            await session.refresh(portfolio)

        return portfolio

    # --- Holding operations ---

    async def get_holding(self, session: AsyncSession, holding_id: int) -> Holding | None:
        """Retrieve a single holding by ID.

        Args:
            session: Async database session.
            holding_id: The ID of the holding.

        Returns:
            The Holding instance or None if not found.
        """
        stmt = select(Holding).where(Holding.id == holding_id)
        result = await session.execute(stmt)
        return result.scalars().first()

    async def get_holdings(self, session: AsyncSession, portfolio_id: int) -> list[Holding]:
        """Retrieve all holdings for a given portfolio.

        Args:
            session: Async database session.
            portfolio_id: The ID of the portfolio.

        Returns:
            List of Holding instances.
        """
        stmt = select(Holding).where(Holding.portfolio_id == portfolio_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def create_holding(
        self,
        session: AsyncSession,
        portfolio_id: int,
        stock_id: int,
        quantity: int,
        price: Decimal,
        transaction_date: date | None = None,
    ) -> Holding:
        """Create a new holding with an initial BUY transaction.

        Also updates the portfolio's updated_at timestamp.

        Args:
            session: Async database session.
            portfolio_id: The portfolio to add the holding to.
            stock_id: The stock being held.
            quantity: Number of shares purchased.
            price: Price per unit at purchase.
            transaction_date: Date of the transaction (defaults to today).

        Returns:
            The newly created Holding instance.
        """
        if transaction_date is None:
            transaction_date = date.today()

        holding = Holding(
            portfolio_id=portfolio_id,
            stock_id=stock_id,
            total_quantity=quantity,
            average_price=price,
        )
        session.add(holding)
        await session.flush()
        await session.refresh(holding)

        # Record the initial BUY transaction
        transaction = Transaction(
            holding_id=holding.id,
            transaction_type="BUY",
            quantity=quantity,
            price_per_unit=price,
            transaction_date=transaction_date,
        )
        session.add(transaction)

        # Touch portfolio updated_at
        await self._touch_portfolio(session, portfolio_id)

        await session.flush()
        return holding

    async def update_holding(
        self,
        session: AsyncSession,
        holding_id: int,
        quantity: int,
        price: Decimal,
        transaction_date: date | None = None,
        expected_updated_at: datetime | None = None,
    ) -> Holding:
        """Update a holding's quantity and average price with optimistic locking.

        Creates a BUY transaction for the additional shares and recalculates
        the weighted average price.

        Args:
            session: Async database session.
            holding_id: The holding to update.
            quantity: New quantity of shares to add (BUY transaction).
            price: Price per unit for this transaction.
            transaction_date: Date of the transaction (defaults to today).
            expected_updated_at: If provided, performs optimistic lock check.

        Returns:
            The updated Holding instance.

        Raises:
            OptimisticLockError: If the holding was modified concurrently.
            NoResultFound: If the holding does not exist.
        """
        if transaction_date is None:
            transaction_date = date.today()

        holding = await self.get_holding(session, holding_id)
        if holding is None:
            raise NoResultFound(f"Holding with id {holding_id} not found")

        # Optimistic locking: check updated_at hasn't changed since the client read it
        if expected_updated_at is not None and holding.updated_at != expected_updated_at:
            raise OptimisticLockError(
                f"Holding {holding_id} was modified concurrently. "
                f"Expected updated_at={expected_updated_at}, "
                f"actual={holding.updated_at}"
            )

        # Recalculate weighted average price:
        # new_avg = (old_avg * old_qty + new_price * new_qty) / (old_qty + new_qty)
        old_total_cost = holding.average_price * holding.total_quantity
        new_total_cost = price * quantity
        new_total_quantity = holding.total_quantity + quantity

        if new_total_quantity > 0:
            holding.average_price = (old_total_cost + new_total_cost) / new_total_quantity
        holding.total_quantity = new_total_quantity

        # Record the BUY transaction
        transaction = Transaction(
            holding_id=holding.id,
            transaction_type="BUY",
            quantity=quantity,
            price_per_unit=price,
            transaction_date=transaction_date,
        )
        session.add(transaction)

        # Touch portfolio updated_at
        await self._touch_portfolio(session, holding.portfolio_id)

        await session.flush()
        await session.refresh(holding)
        return holding

    async def delete_holding(self, session: AsyncSession, holding_id: int) -> None:
        """Remove a holding and all its associated transactions.

        Args:
            session: Async database session.
            holding_id: The holding to delete.

        Raises:
            NoResultFound: If the holding does not exist.
        """
        holding = await self.get_holding(session, holding_id)
        if holding is None:
            raise NoResultFound(f"Holding with id {holding_id} not found")

        portfolio_id = holding.portfolio_id

        # Delete associated transactions first
        stmt = delete(Transaction).where(Transaction.holding_id == holding_id)
        await session.execute(stmt)

        # Delete the holding
        await session.delete(holding)

        # Touch portfolio updated_at
        await self._touch_portfolio(session, portfolio_id)

        await session.flush()

    # --- Transaction operations ---

    async def create_transaction(
        self,
        session: AsyncSession,
        holding_id: int,
        transaction_type: str,
        quantity: int,
        price: Decimal,
        transaction_date: date,
    ) -> Transaction:
        """Add a transaction record to a holding.

        Args:
            session: Async database session.
            holding_id: The holding this transaction belongs to.
            transaction_type: "BUY" or "SELL".
            quantity: Number of shares in the transaction.
            price: Price per unit.
            transaction_date: Date when the transaction occurred.

        Returns:
            The newly created Transaction instance.
        """
        transaction = Transaction(
            holding_id=holding_id,
            transaction_type=transaction_type,
            quantity=quantity,
            price_per_unit=price,
            transaction_date=transaction_date,
        )
        session.add(transaction)
        await session.flush()
        await session.refresh(transaction)
        return transaction

    async def get_transactions(
        self, session: AsyncSession, holding_id: int
    ) -> list[Transaction]:
        """List all transactions for a given holding, ordered by date descending.

        Args:
            session: Async database session.
            holding_id: The holding to retrieve transactions for.

        Returns:
            List of Transaction instances ordered by transaction_date descending.
        """
        stmt = (
            select(Transaction)
            .where(Transaction.holding_id == holding_id)
            .order_by(Transaction.transaction_date.desc(), Transaction.created_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    # --- Watchlist operations ---

    async def get_watchlist(self, session: AsyncSession, user_id: int) -> list[WatchlistItem]:
        """List all watchlist items for a user.

        Args:
            session: Async database session.
            user_id: The user's ID.

        Returns:
            List of WatchlistItem instances.
        """
        stmt = (
            select(WatchlistItem)
            .where(WatchlistItem.user_id == user_id)
            .order_by(WatchlistItem.added_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def add_watchlist_item(
        self, session: AsyncSession, user_id: int, stock_id: int
    ) -> WatchlistItem:
        """Add a stock to the user's watchlist.

        If the stock is already on the watchlist, returns the existing item.

        Args:
            session: Async database session.
            user_id: The user's ID.
            stock_id: The stock to add to the watchlist.

        Returns:
            The WatchlistItem instance (new or existing).
        """
        # Check if already in watchlist
        stmt = select(WatchlistItem).where(
            and_(
                WatchlistItem.user_id == user_id,
                WatchlistItem.stock_id == stock_id,
            )
        )
        result = await session.execute(stmt)
        existing = result.scalars().first()

        if existing is not None:
            return existing

        item = WatchlistItem(user_id=user_id, stock_id=stock_id)
        session.add(item)
        await session.flush()
        await session.refresh(item)
        return item

    async def remove_watchlist_item(self, session: AsyncSession, item_id: int) -> None:
        """Remove a watchlist item by ID.

        Args:
            session: Async database session.
            item_id: The watchlist item ID to remove.

        Raises:
            NoResultFound: If the watchlist item does not exist.
        """
        stmt = select(WatchlistItem).where(WatchlistItem.id == item_id)
        result = await session.execute(stmt)
        item = result.scalars().first()

        if item is None:
            raise NoResultFound(f"WatchlistItem with id {item_id} not found")

        await session.delete(item)
        await session.flush()

    # --- Internal helpers ---

    async def _touch_portfolio(self, session: AsyncSession, portfolio_id: int) -> None:
        """Update the portfolio's updated_at timestamp to reflect a modification.

        Args:
            session: Async database session.
            portfolio_id: The portfolio to update.
        """
        stmt = select(Portfolio).where(Portfolio.id == portfolio_id)
        result = await session.execute(stmt)
        portfolio = result.scalars().first()
        if portfolio is not None:
            portfolio.updated_at = datetime.now(timezone.utc)
