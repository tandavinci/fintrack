"""PriceBar ORM model (design §5.1).

One row per OHLCV bar per (ticker, interval, ts). OHLCV values are stored as
IEEE-754 binary64 (Postgres DOUBLE PRECISION, SQLAlchemy ``Float(precision=53)``)
so a write->read round-trip is bit-identical to the pandas float64 cell the data
service fetched (design §4.4b, G2 determinism). ``volume`` widens to BigInteger
because BSE large-cap daily volumes exceed 32-bit int.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database import Base


class PriceBar(Base):
    __tablename__ = "price_bars"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)  # resolved symbol, e.g. RELIANCE.BO
    interval: Mapped[str] = mapped_column(String(8), nullable=False)  # 1d, 30m, 5m ...
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )  # bar timestamp (tz-naive, matches quant frames)
    # OHLCV stored as IEEE-754 binary64 (DOUBLE PRECISION) — bit-identical to the
    # pandas float64 frame on a write->read round-trip (§4.4b, G2).
    open: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    high: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    low: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    close: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="yfinance")
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("ticker", "interval", "ts", name="uq_price_bars_tkr_interval_ts"),
        Index("idx_price_bars_lookup", "ticker", "interval", "ts"),
    )
