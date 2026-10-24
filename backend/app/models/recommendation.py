"""RecommendationRecord ORM model (Step 3 / G3 persistence backbone).

One persisted row per engine recommendation. The ORM class is deliberately
named ``RecommendationRecord`` (NOT ``Recommendation``) so it never clashes with
the single Pydantic contract ``backend.app.schemas.recommendation.Recommendation``
— that contract remains the one shape every engine returns; this table is just
its durable projection.

Columns mirror the contract fields one-to-one. ``confidence`` is stored as
DOUBLE PRECISION (``Float(precision=53)``) to round-trip the contract's float.
``evidence`` (list[str]) lands in JSONB. ``snapshot_id`` is a FK into
``data_snapshots`` so every stored recommendation is traceable to the exact rows
that justified it (models explain, code computes).

The ``(ticker, as_of, source)`` unique constraint makes re-running a day an
UPSERT, not a duplicate — the repository keys its ``ON CONFLICT DO UPDATE`` on
it. If the quant signal and the deep decision disagree they differ by
``source`` and so coexist as two rows (store both, never average).
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database import Base


class RecommendationRecord(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    action: Mapped[str] = mapped_column(String(4), nullable=False)  # BUY / HOLD / SELL
    # DOUBLE PRECISION (binary64) so the contract's float round-trips exactly.
    confidence: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False)  # list[str]
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # quant / deep / advisor
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # None for rule-based quant
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    snapshot_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("data_snapshots.snapshot_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "ticker", "as_of", "source", name="uq_recommendations_ticker_asof_source"
        ),
        Index("idx_recommendations_ticker_asof", "ticker", "as_of"),
    )
