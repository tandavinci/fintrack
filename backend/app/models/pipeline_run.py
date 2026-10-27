"""PipelineRun ORM model (Step 5 / G5 unattended-day audit record).

One persisted row per NIGHTLY PIPELINE run, keyed UNIQUELY on ``run_date`` (one
run per day; a re-run of the same day upserts the existing row rather than
creating a duplicate). This is the audit backbone for the G5 "unattended day":
it records WHEN the chained pipeline started and finished, its overall status,
and a per-stage summary so an operator (or the Model Status page) can answer
"did last night's pipeline finish, and when?".

The table is deliberately small and append-light: the orchestrator
(``app.tasks.pipeline.run_nightly_pipeline``) calls ``start`` once, merges each
stage's summary as it completes, then ``finish`` once. ``stages`` is JSONB keyed
by stage name (e.g. ``{"data": {...}, "quant": {...}, "deep": {...},
"alerts": {...}}``) so adding a stage never needs a migration.

HONEST SCOPING NOTE: this record proves the chain RAN IN ORDER and whether it
COMPLETED; the Gate-G5 "finishes before 09:00 IST" is a TIMED guarantee that
depends on real deep-run speed and is only provable on capable hardware (the
same blocker as the G4 acceptance). ``finished_at`` is what a future wall-clock
check would read; the deadline itself is a TK-hardware check.
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database import Base


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # "running" | "completed" | "failed"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    # Per-stage summary dicts, e.g. {"quant": {...}, "deep": {...}, "alerts": {...}}.
    stages: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("run_date", name="uq_pipeline_runs_run_date"),
    )
