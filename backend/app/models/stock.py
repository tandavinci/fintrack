"""Stock, Sector, PriceHistory, and TrendData models."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base


class Sector(Base):
    __tablename__ = "sectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Relationships
    stocks = relationship("Stock", back_populates="sector", lazy="selectin")


class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bse_code: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    ticker_symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sector_id: Mapped[int] = mapped_column(Integer, ForeignKey("sectors.id"), nullable=False)
    market_cap: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=True)
    current_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=True)
    day_change_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=True)
    week_52_high: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=True)
    week_52_low: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=True)
    volume: Mapped[int] = mapped_column(Integer, nullable=True, default=0)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    sector = relationship("Sector", back_populates="stocks", lazy="selectin")
    price_history = relationship("PriceHistory", back_populates="stock", lazy="selectin")
    trend_data = relationship("TrendData", back_populates="stock", lazy="selectin")
    insight_scores = relationship("InsightScore", back_populates="stock", lazy="selectin")

    __table_args__ = (
        Index("idx_stocks_ticker", "ticker_symbol"),
        Index(
            "idx_stocks_name_trgm",
            "company_name",
            postgresql_using="gin",
            postgresql_ops={"company_name": "gin_trgm_ops"},
        ),
        Index("idx_stocks_sector", "sector_id"),
    )


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(Integer, ForeignKey("stocks.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    open_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    high_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    low_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    close_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    stock = relationship("Stock", back_populates="price_history", lazy="selectin")

    __table_args__ = (
        Index("idx_price_stock_date", "stock_id", "date"),
    )


class TrendData(Base):
    __tablename__ = "trend_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(Integer, ForeignKey("stocks.id"), nullable=False)
    trend_score: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    # Relationships
    stock = relationship("Stock", back_populates="trend_data", lazy="selectin")

    __table_args__ = (
        Index("idx_trend_stock_fetched", "stock_id", "fetched_at"),
    )
