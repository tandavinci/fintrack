"""Insights router with endpoints for stock insight scores, correlation data, and comparisons."""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.dependencies import get_db
from backend.app.models.insight import InsightScore
from backend.app.models.stock import Stock
from backend.app.schemas.report_schemas import (
    InsightCompareRequest,
    InsightScoreResponse,
    InsightSignal,
    TopOpportunityResponse,
)
from backend.app.services.analysis_engine import (
    CAUTION_THRESHOLD,
    INSIGHT_DISCLAIMER,
    OPPORTUNITY_THRESHOLD,
    AnalysisEngine,
)

router = APIRouter(prefix="/api/v1/insights", tags=["insights"])


# ─── Response Schemas specific to insights endpoints ───────────────────────────


class MethodologyExplanation(BaseModel):
    """Explanation of the methodology used to generate insight scores."""

    historical_weight: float = Field(
        default=0.6,
        description="Weight applied to historical performance score (60%)",
    )
    trend_weight: float = Field(
        default=0.4,
        description="Weight applied to trend momentum score (40%)",
    )
    opportunity_threshold: float = Field(
        default=OPPORTUNITY_THRESHOLD,
        description="Combined score above this value signals an opportunity",
    )
    caution_threshold: float = Field(
        default=CAUTION_THRESHOLD,
        description="Combined score below this value signals caution",
    )
    description: str = Field(
        default=(
            "The combined insight score is calculated as: "
            "historical_score × 0.6 + trend_momentum × 0.4. "
            "Historical score reflects past price performance metrics. "
            "Trend momentum reflects recent public interest via Google Trends data. "
            "Scores above the opportunity threshold indicate potential investment "
            "opportunities, while scores below the caution threshold suggest "
            "increased risk."
        ),
    )


class InsightResponse(BaseModel):
    """Full insight response including score, methodology, and disclaimer."""

    insight: InsightScoreResponse
    methodology: MethodologyExplanation = Field(default_factory=MethodologyExplanation)
    disclaimer: str = Field(default=INSIGHT_DISCLAIMER)


class CorrelationPointResponse(BaseModel):
    """A single data point in the correlation chart."""

    date: date
    close_price: float
    trend_score: Optional[int] = None


class CorrelationResponse(BaseModel):
    """Trend-price correlation chart data for a stock."""

    stock_id: int
    period_start: date
    period_end: date
    data_points: list[CorrelationPointResponse]
    disclaimer: str = Field(default=INSIGHT_DISCLAIMER)


class TopOpportunitiesResponse(BaseModel):
    """Response for top opportunities listing with methodology and disclaimer."""

    opportunities: list[TopOpportunityResponse]
    methodology: MethodologyExplanation = Field(default_factory=MethodologyExplanation)
    disclaimer: str = Field(default=INSIGHT_DISCLAIMER)


class CompareResponse(BaseModel):
    """Response for stock comparison with methodology and disclaimer."""

    insights: list[InsightScoreResponse]
    methodology: MethodologyExplanation = Field(default_factory=MethodologyExplanation)
    disclaimer: str = Field(default=INSIGHT_DISCLAIMER)


# ─── Endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "/top-opportunities",
    response_model=TopOpportunitiesResponse,
    status_code=status.HTTP_200_OK,
    summary="Get top stocks by insight score",
)
async def get_top_opportunities(
    limit: int = Query(default=10, ge=1, le=50, description="Number of results to return"),
    db: AsyncSession = Depends(get_db),
) -> TopOpportunitiesResponse:
    """Get top stocks ranked by combined insight score.

    Returns stocks with the highest combined insight scores, limited to
    those classified as OPPORTUNITY signals. Includes methodology
    explanation and disclaimer.

    Requirements: 7.1, 7.2, 7.3, 7.4
    """
    # Fetch the latest insight score per stock, ordered by combined_score descending
    # Use a subquery to get the most recent insight for each stock

    subquery = (
        select(
            InsightScore.stock_id,
            func.max(InsightScore.computed_at).label("latest_at"),
        )
        .group_by(InsightScore.stock_id)
        .subquery()
    )

    stmt = (
        select(InsightScore, Stock)
        .join(
            subquery,
            (InsightScore.stock_id == subquery.c.stock_id)
            & (InsightScore.computed_at == subquery.c.latest_at),
        )
        .join(Stock, Stock.id == InsightScore.stock_id)
        .where(Stock.is_active == True)  # noqa: E712
        .order_by(InsightScore.combined_score.desc())
        .limit(limit)
    )

    result = await db.execute(stmt)
    rows = result.all()

    opportunities = []
    for insight, stock in rows:
        sector_name = stock.sector.name if stock.sector else "Unknown"
        opportunities.append(
            TopOpportunityResponse(
                stock_id=stock.id,
                stock_name=stock.company_name,
                ticker_symbol=stock.ticker_symbol,
                sector=sector_name,
                combined_score=insight.combined_score,
                signal=InsightSignal(insight.signal),
                computed_at=insight.computed_at,
            )
        )

    return TopOpportunitiesResponse(opportunities=opportunities)


