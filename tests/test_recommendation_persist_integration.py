"""Real-infra integration test for quant recommendation persistence (Step 3 / G3).

Requires Postgres + Redis + live yfinance. Skips-with-reason (never fails) when a
source is down, mirroring the rest of the integration suite. Run with the stack
up and migrated:

    docker compose -f infra/docker-compose.yml up -d
    .venv\\Scripts\\python.exe -m alembic -c backend/alembic.ini upgrade head
    .venv\\Scripts\\python.exe -m pytest -m integration -q tests/test_recommendation_persist_integration.py

Asserts that ``persist_quant_recommendation`` lands a row, that the stored
action/confidence/snapshot_id round-trip the returned contract, and that the FK
to ``data_snapshots`` holds (the referenced snapshot row exists).
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import delete, select

from backend.app.database import create_sync_session
from backend.app.models.data_snapshot import DataSnapshot
from backend.app.models.recommendation import RecommendationRecord
from backend.app.services.recommendation_service import persist_quant_recommendation

from tests.integration_helpers import (  # noqa: F401 (fixtures)
    require_postgres,
    require_redis,
    require_yfinance,
)

pytestmark = pytest.mark.integration

GOLDEN = "RELIANCE.NS"


@pytest.fixture
def clean_recommendations():
    """Delete recommendations rows for the golden ticker before and after."""

    def _clean():
        session = create_sync_session()
        try:
            session.execute(
                delete(RecommendationRecord).where(RecommendationRecord.ticker == GOLDEN)
            )
            session.commit()
        finally:
            session.close()

    _clean()
    yield _clean
    _clean()


def test_persist_quant_recommendation_lands_row_with_valid_fk(
    require_postgres, require_redis, require_yfinance, clean_recommendations
):
    """persist_quant_recommendation lands one row whose stored fields round-trip
    the returned contract and whose snapshot_id FK resolves in data_snapshots."""
    as_of = date.today()

    rec = persist_quant_recommendation(GOLDEN, as_of)

    session = create_sync_session()
    try:
        row = session.execute(
            select(RecommendationRecord).where(
                RecommendationRecord.ticker == GOLDEN,
                RecommendationRecord.as_of == as_of,
                RecommendationRecord.source == "quant",
            )
        ).scalar_one()

        # Stored columns round-trip the returned Recommendation contract.
        assert row.action == rec.action.value
        assert row.confidence == pytest.approx(rec.confidence)
        assert row.snapshot_id == rec.snapshot_id
        assert row.horizon_days == rec.horizon_days
        assert row.source == "quant"
        assert row.model_id is None
        assert isinstance(row.evidence, list)

        # FK integrity: the referenced snapshot row exists.
        snapshot = session.execute(
            select(DataSnapshot).where(DataSnapshot.snapshot_id == row.snapshot_id)
        ).scalar_one_or_none()
        assert snapshot is not None, "snapshot_id FK must resolve in data_snapshots"
        assert snapshot.ticker == GOLDEN
    finally:
        session.close()


def test_persist_quant_recommendation_is_idempotent_upsert(
    require_postgres, require_redis, require_yfinance, clean_recommendations
):
    """Running the same day twice upserts in place — one row, not two."""
    as_of = date.today()

    persist_quant_recommendation(GOLDEN, as_of)
    persist_quant_recommendation(GOLDEN, as_of)

    session = create_sync_session()
    try:
        rows = session.execute(
            select(RecommendationRecord).where(
                RecommendationRecord.ticker == GOLDEN,
                RecommendationRecord.as_of == as_of,
                RecommendationRecord.source == "quant",
            )
        ).scalars().all()
        assert len(rows) == 1, "re-running a day must upsert, not duplicate"
    finally:
        session.close()
