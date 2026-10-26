"""Step-5 signal_alerts table (G5 system-generated alerts)

Revision ID: 005
Revises: 004
Create Date: 2025-01-05 00:01:00.000000

Creates the ``signal_alerts`` table: a SEPARATE, additive subsystem from the
EXISTING user-configured ``alerts`` table (left untouched). These are
SYSTEM-generated insights derived from the nightly pipeline output — AI (quant
pattern), RISK (portfolio volatility), and NEWS (fresh headline) alerts. The
``dedup_hash`` unique constraint makes re-running the generator for a day an
idempotent no-op for already-stored alerts (the repo keys its
``ON CONFLICT (dedup_hash) DO NOTHING`` on it, mirroring ``news_items``).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "signal_alerts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(32), nullable=True),  # NULL = market-wide
        sa.Column("alert_kind", sa.String(16), nullable=False),  # AI / RISK / NEWS
        sa.Column("severity", sa.String(8), nullable=False, server_default="info"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("dedup_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_hash", name="uq_signal_alerts_dedup"),
    )
    op.create_index(
        "idx_signal_alerts_ticker_asof", "signal_alerts", ["ticker", "as_of"]
    )
    op.create_index(
        "idx_signal_alerts_kind_asof", "signal_alerts", ["alert_kind", "as_of"]
    )


def downgrade() -> None:
    op.drop_index("idx_signal_alerts_kind_asof", table_name="signal_alerts")
    op.drop_index("idx_signal_alerts_ticker_asof", table_name="signal_alerts")
    op.drop_table("signal_alerts")
