"""Pydantic schemas for stock-related API requests and responses."""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TimePeriod(str, Enum):
    """Supported time periods for historical data queries."""

    ONE_MONTH = "1m"
    THREE_MONTHS = "3m"
    SIX_MONTHS = "6m"
    ONE_YEAR = "1y"
    FIVE_YEARS = "5y"


# --- Sector Schemas ---


class SectorResponse(BaseModel):
    """Schema for sector information."""

    id: int
    name: str
    description: str

    model_config = {"from_attributes": True}


# --- Stock Schemas ---


class StockSummary(BaseModel):
    """Compact stock representation for search results and lists."""

    id: int
    bse_code: str
    ticker_symbol: str
    company_name: str
    sector_name: str
    current_price: Optional[Decimal] = None
    day_change_pct: Optional[Decimal] = None
    is_highlighted: bool = Field(
        default=False,
        description="True if daily price change exceeds 5%",
    )

    model_config = {"from_attributes": True}


class StockDetail(BaseModel):
    """Full stock information for the detail page."""

    id: int
    bse_code: str
    ticker_symbol: str
    company_name: str
    sector_id: int
    sector_name: str
    market_cap: Optional[Decimal] = None
    current_price: Optional[Decimal] = None
    day_change_pct: Optional[Decimal] = None
    week_52_high: Optional[Decimal] = None
    week_52_low: Optional[Decimal] = None
    volume: Optional[int] = None
    last_updated: Optional[datetime] = None
    is_active: bool = True
    is_highlighted: bool = Field(
        default=False,
        description="True if daily price change exceeds 5%",
    )

    model_config = {"from_attributes": True}


class StockFilter(BaseModel):
    """Query parameters for filtering stock search results."""

    query: Optional[str] = Field(
        default=None,
        description="Search term to match against name, ticker, or sector",
    )
    sector_id: Optional[int] = Field(
        default=None,
        description="Filter by sector ID",
    )
    market_cap_min: Optional[Decimal] = Field(
        default=None,
        description="Minimum market capitalization",
    )
    market_cap_max: Optional[Decimal] = Field(
        default=None,
        description="Maximum market capitalization",
    )
    price_min: Optional[Decimal] = Field(
        default=None,
        description="Minimum current price",
    )
    price_max: Optional[Decimal] = Field(
        default=None,
        description="Maximum current price",
    )


# --- Price History Schemas ---


class PriceRecord(BaseModel):
    """Single price history entry for a stock."""

    id: int
    stock_id: int
    date: date
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: int

    model_config = {"from_attributes": True}


# --- Trend Data Schemas ---


class TrendDataSchema(BaseModel):
    """Google Trends data for a stock."""

    id: int
    stock_id: int
    trend_score: int = Field(ge=0, le=100, description="Normalized trend score 0-100")
    fetched_at: datetime
    period_start: date
    period_end: date

    model_config = {"from_attributes": True}


# --- Market Summary Schemas ---


class PriceQuote(BaseModel):
    """Current price quote for a stock, potentially cached."""

    stock_id: int
    ticker_symbol: str
    company_name: str
    current_price: Decimal
    day_change_pct: Optional[Decimal] = None
    volume: Optional[int] = None
    last_updated: Optional[datetime] = None
    is_stale: bool = Field(
        default=False,
        description="True if data is served from cache due to feed unavailability",
    )


class MarketSummary(BaseModel):
    """BSE market overview including Sensex and top movers."""

    sensex_value: Decimal
    sensex_change_pct: Decimal
    last_updated: datetime
    top_gainers: list["StockSummary"] = Field(default_factory=list)
    top_losers: list["StockSummary"] = Field(default_factory=list)
    is_market_open: bool = False
