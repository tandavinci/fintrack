"""Integration test for the pipeline-status subsystem (Step 5 / G5).

End-to-end over the REAL app + Postgres (skip-with-reason when Postgres is
down). Drives the SYNC ``SyncPipelineRunRepository`` exactly as the orchestrator
would — ``start`` -> ``record_stage`` (quant/deep/alerts) -> ``finish`` — then
``GET /api/pipeline/status`` and asserts the row comes back with the per-stage
summaries and a completed status.

Uses ONE class-scoped ``TestClient`` (one event loop) exactly like
``tests/test_signal_alerts_integration.py`` to avoid the asyncpg closed-loop
hazard.
"""

from __future__ import annotations

from datetime import date

import pytest

pytest_integration = pytest.mark.integration


@pytest_integration
class TestPipelineStatusIntegration:
    """End-to-end over the real app + Postgres (skips-with-reason when down)."""

    RUN_DATE = date(2024, 1, 16)  # a distinctive historical date for this test

    @pytest.fixture
    def seeded(self):
        """Record one full pipeline run via the sync repo; clean up after."""
        from sqlalchemy import delete

        from tests.integration_helpers import postgres_up

        if not postgres_up():
            pytest.skip("Postgres unreachable")

        from backend.app.database import create_sync_session
        from backend.app.models.pipeline_run import PipelineRun
        from backend.app.repositories.pipeline_run_repository import (
            SyncPipelineRunRepository,
        )

        def _clean():
            s = create_sync_session()
            try:
                s.execute(
                    delete(PipelineRun).where(PipelineRun.run_date == self.RUN_DATE)
                )
                s.commit()
            finally:
                s.close()

        _clean()

        repo = SyncPipelineRunRepository()
        repo.start(self.RUN_DATE)
        repo.record_stage(self.RUN_DATE, "data", {"note": "lanes fetch their own data"})
        repo.record_stage(self.RUN_DATE, "quant", {"processed": 12, "errors": 0})
        repo.record_stage(self.RUN_DATE, "deep", {"processed": 5, "total": 5})
        repo.record_stage(self.RUN_DATE, "alerts", {"ai": 7, "inserted": 7})
        repo.finish(self.RUN_DATE, "completed")

        yield
        _clean()

    @pytest.fixture(scope="class")
    def client(self):
        """ONE TestClient for the whole class (single event loop)."""
        from fastapi.testclient import TestClient

        from backend.app.main import app

        with TestClient(app) as c:
            yield c

    def test_repo_persists_merged_stages_and_completes(self, seeded):
        """The sync repo upsert/merge/finish lands one row with all four stages."""
        from sqlalchemy import select

        from backend.app.database import create_sync_session
        from backend.app.models.pipeline_run import PipelineRun

        session = create_sync_session()
        try:
            row = session.execute(
                select(PipelineRun).where(PipelineRun.run_date == self.RUN_DATE)
            ).scalar_one()
        finally:
            session.close()

        assert row.status == "completed"
        assert row.started_at is not None
        assert row.finished_at is not None
        assert set(row.stages.keys()) == {"data", "quant", "deep", "alerts"}
        assert row.stages["quant"] == {"processed": 12, "errors": 0}
        assert row.stages["alerts"] == {"ai": 7, "inserted": 7}

    def test_api_returns_run_with_stage_summaries(self, seeded, client):
        resp = client.get("/api/pipeline/status", params={"limit": 20})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] >= 1

        run = next(
            (r for r in body["runs"] if r["run_date"] == self.RUN_DATE.isoformat()),
            None,
        )
        assert run is not None, "seeded run not returned by /api/pipeline/status"
        assert run["status"] == "completed"
        assert run["started_at"] is not None
        assert run["finished_at"] is not None
        assert run["stages"]["quant"] == {"processed": 12, "errors": 0}
        assert run["stages"]["deep"] == {"processed": 5, "total": 5}
        assert run["stages"]["alerts"] == {"ai": 7, "inserted": 7}
        assert run["stages"]["data"] == {"note": "lanes fetch their own data"}
