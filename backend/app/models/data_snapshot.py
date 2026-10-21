"""DataSnapshot ORM model (design §7.1).

A persisted point-in-time snapshot keyed by the EXISTING ``compute_snapshot_id``.
``payload`` (JSONB) holds the exact OHLCV+indicator tail, indicator summary, and
headlines given to an engine; built by the FEAT-C ``build_and_store_snapshot``
service. The snapshot payload is the source of truth for numbers (models explain,
code computes).
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database import Base


class DataSnapshot(Base):
    __tablename__ = "data_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("idx_snapshots_ticker_asof", "ticker", "as_of"),
    )
