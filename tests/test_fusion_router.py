"""Tests for the recommendation fusion view (Step 5 / G5 product unification).

Two layers:
  * OFFLINE unit tests of the pure selection helper ``_pick_latest_per_source``
    (no DB) — "pick the newest row per source" is factored out so it is testable
    with plain objects: quant+deep present -> both; only quant -> deep absent;
    multiple quant rows -> newest wins (as_of, then created_at tiebreak).
  * ``@pytest.mark.integration`` end-to-end tests against the real app + Postgres
    (skip-with-reason when Postgres is down): seed quant+deep rows with differing
    actions and assert both come back with conflict=True; a quant-only ticker
    returns deep=null, conflict=False; an unknown ticker returns 404.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import pytest

from backend.app.repositories.recommendation_repository import (
    _pick_latest_per_source,
)


# --- offline unit tests of the pure selection helper -------------------------


@dataclass
class _Row:
    """Minimal row stand-in for the selection helper (no DB, no ORM)."""

    source: str
    as_of: date
    created_at: datetime | None = None
    action: str = "BUY"


def test_pick_latest_both_sources_present():
    rows = [
        _Row(source="quant", as_of=date(2024, 1, 10)),
        _Row(source="deep", as_of=date(2024, 1, 9)),
    ]
    picked = _pick_latest_per_source(rows)
    assert set(picked) == {"quant", "deep"}
    assert picked["quant"].as_of == date(2024, 1, 10)
    assert picked["deep"].as_of == date(2024, 1, 9)


def test_pick_latest_only_one_source_means_other_absent():
    rows = [_Row(source="quant", as_of=date(2024, 1, 10))]
    picked = _pick_latest_per_source(rows)
    assert set(picked) == {"quant"}
    assert "deep" not in picked


def test_pick_latest_newest_as_of_wins_per_source():
    rows = [
        _Row(source="quant", as_of=date(2024, 1, 8), action="SELL"),
        _Row(source="quant", as_of=date(2024, 1, 12), action="BUY"),
        _Row(source="quant", as_of=date(2024, 1, 10), action="HOLD"),
    ]
    picked = _pick_latest_per_source(rows)
    assert picked["quant"].as_of == date(2024, 1, 12)
    assert picked["quant"].action == "BUY"


def test_pick_latest_created_at_breaks_as_of_tie():
    older = datetime(2024, 1, 12, 1, 0, tzinfo=timezone.utc)
    newer = datetime(2024, 1, 12, 9, 0, tzinfo=timezone.utc)
    rows = [
        _Row(source="quant", as_of=date(2024, 1, 12), created_at=older, action="HOLD"),
        _Row(source="quant", as_of=date(2024, 1, 12), created_at=newer, action="BUY"),
    ]
    picked = _pick_latest_per_source(rows)
    assert picked["quant"].action == "BUY"  # newer created_at wins the tie


def test_pick_latest_empty_input_is_empty_dict():
    assert _pick_latest_per_source([]) == {}


def test_pick_latest_advisor_source_kept_too():
    rows = [
        _Row(source="quant", as_of=date(2024, 1, 10)),
        _Row(source="deep", as_of=date(2024, 1, 10)),
        _Row(source="advisor", as_of=date(2024, 1, 10)),
    ]
    picked = _pick_latest_per_source(rows)
    assert set(picked) == {"quant", "deep", "advisor"}


# --- integration tests against the real app + Postgres -----------------------

pytest_integration = pytest.mark.integration


@pytest_integration
class TestFusionEndpointIntegration:
    """End-to-end over the real app + Postgres (skips-with-reason when down)."""

    TICKER_CONFLICT = "FUSIONTEST.NS"
    TICKER_QUANT_ONLY = "FUSIONONLY.NS"
    TICKER_UNKNOWN = "NOSUCHTICKER.NS"

    @staticmethod
    def _rec(ticker: str, source: str, action, snapshot_id: str):
        from backend.app.schemas.recommendation import Recommendation

        return Recommendation(
            ticker=ticker,
            as_of=date(2024, 1, 15),
            action=action,
            confidence=0.7,
            horizon_days=10,
            rationale=f"{source} rationale",
            evidence=[f"{source}.evidence=1"],
            source=source,
            model_id=None if source == "quant" else "llama3.2",
            prompt_version=None if source == "quant" else "v1",
            snapshot_id=snapshot_id,
        )

    @pytest.fixture
    def seeded(self):
        """Seed quant+deep (conflicting) and a quant-only ticker; clean up after.

        Requires Postgres AND a data_snapshots row to satisfy the snapshot FK.
        Skips-with-reason if Postgres is unreachable.
        """
        from sqlalchemy import delete, select

        from tests.integration_helpers import postgres_up

        if not postgres_up():
            pytest.skip("Postgres unreachable")

        from backend.app.database import create_sync_session
        from backend.app.models.data_snapshot import DataSnapshot
        from backend.app.models.recommendation import RecommendationRecord
        from backend.app.repositories.recommendation_repository import (
            SyncRecommendationRepository,
        )
        from backend.app.schemas.recommendation import Action

        tickers = [self.TICKER_CONFLICT, self.TICKER_QUANT_ONLY]
        snapshot_id = "fusiontest0001ab"

        def _clean():
            session = create_sync_session()
            try:
                session.execute(
                    delete(RecommendationRecord).where(
                        RecommendationRecord.ticker.in_(tickers)
                    )
                )
                session.execute(
                    delete(DataSnapshot).where(DataSnapshot.snapshot_id == snapshot_id)
                )
                session.commit()
            finally:
                session.close()

        _clean()

        # A minimal snapshot row so the recommendations.snapshot_id FK resolves.
        session = create_sync_session()
        try:
            existing = session.execute(
                select(DataSnapshot).where(DataSnapshot.snapshot_id == snapshot_id)
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    DataSnapshot(
                        snapshot_id=snapshot_id,
                        ticker=self.TICKER_CONFLICT,
                        as_of=date(2024, 1, 15),
                        payload={"prices": {}, "indicators": {}, "headlines": []},
                    )
                )
                session.commit()
        finally:
            session.close()

        repo = SyncRecommendationRepository()
        # Conflicting pair: quant BUY vs deep SELL for the same ticker.
        repo.save(self._rec(self.TICKER_CONFLICT, "quant", Action.BUY, snapshot_id))
        repo.save(self._rec(self.TICKER_CONFLICT, "deep", Action.SELL, snapshot_id))
        # Quant-only ticker.
        repo.save(self._rec(self.TICKER_QUANT_ONLY, "quant", Action.HOLD, snapshot_id))

        yield
        _clean()

    @pytest.fixture(scope="class")
    def client(self):
        """ONE TestClient for the whole class.

        The Tracker caches its async SQLAlchemy engine at module scope, bound to
        the event loop that first drives it. A fresh ``TestClient`` per call
        creates then CLOSES its own loop, leaving the cached asyncpg pool bound
        to a dead loop (``'NoneType' object has no attribute 'send'``) on the
        next call — the exact single-loop hazard documented in
        tests/integration_helpers. Sharing one client (one loop) across the
        class avoids it, mirroring production's single long-lived loop.
        """
        from fastapi.testclient import TestClient

        from backend.app.main import app

        with TestClient(app) as c:
            yield c

    def test_conflicting_pair_returns_both_and_conflict_true(self, seeded, client):
        resp = client.get(f"/api/fusion/{self.TICKER_CONFLICT}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["quant"] is not None
        assert body["deep"] is not None
        assert body["quant"]["action"] == "BUY"
        assert body["deep"]["action"] == "SELL"
        assert body["conflict"] is True

    def test_quant_only_returns_deep_null_no_conflict(self, seeded, client):
        resp = client.get(f"/api/fusion/{self.TICKER_QUANT_ONLY}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["quant"] is not None
        assert body["deep"] is None
        assert body["conflict"] is False

    def test_unknown_ticker_returns_404(self, seeded, client):
        resp = client.get(f"/api/fusion/{self.TICKER_UNKNOWN}")
        assert resp.status_code == 404
        assert self.TICKER_UNKNOWN in resp.json()["detail"]
