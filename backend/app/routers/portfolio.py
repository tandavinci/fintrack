"""Portfolio management router.

Provides endpoints for portfolio summary, holdings CRUD, and watchlist management.
All endpoints require authentication via JWT token.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.dependencies import get_current_user, get_db
from backend.app.schemas.portfolio_schemas import (
    HoldingCreate,
    HoldingResponse,
    HoldingUpdate,
    PortfolioSummary,
    WatchlistItemCreate,
    WatchlistItemResponse,
)
from backend.app.services.portfolio_service import (
    HoldingNotFoundError,
    PortfolioService,
    PortfolioServiceError,
    StockNotFoundError,
    WatchlistItemNotFoundError,
)

router = APIRouter(prefix="/api/v1/portfolio", tags=["portfolio"])

# Service instance shared across endpoints
_portfolio_service = PortfolioService()


def get_portfolio_service() -> PortfolioService:
    """Dependency to provide the PortfolioService instance."""
    return _portfolio_service


# --- Portfolio summary ---


@router.get("/", response_model=PortfolioSummary)
async def get_portfolio_summary(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    service: PortfolioService = Depends(get_portfolio_service),
) -> PortfolioSummary:
    """Get the current user's portfolio summary including metrics.

    Returns portfolio info with total invested, current value, profit/loss,
    percentage return, and holdings count.
    """
    return await service.get_summary(db, current_user["id"])


# --- Holdings ---


@router.get("/holdings", response_model=list[HoldingResponse])
async def list_holdings(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    service: PortfolioService = Depends(get_portfolio_service),
) -> list[HoldingResponse]:
    """List all holdings in the user's portfolio."""
    portfolio = await service.repository.get_portfolio_by_user(db, current_user["id"])
    holdings = await service.repository.get_holdings(db, portfolio.id)
    return [HoldingResponse.model_validate(h) for h in holdings]


@router.post("/holdings", response_model=HoldingResponse, status_code=status.HTTP_201_CREATED)
async def add_holding(
    holding_data: HoldingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    service: PortfolioService = Depends(get_portfolio_service),
) -> HoldingResponse:
    """Add a new holding to the user's portfolio.

    Creates the holding with an initial BUY transaction record.
    """
    try:
        holding = await service.add_holding(db, current_user["id"], holding_data)
        await db.commit()
        await db.refresh(holding)
        return HoldingResponse.model_validate(holding)
    except StockNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.put("/holdings/{holding_id}", response_model=HoldingResponse)
async def update_holding(
    holding_id: int,
    update_data: HoldingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    service: PortfolioService = Depends(get_portfolio_service),
) -> HoldingResponse:
    """Update an existing holding's quantity and/or price.

    Records a new BUY transaction and recalculates the weighted average price.
    """
    try:
        holding = await service.update_holding(db, holding_id, update_data)
        await db.commit()
        await db.refresh(holding)
        return HoldingResponse.model_validate(holding)
    except HoldingNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PortfolioServiceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/holdings/{holding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_holding(
    holding_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    service: PortfolioService = Depends(get_portfolio_service),
) -> None:
    """Remove a holding from the portfolio.

    Records a SELL transaction for audit purposes before deletion.
    """
    try:
        await service.remove_holding(db, holding_id)
        await db.commit()
    except HoldingNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# --- Watchlist ---


@router.get("/watchlist", response_model=list[WatchlistItemResponse])
async def get_watchlist(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    service: PortfolioService = Depends(get_portfolio_service),
) -> list[WatchlistItemResponse]:
    """Get all stocks on the user's watchlist."""
    items = await service.get_watchlist(db, current_user["id"])
    return [WatchlistItemResponse.model_validate(item) for item in items]


@router.post("/watchlist", response_model=WatchlistItemResponse, status_code=status.HTTP_201_CREATED)
async def add_to_watchlist(
    item_data: WatchlistItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    service: PortfolioService = Depends(get_portfolio_service),
) -> WatchlistItemResponse:
    """Add a stock to the user's watchlist.

    Returns the existing item if the stock is already on the watchlist.
    """
    try:
        item = await service.add_to_watchlist(db, current_user["id"], item_data.stock_id)
        await db.commit()
        await db.refresh(item)
        return WatchlistItemResponse.model_validate(item)
    except StockNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.delete("/watchlist/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_watchlist(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    service: PortfolioService = Depends(get_portfolio_service),
) -> None:
    """Remove a stock from the user's watchlist."""
    try:
        await service.remove_from_watchlist(db, item_id)
        await db.commit()
    except WatchlistItemNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
