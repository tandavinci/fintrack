"""Stock router with endpoints for search, detail, history, trends, sectors, and market summary."""

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.dependencies import get_db
from backend.app.schemas.stock_schemas import (
    MarketSummary,
    PriceRecord,
    SectorResponse,
    StockDetail,
    StockFilter,
    StockSummary,
    TimePeriod,
    TrendDataSchema,
)
from backend.app.services.stock_service import StockService

router = APIRouter(prefix="/api/v1/stocks", tags=["stocks"])


@router.get(
    "/sectors",
    response_model=list[SectorResponse],
    status_code=status.HTTP_200_OK,
    summary="List all sectors",
)
async def list_sectors(
    db: AsyncSession = Depends(get_db),
) -> list[SectorResponse]:
    """List all available stock sectors.

    Returns all sectors with their names and descriptions.
    """
    service = StockService(session=db)
    sectors = await service._repository.get_sectors()
    return [
        SectorResponse(id=s.id, name=s.name, description=s.description)
        for s in sectors
    ]


@router.get(
    "/market-summary",
    response_model=MarketSummary,
    status_code=status.HTTP_200_OK,
    summary="Get BSE Sensex and market overview",
)
async def market_summary(
    db: AsyncSession = Depends(get_db),
) -> MarketSummary:
    """Get BSE market summary including Sensex value, daily change, and top movers.

    Returns the current Sensex approximation, top gainers, top losers,
    and whether the market is currently open.
    """
    service = StockService(session=db)
    return await service.get_market_summary()


@router.get(
    "/search",
    response_model=list[StockSummary],
    status_code=status.HTTP_200_OK,
    summary="Search stocks with text and filters",
)
async def search_stocks(
    q: Optional[str] = Query(
        default=None,
        description="Search term to match against name, ticker, or sector",
    ),
    sector_id: Optional[int] = Query(
        default=None,
        description="Filter by sector ID",
    ),
    market_cap_min: Optional[Decimal] = Query(
        default=None,
        description="Minimum market capitalization",
    ),
    market_cap_max: Optional[Decimal] = Query(
        default=None,
        description="Maximum market capitalization",
    ),
    price_min: Optional[Decimal] = Query(
        default=None,
        description="Minimum current price",
    ),
    price_max: Optional[Decimal] = Query(
        default=None,
        description="Maximum current price",
    ),
    db: AsyncSession = Depends(get_db),
) -> list[StockSummary]:
    """Search and filter BSE-listed stocks.

    Supports text search by name, ticker symbol, or sector, combined with
    optional filters for sector, market cap range, and price range.
    Returns results within 2 seconds for standard datasets.
    """
    filters = StockFilter(
        query=q,
        sector_id=sector_id,
        market_cap_min=market_cap_min,
        market_cap_max=market_cap_max,
        price_min=price_min,
        price_max=price_max,
    )

    service = StockService(session=db)
    return await service.search(query=q, filters=filters)


@router.get(
    "/{stock_id}",
    response_model=StockDetail,
    status_code=status.HTTP_200_OK,
    summary="Get stock detail with current price",
)
async def get_stock_detail(
    stock_id: int,
    db: AsyncSession = Depends(get_db),
) -> StockDetail:
    """Get full stock information including price, volume, 52-week range, and market cap.

    Returns 404 if the stock is not found.
    """
    service = StockService(session=db)
    detail = await service.get_detail(stock_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "STOCK_NOT_FOUND", "message": f"Stock with id {stock_id} not found"},
        )
    return detail


@router.get(
    "/{stock_id}/history",
    response_model=list[PriceRecord],
    status_code=status.HTTP_200_OK,
    summary="Get historical price data",
)
async def get_stock_history(
    stock_id: int,
    period: TimePeriod = Query(
        default=TimePeriod.ONE_YEAR,
        description="Time period for historical data (1m, 3m, 6m, 1y, 5y)",
    ),
    db: AsyncSession = Depends(get_db),
) -> list[PriceRecord]:
    """Get historical price data for a stock over a given time period.

    Returns daily price records (open, high, low, close, volume) ordered
    by date descending for the selected period.
    """
    service = StockService(session=db)
    return await service.get_history(stock_id=stock_id, period=period)


@router.get(
    "/{stock_id}/trends",
    response_model=Optional[TrendDataSchema],
    status_code=status.HTTP_200_OK,
    summary="Get Google Trends data for stock",
)
async def get_stock_trends(
    stock_id: int,
    db: AsyncSession = Depends(get_db),
) -> Optional[TrendDataSchema]:
    """Get the latest Google Trends data for a stock.

    Returns the most recent trend score (0-100) representing public interest.
    Returns null if no trend data is available for the stock.
    """
    service = StockService(session=db)
    return await service.get_trend_score(stock_id)
