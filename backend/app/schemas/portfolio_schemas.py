"""Pydantic schemas for portfolio, holdings, transactions, and watchlist."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


# --- Holding Schemas ---


class HoldingCreate(BaseModel):
    """Schema for adding a new holding to a portfolio."""

    stock_id: int = Field(..., description="ID of the stock to add")
    quantity: int = Field(..., gt=0, description="Number of shares purchased")
    price_per_unit: Decimal = Field(..., gt=0, description="Purchase price per share")
    transaction_date: date = Field(..., description="Date of the purchase transaction")


class HoldingUpdate(BaseModel):
    """Schema for updating an existing holding."""

    quantity: int | None = Field(None, gt=0, description="Updated quantity of shares")
    price_per_unit: Decimal | None = Field(None, gt=0, description="Updated price per unit")
    transaction_date: date | None = Field(None, description="Date of the update transaction")


class TransactionResponse(BaseModel):
    """Schema for transaction data in responses."""

    id: int
    holding_id: int
    transaction_type: str
    quantity: int
    price_per_unit: Decimal
    transaction_date: date
    created_at: datetime

    model_config = {"from_attributes": True}


class HoldingResponse(BaseModel):
    """Schema for holding data in responses."""

    id: int
    portfolio_id: int
    stock_id: int
    total_quantity: int
    average_price: Decimal
    created_at: datetime
    updated_at: datetime
    transactions: list[TransactionResponse] = []

    model_config = {"from_attributes": True}


# --- Portfolio Schemas ---


class PortfolioSummary(BaseModel):
    """Summary view of a user's portfolio."""

    id: int
    name: str
    total_invested: Decimal = Field(
        default=Decimal("0.00"), description="Sum of (average_price * quantity) for all holdings"
    )
    current_value: Decimal = Field(
        default=Decimal("0.00"), description="Sum of (current_price * quantity) for all holdings"
    )
    profit_loss: Decimal = Field(
        default=Decimal("0.00"), description="current_value - total_invested"
    )
    pct_return: Decimal = Field(
        default=Decimal("0.00"),
        description="(profit_loss / total_invested) * 100 when total_invested > 0",
    )
    holdings_count: int = Field(default=0, description="Number of holdings in portfolio")
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PortfolioMetrics(BaseModel):
    """Computed portfolio performance metrics."""

    total_invested: Decimal = Field(
        ..., description="Sum of (average_price * quantity) for all holdings"
    )
    current_value: Decimal = Field(
        ..., description="Sum of (current_price * quantity) for all holdings"
    )
    profit_loss: Decimal = Field(..., description="current_value - total_invested")
    pct_return: Decimal = Field(
        ..., description="(profit_loss / total_invested) * 100 when total_invested > 0"
    )
    top_gainer_stock_id: int | None = Field(
        None, description="Stock ID with highest percentage gain"
    )
    top_loser_stock_id: int | None = Field(
        None, description="Stock ID with highest percentage loss"
    )


# --- Watchlist Schemas ---


class WatchlistItemCreate(BaseModel):
    """Schema for adding a stock to the watchlist."""

    stock_id: int = Field(..., description="ID of the stock to watch")


class WatchlistItemResponse(BaseModel):
    """Schema for watchlist item in responses."""

    id: int
    user_id: int
    stock_id: int
    added_at: datetime

    model_config = {"from_attributes": True}
