"""Step-6 paper_positions table (G6 deliverable 4 — paper trading)

Revision ID: 007
Revises: 006
Create Date: 2025-01-07 00:01:00.000000

Creates the ``paper_positions`` table: one row per PAPER position opened from a
stored recommendation ("no order execution, paper positions only" — STEERING
rule 9). The ``source`` column ("quant"/"deep") keeps a SEPARATE paper book per
engine (the dual-book option) so the evaluation can compare quant vs deep vs a
buy-and-hold benchmark. Keyed UNIQUELY on ``(ticker, source, opened_as_of)`` so
a re-run of a day's mark-to-market UPSERTS the position rather than duplicating
it. Monetary/ratio columns are DOUBLE PRECISION (``Float(precision=53)``) to
match the project's money convention. Additive: no existing table is touched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paper_positions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("opened_as_of", sa.Date(), nullable=False),
        sa.Column("entry_price", sa.Float(precision=53), nullable=False),
        sa.Column("notional", sa.Float(precision=53), nullable=False),
        sa.Column("quantity", sa.Float(precision=53), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="open"),
        sa.Column("last_mark_date", sa.Date(), nullable=True),
        sa.Column("last_mark_price", sa.Float(precision=53), nullable=True),
        sa.Column("unrealized_pnl_pct", sa.Float(precision=53), nullable=True),
        sa.Column("closed_as_of", sa.Date(), nullable=True),
        sa.Column("exit_price", sa.Float(precision=53), nullable=True),
        sa.Column("realized_pnl_pct", sa.Float(precision=53), nullable=True),
        sa.Column("close_reason", sa.String(16), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticker",
            "source",
            "opened_as_of",
            name="uq_paper_positions_ticker_source_opened",
        ),
    )
    op.create_index(
        "idx_paper_positions_source_status",
        "paper_positions",
        ["source", "status"],
    )
    op.create_index(
        "idx_paper_positions_ticker",
        "paper_positions",
        ["ticker"],
    )


def downgrade() -> None:
    op.drop_index("idx_paper_positions_ticker", table_name="paper_positions")
    op.drop_index("idx_paper_positions_source_status", table_name="paper_positions")
    op.drop_table("paper_positions")
