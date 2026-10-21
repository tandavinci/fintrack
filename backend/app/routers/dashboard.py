"""Dashboard router with endpoints for portfolio overview, market data, and trending stocks.

Provides consolidated views assembling data from portfolio, stock, and alert services
with Redis caching (60s TTL) for performance.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.cache import get_dashboard_data, set_dashboard_data
from backend.app.dependencies import get_current_user, get_db
from backend.app.models.alert import Alert
from backend.app.models.stock import Stock, TrendData
from backend.app.models.portfolio import WatchlistItem
from backend.app.schemas.alert_schemas import AlertResponse
from backend.app.schemas.portfolio_schemas import PortfolioSummary
from backend.app.schemas.stock_schemas import MarketSummary, StockSummary
from backend.app.services.portfolio_service import PortfolioService
from backend.app.services.stock_service import StockService

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


# --- Response Schemas ---


class TrendingStock(BaseModel):
    """A stock with its trend score change over the last 24 hours."""

    id: int
    bse_code: str
    ticker_symbol: str
    company_name: str
    sector_name: str
    current_price: Optional[Decimal] = None
    day_change_pct: Optional[Decimal] = None
    trend_score_current: int = Field(description="Most recent trend score")
    trend_score_delta: int = Field(description="Change in trend score over last 24 hours")


class DashboardResponse(BaseModel):
    """Full dashboard data including portfolio, market, alerts, and trends."""

    portfolio_summary: Optional[PortfolioSummary] = None
    top_gainers: list[StockSummary] = Field(default_factory=list)
    top_losers: list[StockSummary] = Field(default_factory=list)
    active_alerts: list[AlertResponse] = Field(default_factory=list)
    sensex_value: Decimal = Decimal("0")
    sensex_change_pct: Decimal = Decimal("0")
    trending_stocks: list[TrendingStock] = Field(default_factory=list)
    is_market_open: bool = False
    last_updated: Optional[datetime] = None


class MarketOverviewResponse(BaseModel):
    """Market overview with Sensex and top movers."""

    sensex_value: Decimal
    sensex_change_pct: Decimal
    top_gainers: list[StockSummary] = Field(default_factory=list)
    top_losers: list[StockSummary] = Field(default_factory=list)
    is_market_open: bool = False
    last_updated: datetime


class TrendingResponse(BaseModel):
    """Top 5 trending stocks by 24h trend score change."""

    trending_stocks: list[TrendingStock] = Field(default_factory=list)
    last_updated: datetime


# --- Helper functions ---


async def _get_top_gainers_losers_from_watchlist(
    session: AsyncSession, user_id: int
) -> tuple[list[StockSummary], list[StockSummary]]:
    """Get top gainers and losers from the user's watchlist stocks."""
    # Get user's watchlist stock IDs
    watchlist_stmt = select(WatchlistItem.stock_id).where(
        WatchlistItem.user_id == user_id
    )
    result = await session.execute(watchlist_stmt)
    watchlist_stock_ids = [row[0] for row in result.all()]

    if not watchlist_stock_ids:
        return [], []

    # Get those stocks with their price changes
    stocks_stmt = (
        select(Stock)
        .where(Stock.id.in_(watchlist_stock_ids))
        .where(Stock.is_active.is_(True))
        .where(Stock.day_change_pct.isnot(None))
    )
    stocks_result = await session.execute(stocks_stmt)
    stocks = stocks_result.scalars().all()

    if not stocks:
        return [], []

    # Sort by day_change_pct
    sorted_stocks = sorted(
        stocks,
        key=lambda s: s.day_change_pct if s.day_change_pct else Decimal("0"),
        reverse=True,
    )

    def _to_summary(stock: Stock) -> StockSummary:
        sector_name = stock.sector.name if stock.sector else "Unknown"
        day_change = stock.day_change_pct or Decimal("0")
        return StockSummary(
            id=stock.id,
            bse_code=stock.bse_code,
            ticker_symbol=stock.ticker_symbol,
            company_name=stock.company_name,
            sector_name=sector_name,
            current_price=stock.current_price,
            day_change_pct=stock.day_change_pct,
            is_highlighted=abs(day_change) > Decimal("5"),
        )

    # Top gainers (positive change) and top losers (negative change)
    gainers = [_to_summary(s) for s in sorted_stocks[:5] if (s.day_change_pct or 0) > 0]
    losers = [_to_summary(s) for s in reversed(sorted_stocks) if (s.day_change_pct or 0) < 0][:5]

    return gainers, losers


async def _get_active_alerts(session: AsyncSession, user_id: int) -> list[AlertResponse]:
    """Get active alerts for the user."""
    stmt = (
        select(Alert)
        .where(Alert.user_id == user_id)
        .where(Alert.is_active.is_(True))
        .order_by(desc(Alert.created_at))
    )
    result = await session.execute(stmt)
    alerts = result.scalars().all()
    return [AlertResponse.model_validate(alert) for alert in alerts]


