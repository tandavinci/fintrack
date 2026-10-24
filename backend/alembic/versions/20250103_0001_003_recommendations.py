"""Step-3 recommendations table (G3 persistence backbone)

Revision ID: 003
Revises: 002
Create Date: 2025-01-03 00:01:00.000000

Creates the ``recommendations`` table: the durable projection of the single
Pydantic Recommendation contract. ``confidence`` is DOUBLE PRECISION
(sa.Float(precision=53)) so the contract's float round-trips exactly;
``evidence`` (list[str]) lands in JSONB. ``snapshot_id`` is a FK into
``data_snapshots`` so every stored recommendation traces to the rows that
justified it. The (ticker, as_of, source) unique constraint makes re-running a
day an UPSERT, not a duplicate (the repository keys ON CONFLICT on it); quant vs
deep disagreement coexists as two rows because they differ by ``source``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recommendations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("action", sa.String(4), nullable=False),
        # DOUBLE PRECISION (binary64) so the contract's confidence round-trips exactly.
        sa.Column("confidence", sa.Float(precision=53), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("model_id", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.Column("snapshot_id", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["data_snapshots.snapshot_id"]),
        sa.UniqueConstraint("ticker", "as_of", "source", name="uq_recommendations_ticker_asof_source"),
    )
    op.create_index("idx_recommendations_ticker_asof", "recommendations", ["ticker", "as_of"])


def downgrade() -> None:
    op.drop_index("idx_recommendations_ticker_asof", table_name="recommendations")
    op.drop_table("recommendations")
