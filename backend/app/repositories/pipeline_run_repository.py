"""PipelineRun repository (Step 5 / G5 unattended-day audit record).

A tiny SYNCHRONOUS repository the orchestrator
(``app.tasks.pipeline.run_nightly_pipeline``) uses to record the nightly run.
Sync only — the pipeline task runs synchronously in-process on the Celery worker
(like ``quant_recommender`` / ``deep_recommender`` / ``signal_alerter``), so
there is no async caller and no async variant is needed.

Three small, idempotent writes:
  * ``start(run_date)`` — upsert the day's row to status=running, started_at=now,
    stages={} (resets a re-run cleanly).
  * ``record_stage(run_date, name, summary)`` — MERGE one stage's summary into
    the JSONB ``stages`` map (Postgres ``stages || {name: summary}``), so stages
    accumulate without read-modify-write races.
  * ``finish(run_date, status)`` — set finished_at=now and the final status.

All three key on ``run_date`` (the table's unique column). The upsert in
``start`` makes re-running a day safe; ``record_stage``/``finish`` target the
existing row by ``run_date``.
"""

from datetime import date
from typing import Optional

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.database import create_sync_session
from backend.app.models.pipeline_run import PipelineRun


class SyncPipelineRunRepository:
    """Synchronous repository for the ``pipeline_runs`` audit table."""

    def __init__(self, session: Optional[Session] = None):
        self._session = session

    def _get_session(self) -> Session:
        return self._session if self._session is not None else create_sync_session()

    def start(self, run_date: date) -> None:
        """Upsert the day's row to a fresh running state (started_at=now, stages={}).

        Keyed on ``run_date`` (ON CONFLICT DO UPDATE) so re-running a day resets
        its row rather than creating a duplicate.
        """
        session = self._get_session()
        owns = self._session is None
        try:
            stmt = pg_insert(PipelineRun).values(
                run_date=run_date,
                status="running",
                started_at=_now_expr(),
                finished_at=None,
                stages={},
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["run_date"],
                set_={
                    "status": "running",
                    "started_at": _now_expr(),
                    "finished_at": None,
                    "stages": {},
                },
            )
            session.execute(stmt)
            session.commit()
        finally:
            if owns:
                session.close()

    def record_stage(self, run_date: date, name: str, summary: dict) -> None:
        """Merge one stage's ``summary`` into the JSONB ``stages`` map.

        Uses ``stages = stages || jsonb_build_object(name, summary)`` so the
        single stage is added/overwritten atomically in the database, without a
        read-modify-write round-trip (idempotent, race-free).
        """
        session = self._get_session()
        owns = self._session is None
        try:
            from sqlalchemy import func

            merged = PipelineRun.stages.op("||")(
                func.jsonb_build_object(name, _as_jsonb(summary))
            )
            stmt = (
                update(PipelineRun)
                .where(PipelineRun.run_date == run_date)
                .values(stages=merged)
            )
            session.execute(stmt)
            session.commit()
        finally:
            if owns:
                session.close()

    def finish(self, run_date: date, status: str) -> None:
        """Set ``finished_at=now`` and the final ``status`` for the day's row."""
        session = self._get_session()
        owns = self._session is None
        try:
            stmt = (
                update(PipelineRun)
                .where(PipelineRun.run_date == run_date)
                .values(finished_at=_now_expr(), status=status)
            )
            session.execute(stmt)
            session.commit()
        finally:
            if owns:
                session.close()


def _now_expr():
    """Server-side ``now()`` so timestamps use the DB clock (tz-aware)."""
    from sqlalchemy import func

    return func.now()


def _as_jsonb(value):
    """Cast a Python dict to a JSONB bind param for the ``||`` merge."""
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import JSONB

    return cast(value, JSONB)
