"""Alert model for price and trend notifications."""

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base


class AlertTypeEnum(str, enum.Enum):
    """Supported alert trigger types."""

    PRICE_ABOVE = "PRICE_ABOVE"
    PRICE_BELOW = "PRICE_BELOW"
    TREND_ABOVE = "TREND_ABOVE"
    TREND_BELOW = "TREND_BELOW"


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    stock_id: Mapped[int] = mapped_column(Integer, ForeignKey("stocks.id"), nullable=False)
    alert_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # PRICE_ABOVE, PRICE_BELOW, TREND_ABOVE, TREND_BELOW
    threshold_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    user = relationship("User", back_populates="alerts", lazy="selectin")
    stock = relationship("Stock", lazy="selectin")

    __table_args__ = (
        Index("idx_alerts_user_active", "user_id", "is_active"),
    )