async def _get_trending_stocks(session: AsyncSession) -> list[TrendingStock]:
    """Get top 5 stocks by trend_score_delta in the last 24 hours.

    Computes delta by comparing the most recent trend_score with the one
    from approximately 24 hours earlier for each stock.
    """
    now = datetime.now(timezone.utc)
    twenty_four_hours_ago = now - timedelta(hours=24)

    # Get the most recent trend data per stock (within the last 48h to ensure coverage)
    recent_cutoff = now - timedelta(hours=48)

    # Subquery: latest trend score per stock
    latest_subq = (
        select(
            TrendData.stock_id,
            func.max(TrendData.fetched_at).label("max_fetched_at"),
        )
        .where(TrendData.fetched_at >= recent_cutoff)
        .group_by(TrendData.stock_id)
        .subquery()
    )

    # Get the latest trend data joined with stock info
    latest_trends_stmt = (
        select(TrendData, Stock)
        .join(
            latest_subq,
            (TrendData.stock_id == latest_subq.c.stock_id)
            & (TrendData.fetched_at == latest_subq.c.max_fetched_at),
        )
        .join(Stock, Stock.id == TrendData.stock_id)
        .where(Stock.is_active.is_(True))
    )
    latest_result = await session.execute(latest_trends_stmt)
    latest_rows = latest_result.all()

    if not latest_rows:
        return []

    # For each stock, find the trend score from ~24h ago
    trending_stocks: list[TrendingStock] = []

    for trend_data, stock in latest_rows:
        # Find older trend data for this stock (closest to 24h ago)
        older_stmt = (
            select(TrendData.trend_score)
            .where(TrendData.stock_id == trend_data.stock_id)
            .where(TrendData.fetched_at <= twenty_four_hours_ago)
            .order_by(desc(TrendData.fetched_at))
            .limit(1)
        )
        older_result = await session.execute(older_stmt)
        older_score = older_result.scalar_one_or_none()

        # If no older data, treat previous score as 0
        previous_score = older_score if older_score is not None else 0
        delta = trend_data.trend_score - previous_score

        sector_name = stock.sector.name if stock.sector else "Unknown"

        trending_stocks.append(
            TrendingStock(
                id=stock.id,
                bse_code=stock.bse_code,
                ticker_symbol=stock.ticker_symbol,
                company_name=stock.company_name,
                sector_name=sector_name,
                current_price=stock.current_price,
                day_change_pct=stock.day_change_pct,
                trend_score_current=trend_data.trend_score,
                trend_score_delta=delta,
            )
        )

    # Sort by trend_score_delta descending and return top 5
    trending_stocks.sort(key=lambda s: s.trend_score_delta, reverse=True)
    return trending_stocks[:5]


# --- Endpoints ---


@router.get(
    "/",
    response_model=DashboardResponse,
    status_code=status.HTTP_200_OK,
    summary="Get full dashboard data",
)
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> DashboardResponse:
    """Get full dashboard data for the authenticated user.

    Includes portfolio summary, top gainers/losers from watchlist,
    active alerts, Sensex value, and top 5 trending stocks.
    Uses Redis caching with 60-second TTL for performance.
    """
    user_id = current_user["id"]

    # Check cache first
    cached = await get_dashboard_data(user_id)
    if cached is not None:
        return DashboardResponse(**cached)

    # Assemble dashboard data
    portfolio_service = PortfolioService()
    stock_service = StockService(session=db)

    # Portfolio summary
    try:
        portfolio_summary = await portfolio_service.get_summary(db, user_id)
    except Exception:
        portfolio_summary = None

    # Top gainers/losers from watchlist
    top_gainers, top_losers = await _get_top_gainers_losers_from_watchlist(db, user_id)

    # Active alerts
    active_alerts = await _get_active_alerts(db, user_id)

    # Market summary (Sensex)
    market = await stock_service.get_market_summary()

    # Trending stocks (top 5 by 24h trend_score_delta)
    trending_stocks = await _get_trending_stocks(db)

    now = datetime.now(timezone.utc)

    response = DashboardResponse(
        portfolio_summary=portfolio_summary,
        top_gainers=top_gainers,
        top_losers=top_losers,
        active_alerts=active_alerts,
        sensex_value=market.sensex_value,
        sensex_change_pct=market.sensex_change_pct,
        trending_stocks=trending_stocks,
        is_market_open=market.is_market_open,
        last_updated=now,
    )

    # Cache dashboard data with 60s TTL
    await set_dashboard_data(user_id, response.model_dump(mode="json"))

    return response


@router.get(
    "/market",
    response_model=MarketOverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Get market overview (Sensex, top movers)",
)
async def get_market_overview(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> MarketOverviewResponse:
    """Get market overview including BSE Sensex value and top movers.

    Returns the current Sensex approximation, daily change percentage,
    top gainers, top losers, and market open status.
    """
    stock_service = StockService(session=db)
    market = await stock_service.get_market_summary()

    return MarketOverviewResponse(
        sensex_value=market.sensex_value,
        sensex_change_pct=market.sensex_change_pct,
        top_gainers=market.top_gainers,
        top_losers=market.top_losers,
        is_market_open=market.is_market_open,
        last_updated=market.last_updated,
    )


@router.get(
    "/trending",
    response_model=TrendingResponse,
    status_code=status.HTTP_200_OK,
    summary="Get top trending stocks",
)
async def get_trending_stocks(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> TrendingResponse:
    """Get top 5 stocks by trend score change in the last 24 hours.

    Returns stocks sorted by trend_score_delta descending,
    limited to at most 5 entries.
    """
    trending = await _get_trending_stocks(db)

    return TrendingResponse(
        trending_stocks=trending,
        last_updated=datetime.now(timezone.utc),
    )
