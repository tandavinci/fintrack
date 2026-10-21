"""Step-2 data tables: price_bars, news_items, data_snapshots + pgvector

Revision ID: 002
Revises: 001
Create Date: 2025-01-02 00:01:00.000000

Creates the three Step-2 data tables in one revision (decision A10) plus the
pgvector extension enable (provisions for Step-4 RAG; satisfies scope.md's
pgvector requirement even though no vector column is used yet).

OHLCV columns are DOUBLE PRECISION (sa.Float(precision=53)) so a write->read
round-trip is bit-identical to the pandas float64 frame — see design §4.4b/§5.1
(G2 determinism).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector (provisions for Step-4 RAG; mirrors rev 001's pg_trgm enable).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- price_bars ---
    op.create_table(
        "price_bars",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=False), nullable=False),
        # DOUBLE PRECISION (binary64) so OHLCV round-trips bit-identically to the
        # pandas float64 frame — see §4.4b / §5.1 (G2 determinism).
        sa.Column("open", sa.Float(precision=53), nullable=False),
        sa.Column("high", sa.Float(precision=53), nullable=False),
        sa.Column("low", sa.Float(precision=53), nullable=False),
        sa.Column("close", sa.Float(precision=53), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("source", sa.String(16), nullable=False, server_default=sa.text("'yfinance'")),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", "interval", "ts", name="uq_price_bars_tkr_interval_ts"),
    )
    op.create_index("idx_price_bars_lookup", "price_bars", ["ticker", "interval", "ts"])

    # --- news_items ---
    op.create_table(
        "news_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(32), nullable=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("dedup_hash", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_hash", name="uq_news_items_dedup"),
    )
    op.create_index("idx_news_ticker_published", "news_items", ["ticker", "published_at"])

    # --- data_snapshots ---
    op.create_table(
        "data_snapshots",
        sa.Column("snapshot_id", sa.String(16), nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    op.create_index("idx_snapshots_ticker_asof", "data_snapshots", ["ticker", "as_of"])


def downgrade() -> None:
    op.drop_index("idx_snapshots_ticker_asof", table_name="data_snapshots")
    op.drop_table("data_snapshots")

    op.drop_index("idx_news_ticker_published", table_name="news_items")
    op.drop_table("news_items")

    op.drop_index("idx_price_bars_lookup", table_name="price_bars")
    op.drop_table("price_bars")
