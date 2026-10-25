"""DeepMemoryEntry ORM model (Step 4 / G4 memory-log PostgreSQL mirror).

TradingAgents keeps writing its own markdown memory log (``trading_memory.md``,
the backup export — STEERING rule 2: its source is never edited). This table is
a DURABLE MIRROR of that log: after each ticker's deep run the service copies
that ticker's entries — parsed by TradingAgents' OWN
``TradingMemoryLog.load_entries()`` — into ``deep_memory`` (write-as-you-go, a
few rows per ticker, never batched to the end).

Columns map one-to-one onto the ``load_entries()`` dict shape:
``date``->``trade_date`` (parsed), ``ticker``, ``rating``, ``pending``,
``raw``->``raw_return``, ``alpha``->``alpha_return``, ``holding``->``holding_days``,
``resolved``->``resolved_date`` (parsed, None when absent), ``decision``,
``reflection``. ``raw``/``alpha``/``holding`` are kept as TEXT because the parser
emits display strings (e.g. ``"+12.5%"``, ``"20d"``, ``"n/a"``), not raw numbers.

The ``(ticker, trade_date)`` unique constraint makes a re-mirror an UPSERT, not a
duplicate — the repository keys its ``ON CONFLICT DO UPDATE`` on it, so a pending
entry that later settles updates the SAME row in place (``pending`` flips False,
``resolved_date``/``reflection`` fill in).
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class DeepMemoryEntry(Base):
    __tablename__ = "deep_memory"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    rating: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # True = outcome not yet settled (the entry is still "pending" in the log).
    pending: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # The parser's display strings — kept as TEXT (e.g. "+12.5%", "20d", "n/a").
    raw_return: Mapped[str | None] = mapped_column(String(32), nullable=True)
    alpha_return: Mapped[str | None] = mapped_column(String(32), nullable=True)
    holding_days: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    decision: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reflection: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("ticker", "trade_date", name="uq_deep_memory_ticker_date"),
        Index("idx_deep_memory_ticker_date", "ticker", "trade_date"),
    )
