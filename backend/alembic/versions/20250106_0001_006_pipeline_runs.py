"""Step-5 pipeline_runs table (G5 unattended-day audit record)

Revision ID: 006
Revises: 005
Create Date: 2025-01-06 00:01:00.000000

Creates the ``pipeline_runs`` table: one row per nightly pipeline run, keyed
UNIQUELY on ``run_date`` so a re-run of the same day UPSERTS in place rather
than duplicating. It records when the chained pipeline started/finished, its
overall status ("running"/"completed"/"failed"), and a per-stage JSONB summary
(``{"data": {...}, "quant": {...}, "deep": {...}, "alerts": {...}}``) so an
operator can tell whether last night's unattended run finished and when. The
JSONB ``stages`` means adding a stage never needs a migration. Additive: no
existing table is touched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column(
            "stages",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_date", name="uq_pipeline_runs_run_date"),
    )


def downgrade() -> None:
    op.drop_table("pipeline_runs")
