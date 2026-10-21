"""Insight score model for stock analysis signals."""

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base


class InsightSignalEnum(str, enum.Enum):
    """Classification of insight scores into actionable signals."""

    OPPORTUNITY = "OPPORTUNITY"
    CAUTION = "CAUTION"
    NEUTRAL = "NEUTRAL"


class InsightScore(Base):
    __tablename__ = "insight_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(Integer, ForeignKey("stocks.id"), nullable=False)
    historical_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    trend_momentum: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    combined_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    signal: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # OPPORTUNITY, CAUTION, NEUTRAL
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    stock = relationship("Stock", back_populates="insight_scores", lazy="selectin")
