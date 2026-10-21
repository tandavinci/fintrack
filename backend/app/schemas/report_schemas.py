"""Pydantic schemas for Reports, Insights, and Education endpoints."""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ─── Insight Schemas ───────────────────────────────────────────────────────────

# Disclaimer constant for inclusion in insight API responses
INSIGHT_DISCLAIMER: str = (
    "These insights are informational only and do not constitute financial advice. "
    "Investment decisions should be made based on your own research and risk tolerance. "
    "Past performance and trend data do not guarantee future results."
)


class InsightSignal(str, Enum):
    """Classification of the insight score into actionable signals."""

    OPPORTUNITY = "OPPORTUNITY"
    CAUTION = "CAUTION"
    NEUTRAL = "NEUTRAL"


class InsightScoreResponse(BaseModel):
    """Schema for insight score data returned from the API."""

    id: int
    stock_id: int
    historical_score: Decimal
    trend_momentum: Decimal
    combined_score: Decimal
    signal: InsightSignal
    computed_at: datetime
    disclaimer: str = Field(default=INSIGHT_DISCLAIMER, description="Informational disclaimer")

    model_config = {"from_attributes": True}


class InsightCompareRequest(BaseModel):
    """Schema for comparing insight scores across multiple stocks."""

    stock_ids: list[int] = Field(
        ..., min_length=2, max_length=5, description="Stock IDs to compare"
    )


class InsightCompareResponse(BaseModel):
    """Schema for the comparison result of multiple stocks."""

    insights: list[InsightScoreResponse]


class TopOpportunityResponse(BaseModel):
    """Schema for top investment opportunity listings."""

    stock_id: int
    stock_name: str
    ticker_symbol: str
    sector: str
    combined_score: Decimal
    signal: InsightSignal
    computed_at: datetime

    model_config = {"from_attributes": True}


# ─── Education Schemas ─────────────────────────────────────────────────────────


class DifficultyLevel(str, Enum):
    """Education module difficulty levels."""

    BEGINNER = "BEGINNER"
    INTERMEDIATE = "INTERMEDIATE"
    ADVANCED = "ADVANCED"


class EducationModuleResponse(BaseModel):
    """Schema for education module data returned from the API."""

    id: int
    title: str
    content: str
    difficulty: DifficultyLevel
    category: str
    sector_relevance: Optional[str] = None
    order_index: int
    created_at: datetime

    model_config = {"from_attributes": True}


class EducationModuleListResponse(BaseModel):
    """Schema for a list of education modules."""

    modules: list[EducationModuleResponse]
    total: int


class EducationProgressResponse(BaseModel):
    """Schema for user education progress."""

    id: int
    user_id: int
    module_id: int
    is_completed: bool
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class UserEducationSummary(BaseModel):
    """Schema summarizing a user's overall education progress."""

    total_modules: int
    completed_modules: int
    progress_pct: float = Field(..., ge=0, le=100)
    completed: list[EducationProgressResponse]


class GlossaryTerm(BaseModel):
    """Schema for a stock market glossary term."""

    term: str
    definition: str


# ─── Report Schemas ────────────────────────────────────────────────────────────


class ReportFormat(str, Enum):
    """Supported export formats."""

    PDF = "PDF"
    CSV = "CSV"


class GainsCategory(str, Enum):
    """Capital gains holding period classification per Indian tax rules."""

    SHORT_TERM = "SHORT_TERM"
    LONG_TERM = "LONG_TERM"


class DateRange(BaseModel):
    """Schema for specifying a date range in report requests."""

    start_date: date
    end_date: date


class ReportParams(BaseModel):
    """Schema for portfolio report generation parameters."""

    format: ReportFormat = ReportFormat.PDF
    date_range: Optional[DateRange] = None


class CapitalGainsParams(BaseModel):
    """Schema for capital gains report parameters."""

    format: ReportFormat = ReportFormat.PDF
    date_range: DateRange


class HistoryExportParams(BaseModel):
    """Schema for historical data CSV export."""

    stock_id: int
    start_date: date
    end_date: date


class HoldingReportEntry(BaseModel):
    """A single holding entry in a portfolio report."""

    stock_id: int
    stock_name: str
    ticker_symbol: str
    purchase_date: date
    purchase_price: Decimal
    quantity: int
    current_price: Decimal
    profit_loss: Decimal
    pct_return: float


class CapitalGainsEntry(BaseModel):
    """A single holding entry in a capital gains report."""

    stock_id: int
    stock_name: str
    ticker_symbol: str
    purchase_date: date
    purchase_price: Decimal
    quantity: int
    current_price: Decimal
    profit_loss: Decimal
    category: GainsCategory
    holding_days: int


class ReportStatus(str, Enum):
    """Report generation status."""

    PENDING = "PENDING"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ReportResponse(BaseModel):
    """Schema for a generated report metadata."""

    report_id: str
    status: ReportStatus
    format: ReportFormat
    created_at: datetime
    download_url: Optional[str] = None


class PortfolioReportResponse(BaseModel):
    """Schema for portfolio report content."""

    report_id: str
    holdings: list[HoldingReportEntry]
    total_invested: Decimal
    current_value: Decimal
    total_profit_loss: Decimal
    pct_return: float
    generated_at: datetime


class CapitalGainsReportResponse(BaseModel):
    """Schema for capital gains report content."""

    report_id: str
    entries: list[CapitalGainsEntry]
    short_term_gains: Decimal
    long_term_gains: Decimal
    total_gains: Decimal
    date_range: DateRange
    generated_at: datetime
