"""SignalAlert ORM model (Step 5 / G5 system-generated alerts).

A SEPARATE, additive subsystem from the EXISTING user-configured ``alerts``
table (``backend.app.models.alert.Alert`` — per-stock price/trend threshold
alerts a user sets up). This table holds SYSTEM-generated insights derived from
the nightly pipeline output:

  * ``AI``   — a quant pattern worth surfacing (an actionable BUY/SELL signal
               with sufficient confidence);
  * ``RISK`` — elevated portfolio volatility on a held ticker;
  * ``NEWS`` — a fresh headline from the ``news_items`` ingest.

``ticker`` is nullable so a market-wide alert (e.g. a macro news item with no
resolved symbol) can be stored. ``dedup_hash`` is the unique identity so
re-running the generator for a day never duplicates the same alert — the
repository keys its ``ON CONFLICT (dedup_hash) DO NOTHING`` on it (mirrors the
``news_items`` dedup pattern).
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database import Base


class SignalAlert(Base):
    __tablename__ = "signal_alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)  # NULL = market-wide
    alert_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # AI / RISK / NEWS
    severity: Mapped[str] = mapped_column(
        String(8), nullable=False, default="info"
    )  # info / warning / critical
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    dedup_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("dedup_hash", name="uq_signal_alerts_dedup"),
        Index("idx_signal_alerts_ticker_asof", "ticker", "as_of"),
        Index("idx_signal_alerts_kind_asof", "alert_kind", "as_of"),
    )
