"""Analysis Engine service for computing stock insights and technical indicators."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.insight import InsightScore, InsightSignalEnum
from backend.app.models.stock import PriceHistory, Sector, Stock, TrendData

# ─── Insight Signal Thresholds ─────────────────────────────────────────────────
OPPORTUNITY_THRESHOLD: float = 70.0
CAUTION_THRESHOLD: float = 30.0

# ─── Disclaimer ────────────────────────────────────────────────────────────────
INSIGHT_DISCLAIMER: str = (
    "These insights are informational only and do not constitute financial advice. "
    "Investment decisions should be made based on your own research and risk tolerance. "
    "Past performance and trend data do not guarantee future results."
)


@dataclass
class MovingAverages:
    """Moving average values for a stock."""

    stock_id: int
    ma_20: Optional[float] = None
    ma_50: Optional[float] = None
    ma_200: Optional[float] = None


@dataclass
class SupportResistanceLevel:
    """A single support or resistance level."""

    price: float
    level_type: str  # "support" or "resistance"
    strength: int  # number of touches


@dataclass
class SupportResistanceLevels:
    """Collection of support and resistance levels for a stock."""

    stock_id: int
    support_levels: List[SupportResistanceLevel]
    resistance_levels: List[SupportResistanceLevel]


@dataclass
class CorrelationDataPoint:
    """A single data point correlating trend score with price."""

    date: date
    close_price: float
    trend_score: Optional[int] = None


@dataclass
class CorrelationData:
    """Trend-price correlation data for a stock over a period."""

    stock_id: int
    period_start: date
    period_end: date
    data_points: List[CorrelationDataPoint]


@dataclass
class SectorTrend:
    """Aggregated trend data for a sector."""

    sector_name: str
    sector_id: int
    average_trend_score: float
    stock_count: int


class AnalysisEngine:
    """Service for computing stock analysis, insights, and technical indicators.

    Uses async SQLAlchemy queries against PriceHistory, TrendData, Stock, and Sector
    models to provide moving averages, support/resistance levels, combined insight
    scores, correlation data, and sector-level trend aggregation.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def compute_moving_averages(self, stock_id: int) -> MovingAverages:
        """Calculate 20-day, 50-day, and 200-day moving averages from price history.

        The N-day moving average at the most recent date equals the arithmetic mean
        of the last N closing prices ordered by date descending.

        Validates: Requirements 6.3
        """
        # Fetch last 200 trading days of price history (enough for all MAs)
        stmt = (
            select(PriceHistory.close_price)
            .where(PriceHistory.stock_id == stock_id)
            .order_by(PriceHistory.date.desc())
            .limit(200)
        )
        result = await self._session.execute(stmt)
        prices = [float(row[0]) for row in result.all()]

        ma_20: Optional[float] = None
        ma_50: Optional[float] = None
        ma_200: Optional[float] = None

        if len(prices) >= 20:
            ma_20 = sum(prices[:20]) / 20
        if len(prices) >= 50:
            ma_50 = sum(prices[:50]) / 50
        if len(prices) >= 200:
            ma_200 = sum(prices[:200]) / 200

        return MovingAverages(
            stock_id=stock_id,
            ma_20=ma_20,
            ma_50=ma_50,
            ma_200=ma_200,
        )

    async def identify_support_resistance(
        self, stock_id: int, lookback_days: int = 365
    ) -> SupportResistanceLevels:
        """Detect support and resistance levels from historical price data.

        Uses local minima/maxima detection on daily prices. A price level is
        considered significant if it has been touched (within a tolerance of 1.5%)
        at least twice.

        Validates: Requirements 6.5
        """
        cutoff_date = date.today() - timedelta(days=lookback_days)
        stmt = (
            select(PriceHistory.high_price, PriceHistory.low_price, PriceHistory.close_price)
            .where(
                PriceHistory.stock_id == stock_id,
                PriceHistory.date >= cutoff_date,
            )
            .order_by(PriceHistory.date.asc())
        )
        result = await self._session.execute(stmt)
        rows = result.all()

        if len(rows) < 5:
            return SupportResistanceLevels(
                stock_id=stock_id, support_levels=[], resistance_levels=[]
            )

        highs = [float(row[0]) for row in rows]
        lows = [float(row[1]) for row in rows]

        # Find local maxima (resistance candidates) and local minima (support candidates)
        resistance_candidates: List[float] = []
        support_candidates: List[float] = []

        for i in range(2, len(highs) - 2):
            # Local maximum: higher than 2 neighbors on each side
            if highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and \
               highs[i] > highs[i + 1] and highs[i] > highs[i + 2]:
                resistance_candidates.append(highs[i])

            # Local minimum: lower than 2 neighbors on each side
            if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and \
               lows[i] < lows[i + 1] and lows[i] < lows[i + 2]:
                support_candidates.append(lows[i])

        # Cluster nearby levels (within 1.5% tolerance) and count touches
        tolerance = 0.015

        support_levels = self._cluster_levels(support_candidates, tolerance, "support")
        resistance_levels = self._cluster_levels(resistance_candidates, tolerance, "resistance")

        return SupportResistanceLevels(
            stock_id=stock_id,
            support_levels=support_levels,
            resistance_levels=resistance_levels,
        )

    def _cluster_levels(
        self, candidates: List[float], tolerance: float, level_type: str
    ) -> List[SupportResistanceLevel]:
        """Cluster price levels within tolerance and return significant ones."""
        if not candidates:
            return []

        candidates_sorted = sorted(candidates)
        clusters: List[List[float]] = []
        current_cluster: List[float] = [candidates_sorted[0]]

        for price in candidates_sorted[1:]:
            cluster_center = sum(current_cluster) / len(current_cluster)
            if abs(price - cluster_center) / cluster_center <= tolerance:
                current_cluster.append(price)
            else:
                clusters.append(current_cluster)
                current_cluster = [price]
        clusters.append(current_cluster)

        # Only include levels with at least 2 touches (significant)
        levels = []
        for cluster in clusters:
            if len(cluster) >= 2:
                avg_price = sum(cluster) / len(cluster)
                levels.append(
                    SupportResistanceLevel(
                        price=round(avg_price, 2),
                        level_type=level_type,
                        strength=len(cluster),
                    )
                )

        # Sort by strength descending, return top levels
        levels.sort(key=lambda x: x.strength, reverse=True)
        return levels[:5]

    async def compute_combined_score(
        self, historical_score: float, trend_momentum: float
    ) -> float:
        """Compute combined insight score: historical_score × 0.6 + trend_momentum × 0.4.

        Validates: Requirements 7.1, 7.2
        """
        return historical_score * 0.6 + trend_momentum * 0.4

    async def classify_and_store_insight(
        self, stock_id: int, historical_score: float, trend_momentum: float
    ) -> InsightScore:
        """Compute combined score, classify signal, store InsightScore, and return it.

        Signal classification:
        - OPPORTUNITY when combined_score > OPPORTUNITY_THRESHOLD (70)
        - CAUTION when combined_score < CAUTION_THRESHOLD (30)
        - NEUTRAL otherwise

        Validates: Requirements 7.1, 7.2, 7.3, 7.4
        """
        combined_score = await self.compute_combined_score(historical_score, trend_momentum)

        # Determine signal based on thresholds
        if combined_score > OPPORTUNITY_THRESHOLD:
            signal = InsightSignalEnum.OPPORTUNITY
        elif combined_score < CAUTION_THRESHOLD:
            signal = InsightSignalEnum.CAUTION
        else:
            signal = InsightSignalEnum.NEUTRAL

        # Create and persist InsightScore record
        insight = InsightScore(
            stock_id=stock_id,
            historical_score=Decimal(str(round(historical_score, 2))),
            trend_momentum=Decimal(str(round(trend_momentum, 2))),
            combined_score=Decimal(str(round(combined_score, 2))),
            signal=signal.value,
        )

        self._session.add(insight)
        await self._session.flush()

        return insight

    async def get_correlation_data(
        self, stock_id: int, period_days: int = 90
    ) -> CorrelationData:
        """Overlay trend scores with price data for selected periods.

        Returns a time series of closing prices paired with trend scores for
        the given period. Supports 30, 90, 180, and 365-day periods.

        Validates: Requirements 5.4
        """
        end_date = date.today()
        start_date = end_date - timedelta(days=period_days)

        # Fetch price history for the period
        price_stmt = (
            select(PriceHistory.date, PriceHistory.close_price)
            .where(
                PriceHistory.stock_id == stock_id,
                PriceHistory.date >= start_date,
                PriceHistory.date <= end_date,
            )
            .order_by(PriceHistory.date.asc())
        )
        price_result = await self._session.execute(price_stmt)
        price_rows = price_result.all()

        # Fetch trend data for the period
        trend_stmt = (
            select(TrendData.period_start, TrendData.period_end, TrendData.trend_score)
            .where(
                TrendData.stock_id == stock_id,
                TrendData.period_start >= start_date,
                TrendData.period_end <= end_date,
            )
            .order_by(TrendData.period_start.asc())
        )
        trend_result = await self._session.execute(trend_stmt)
        trend_rows = trend_result.all()

        # Build a date-to-trend_score mapping (trend data covers a range)
        trend_by_date: dict[date, int] = {}
        for trend_start, trend_end, score in trend_rows:
            current = trend_start
            while current <= trend_end:
                trend_by_date[current] = score
                current += timedelta(days=1)

        # Merge price and trend data
        data_points: List[CorrelationDataPoint] = []
        for price_date, close_price in price_rows:
            data_points.append(
                CorrelationDataPoint(
                    date=price_date,
                    close_price=float(close_price),
                    trend_score=trend_by_date.get(price_date),
                )
            )

        return CorrelationData(
            stock_id=stock_id,
            period_start=start_date,
            period_end=end_date,
            data_points=data_points,
        )

    async def get_sector_trend(self, sector_name: str) -> SectorTrend:
        """Aggregate individual stock trends to sector level using arithmetic mean.

        For a sector containing N stocks with individual trend scores, the
        sector-level Trend_Score equals the arithmetic mean of the most recent
        individual stock trend scores within that sector.

        Validates: Requirements 5.6
        """
        # Get the sector
        sector_stmt = select(Sector).where(Sector.name == sector_name)
        sector_result = await self._session.execute(sector_stmt)
        sector = sector_result.scalar_one_or_none()

        if sector is None:
            return SectorTrend(
                sector_name=sector_name,
                sector_id=0,
                average_trend_score=0.0,
                stock_count=0,
            )

        # Get all active stocks in this sector
        stocks_stmt = select(Stock.id).where(
            Stock.sector_id == sector.id,
            Stock.is_active == True,  # noqa: E712
        )
        stocks_result = await self._session.execute(stocks_stmt)
        stock_ids = [row[0] for row in stocks_result.all()]

        if not stock_ids:
            return SectorTrend(
                sector_name=sector_name,
                sector_id=sector.id,
                average_trend_score=0.0,
                stock_count=0,
            )

        # Get the most recent trend score for each stock using a subquery
        # For each stock, pick the TrendData with the latest fetched_at
        latest_scores: List[float] = []
        for sid in stock_ids:
            trend_stmt = (
                select(TrendData.trend_score)
                .where(TrendData.stock_id == sid)
                .order_by(TrendData.fetched_at.desc())
                .limit(1)
            )
            trend_result = await self._session.execute(trend_stmt)
            row = trend_result.scalar_one_or_none()
            if row is not None:
                latest_scores.append(float(row))

        stock_count = len(latest_scores)
        average_score = sum(latest_scores) / stock_count if stock_count > 0 else 0.0

        return SectorTrend(
            sector_name=sector_name,
            sector_id=sector.id,
            average_trend_score=round(average_score, 2),
            stock_count=stock_count,
        )
