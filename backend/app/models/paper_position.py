"""PaperPosition ORM model (Step 6 / G6 deliverable 4 — paper trading).

One persisted row per PAPER position opened from a stored ``Recommendation``.
"No order execution, paper positions only" (STEERING rule 9): nothing here
places a real order; a position is a bookkeeping record of "if we had acted on
this recommendation with a fixed notional, here is the entry, the latest mark,
and the P&L".

DUAL BOOK (option B): the ``source`` column ("quant" / "deep") keeps a SEPARATE
paper book per engine, so the evaluation can compare the quant book, the deep
book, and a buy-and-hold benchmark side by side. On modest hardware the deep
book fills slowly (the deep lane is the performance blocker); the mechanism is
identical for both sources, so a capable machine populates the deep book without
any schema change.

Keyed UNIQUELY on ``(ticker, source, opened_as_of)`` so re-running a day's
mark-to-market UPSERTS the same position in place rather than duplicating it.
A later flip to SELL (or the horizon elapsing) CLOSES the position, realizing
the P&L; marks on an open position only update the unrealized fields.

Prices are point-in-time: the ENTRY price is the close the opening
recommendation saw at its ``opened_as_of`` (same data path the engine used), and
each mark is the latest close. All monetary/ratio fields are
``Float(precision=53)`` (DOUBLE PRECISION) to match the project's money
convention (see ``recommendation.confidence``); there is no real cash, so a
fixed NOTIONAL per position makes the P&L a pure return figure.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database import Base

# Fixed paper notional per position (INR). Deterministic: there is no real cash
# balance, so every position is sized identically and P&L is a pure return.
PAPER_NOTIONAL = 10_000.0


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    # Which engine's recommendations drive this book: "quant" / "deep".
    source: Mapped[str] = mapped_column(String(16), nullable=False)

    # The as_of of the recommendation that OPENED this position (point-in-time).
    opened_as_of: Mapped[date] = mapped_column(Date, nullable=False)
    # Entry price = the close the opening recommendation saw at opened_as_of.
    entry_price: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    # Fixed paper notional at entry; quantity is notional / entry_price.
    notional: Mapped[float] = mapped_column(
        Float(precision=53), nullable=False, default=PAPER_NOTIONAL
    )
    quantity: Mapped[float] = mapped_column(Float(precision=53), nullable=False)

    # "open" | "closed".
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="open")

    # --- mark-to-market (updated while open) ---------------------------------
    last_mark_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_mark_price: Mapped[float | None] = mapped_column(
        Float(precision=53), nullable=True
    )
    # Unrealized return as a FRACTION ((mark/entry) - 1); null before first mark.
    unrealized_pnl_pct: Mapped[float | None] = mapped_column(
        Float(precision=53), nullable=True
    )

    # --- realized (set when closed) ------------------------------------------
    closed_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float(precision=53), nullable=True)
    # Realized return as a FRACTION ((exit/entry) - 1); null while open.
    realized_pnl_pct: Mapped[float | None] = mapped_column(
        Float(precision=53), nullable=True
    )
    # Why the position closed: "sell_signal" | "horizon" (null while open).
    close_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)

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
        UniqueConstraint(
            "ticker",
            "source",
            "opened_as_of",
            name="uq_paper_positions_ticker_source_opened",
        ),
        Index("idx_paper_positions_source_status", "source", "status"),
        Index("idx_paper_positions_ticker", "ticker"),
    )
