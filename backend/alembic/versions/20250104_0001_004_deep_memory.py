"""Step-4 deep_memory table (G4 memory-log PostgreSQL mirror)

Revision ID: 004
Revises: 003
Create Date: 2025-01-04 00:01:00.000000

Creates the ``deep_memory`` table: a durable mirror of TradingAgents' own
markdown memory log (``trading_memory.md``, the backup export — its source is
never edited, STEERING rule 2). The deep service copies each ticker's entries —
parsed by TradingAgents' OWN ``load_entries()`` — into this table right after
that ticker's deep run (write-as-you-go). ``raw_return``/``alpha_return``/
``holding_days`` are TEXT because the parser emits display strings. The
(ticker, trade_date) unique constraint makes a re-mirror an UPSERT (the repo
keys ON CONFLICT on it), so a pending entry that later settles updates the SAME
row in place.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deep_memory",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("rating", sa.String(16), nullable=True),
        sa.Column("pending", sa.Boolean(), nullable=False),
        sa.Column("raw_return", sa.String(32), nullable=True),
        sa.Column("alpha_return", sa.String(32), nullable=True),
        sa.Column("holding_days", sa.String(32), nullable=True),
        sa.Column("resolved_date", sa.Date(), nullable=True),
        sa.Column("decision", sa.Text(), nullable=False, server_default=""),
        sa.Column("reflection", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", "trade_date", name="uq_deep_memory_ticker_date"),
    )
    op.create_index("idx_deep_memory_ticker_date", "deep_memory", ["ticker", "trade_date"])


def downgrade() -> None:
    op.drop_index("idx_deep_memory_ticker_date", table_name="deep_memory")
    op.drop_table("deep_memory")