@router.post(
    "/compare",
    response_model=CompareResponse,
    status_code=status.HTTP_200_OK,
    summary="Compare multiple stocks by insight score",
)
async def compare_stocks(
    request: InsightCompareRequest,
    db: AsyncSession = Depends(get_db),
) -> CompareResponse:
    """Compare insight scores across multiple stocks (2-5).

    Returns the most recent insight score for each requested stock,
    along with methodology explanation and disclaimer.

    Requirements: 6.4, 7.1, 7.2, 7.3, 7.4
    """
    insights: list[InsightScoreResponse] = []

    for stock_id in request.stock_ids:
        # Get the most recent insight for each stock
        stmt = (
            select(InsightScore)
            .where(InsightScore.stock_id == stock_id)
            .order_by(InsightScore.computed_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        insight = result.scalar_one_or_none()

        if insight is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "INSIGHT_NOT_FOUND",
                    "message": f"No insight data found for stock_id {stock_id}",
                },
            )

        insights.append(
            InsightScoreResponse(
                id=insight.id,
                stock_id=insight.stock_id,
                historical_score=insight.historical_score,
                trend_momentum=insight.trend_momentum,
                combined_score=insight.combined_score,
                signal=InsightSignal(insight.signal),
                computed_at=insight.computed_at,
            )
        )

    return CompareResponse(insights=insights)


@router.get(
    "/{stock_id}",
    response_model=InsightResponse,
    status_code=status.HTTP_200_OK,
    summary="Get combined insight for a stock",
)
async def get_stock_insight(
    stock_id: int,
    db: AsyncSession = Depends(get_db),
) -> InsightResponse:
    """Get the most recent combined insight score for a stock.

    Returns the insight score with signal classification (OPPORTUNITY,
    CAUTION, or NEUTRAL), methodology explanation showing how the
    score is computed, and a disclaimer.

    Requirements: 7.1, 7.2, 7.3, 7.4
    """
    # Verify stock exists
    stock_stmt = select(Stock).where(Stock.id == stock_id)
    stock_result = await db.execute(stock_stmt)
    stock = stock_result.scalar_one_or_none()

    if stock is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "STOCK_NOT_FOUND",
                "message": f"Stock with id {stock_id} not found",
            },
        )

    # Get the most recent insight for this stock
    stmt = (
        select(InsightScore)
        .where(InsightScore.stock_id == stock_id)
        .order_by(InsightScore.computed_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    insight = result.scalar_one_or_none()

    if insight is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "INSIGHT_NOT_FOUND",
                "message": f"No insight data available for stock_id {stock_id}. Insights are computed daily after market close.",
            },
        )

    insight_response = InsightScoreResponse(
        id=insight.id,
        stock_id=insight.stock_id,
        historical_score=insight.historical_score,
        trend_momentum=insight.trend_momentum,
        combined_score=insight.combined_score,
        signal=InsightSignal(insight.signal),
        computed_at=insight.computed_at,
    )

    return InsightResponse(insight=insight_response)


@router.get(
    "/{stock_id}/correlation",
    response_model=CorrelationResponse,
    status_code=status.HTTP_200_OK,
    summary="Get trend-price correlation chart data",
)
async def get_correlation(
    stock_id: int,
    period_days: int = Query(
        default=90,
        description="Period in days for correlation data (30, 90, 180, or 365)",
    ),
    db: AsyncSession = Depends(get_db),
) -> CorrelationResponse:
    """Get trend-price correlation data for a stock over a selected period.

    Returns a time series of closing prices paired with Google Trends
    scores, suitable for rendering an overlay chart. Supports 30, 90,
    180, and 365-day periods.

    Requirements: 5.4, 6.4, 7.4
    """
    # Validate period
    valid_periods = {30, 90, 180, 365}
    if period_days not in valid_periods:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_PERIOD",
                "message": f"Period must be one of {sorted(valid_periods)} days",
            },
        )

    # Verify stock exists
    stock_stmt = select(Stock).where(Stock.id == stock_id)
    stock_result = await db.execute(stock_stmt)
    stock = stock_result.scalar_one_or_none()

    if stock is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "STOCK_NOT_FOUND",
                "message": f"Stock with id {stock_id} not found",
            },
        )

    engine = AnalysisEngine(session=db)
    correlation_data = await engine.get_correlation_data(
        stock_id=stock_id, period_days=period_days
    )

    data_points = [
        CorrelationPointResponse(
            date=dp.date,
            close_price=dp.close_price,
            trend_score=dp.trend_score,
        )
        for dp in correlation_data.data_points
    ]

    return CorrelationResponse(
        stock_id=correlation_data.stock_id,
        period_start=correlation_data.period_start,
        period_end=correlation_data.period_end,
        data_points=data_points,
    )
