"""Integration tests for the signal-alert subsystem (Step 5 / G5).

End-to-end over the REAL app + Postgres (skip-with-reason when Postgres is
down). Seeds a minimal universe (a Stock in a Holding), a quant Recommendation
(BUY, high confidence -> an AI alert) and a fresh news_item (-> a NEWS alert),
runs the sync ``generate_and_store`` generator, then asserts:

  * ``signal_alerts`` rows exist for the seeded ticker;
  * ``GET /api/alerts/signals`` returns them, and the ``?kind=`` filter works;
  * re-running ``generate_and_store`` inserts NOTHING new (dedup on dedup_hash).

Uses ONE class-scoped ``TestClient`` (one event loop) exactly like
``tests/test_fusion_router.py`` to avoid the asyncpg closed-loop hazard.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

pytest_integration = pytest.mark.integration


@pytest_integration
class TestSignalAlertsIntegration:
    """End-to-end over the real app + Postgres (skips-with-reason when down)."""

    TICKER = "SIGALERT.NS"
    SNAPSHOT_ID = "sigalert00001a"
    EMAIL = "sigalert-test@example.test"
    AS_OF = date(2024, 1, 15)
    NEWS_HASH = "sigalertnews" + "0" * 52  # 64-char dedup_hash

    # --- seed / teardown ----------------------------------------------------

    @pytest.fixture
    def seeded(self):
        """Seed a universe ticker + quant rec + fresh news item; clean up after.

        Skips-with-reason if Postgres is unreachable.
        """
        from sqlalchemy import delete, select

        from tests.integration_helpers import postgres_up

        if not postgres_up():
            pytest.skip("Postgres unreachable")

        from backend.app.database import create_sync_session
        from backend.app.models.data_snapshot import DataSnapshot
        from backend.app.models.news_item import NewsItem
        from backend.app.models.portfolio import Holding, Portfolio
        from backend.app.models.recommendation import RecommendationRecord
        from backend.app.models.signal_alert import SignalAlert
        from backend.app.models.stock import Sector, Stock
        from backend.app.models.user import User
        from backend.app.repositories.recommendation_repository import (
            SyncRecommendationRepository,
        )
        from backend.app.schemas.recommendation import Action, Recommendation

        session = create_sync_session()

        def _clean():
            s = create_sync_session()
            try:
                s.execute(delete(SignalAlert).where(SignalAlert.ticker == self.TICKER))
                s.execute(delete(SignalAlert).where(SignalAlert.dedup_hash == self.NEWS_HASH))
                s.execute(delete(NewsItem).where(NewsItem.dedup_hash == self.NEWS_HASH))
                s.execute(delete(RecommendationRecord).where(RecommendationRecord.ticker == self.TICKER))
                s.execute(delete(DataSnapshot).where(DataSnapshot.snapshot_id == self.SNAPSHOT_ID))
                # Holdings/portfolio/stock/user for this test ticker + email.
                stock = s.execute(select(Stock).where(Stock.ticker_symbol == self.TICKER)).scalar_one_or_none()
                if stock is not None:
                    s.execute(delete(Holding).where(Holding.stock_id == stock.id))
                user = s.execute(select(User).where(User.email == self.EMAIL)).scalar_one_or_none()
                if user is not None:
                    portfolios = s.execute(select(Portfolio).where(Portfolio.user_id == user.id)).scalars().all()
                    for p in portfolios:
                        s.execute(delete(Holding).where(Holding.portfolio_id == p.id))
                        s.execute(delete(Portfolio).where(Portfolio.id == p.id))
                if stock is not None:
                    s.execute(delete(Stock).where(Stock.id == stock.id))
                if user is not None:
                    s.execute(delete(User).where(User.id == user.id))
                s.commit()
            finally:
                s.close()

        _clean()

        try:
            # Sector (reuse-or-create a shared test sector).
            sector = session.execute(
                select(Sector).where(Sector.name == "SigAlertTestSector")
            ).scalar_one_or_none()
            if sector is None:
                sector = Sector(name="SigAlertTestSector", description="test")
                session.add(sector)
                session.flush()

            stock = Stock(
                bse_code="SIGALERT1",
                ticker_symbol=self.TICKER,
                company_name="SigAlert Test Co",
                sector_id=sector.id,
            )
            session.add(stock)
            session.flush()

            user = User(
                email=self.EMAIL,
                password_hash="x",
                full_name="SigAlert Tester",
            )
            session.add(user)
            session.flush()

            portfolio = Portfolio(user_id=user.id, name="SigAlert PF")
            session.add(portfolio)
            session.flush()

            session.add(
                Holding(
                    portfolio_id=portfolio.id,
                    stock_id=stock.id,
                    total_quantity=10,
                    average_price=100,
                )
            )

            # Snapshot (FK target for the recommendation).
            session.add(
                DataSnapshot(
                    snapshot_id=self.SNAPSHOT_ID,
                    ticker=self.TICKER,
                    as_of=self.AS_OF,
                    payload={"prices": {}, "indicators": {}, "headlines": []},
                )
            )

            # A fresh news item (published now, so within the 24h window when
            # the generator runs for as_of=today).
            session.add(
                NewsItem(
                    ticker=self.TICKER,
                    source="BSE",
                    headline="SigAlert Co posts record quarter",
                    url="https://example.test/sigalert",
                    published_at=datetime.now(timezone.utc),
                    dedup_hash=self.NEWS_HASH,
                )
            )
            session.commit()
        finally:
            session.close()

        # Quant rec: BUY at high confidence -> an AI alert (via the contract).
        repo = SyncRecommendationRepository()
        repo.save(
            Recommendation(
                ticker=self.TICKER,
                as_of=self.AS_OF,
                action=Action.BUY,
                confidence=0.72,
                horizon_days=10,
                rationale="Golden cross with rising volume (test).",
                evidence=["sigalert.evidence=1"],
                source="quant",
                model_id=None,
                prompt_version=None,
                snapshot_id=self.SNAPSHOT_ID,
            )
        )

        yield
        _clean()

    @pytest.fixture(scope="class")
    def client(self):
        """ONE TestClient for the whole class (single event loop).

        Mirrors tests/test_fusion_router.py: a fresh TestClient per call closes
        its own loop, orphaning the module-scoped asyncpg pool. Sharing one loop
        across the class avoids the "'NoneType' object has no attribute 'send'"
        hazard.
        """
        from fastapi.testclient import TestClient

        from backend.app.main import app

        with TestClient(app) as c:
            yield c

    # --- the tests ----------------------------------------------------------

    def test_generate_persists_ai_and_news_alerts(self, seeded):
        """Running the generator writes AI + NEWS alerts for the seeded ticker.

        ``generate_and_store`` uses ``as_of=today`` so the just-published news
        item is inside the 24h freshness window. The AI alert is keyed on the
        quant rec (its own as_of is historical but the alert's as_of is today —
        both are fine; the test only asserts the rows land).
        """
        from sqlalchemy import select

        from backend.app.database import create_sync_session
        from backend.app.models.signal_alert import SignalAlert
        from backend.app.services.signal_alert_service import generate_and_store

        summary = generate_and_store()  # as_of defaults to today
        assert summary["ai"] >= 1
        assert summary["news"] >= 1
        assert summary["inserted"] >= 2

        session = create_sync_session()
        try:
            rows = (
                session.execute(
                    select(SignalAlert).where(SignalAlert.ticker == self.TICKER)
                )
                .scalars()
                .all()
            )
        finally:
            session.close()

        kinds = {r.alert_kind for r in rows}
        assert "AI" in kinds
        assert "NEWS" in kinds

    def test_api_returns_alerts_and_kind_filter_works(self, seeded, client):
        from backend.app.services.signal_alert_service import generate_and_store

        generate_and_store()

        # Unfiltered: our ticker's alerts are present.
        resp = client.get("/api/alerts/signals", params={"ticker": self.TICKER, "limit": 50})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] >= 2
        returned_kinds = {a["alert_kind"] for a in body["alerts"]}
        assert {"AI", "NEWS"} <= returned_kinds
        assert all(a["ticker"] == self.TICKER for a in body["alerts"])

        # ?kind=AI filter: only AI rows for this ticker.
        resp_ai = client.get(
            "/api/alerts/signals", params={"ticker": self.TICKER, "kind": "AI"}
        )
        assert resp_ai.status_code == 200, resp_ai.text
        ai_body = resp_ai.json()
        assert ai_body["count"] >= 1
        assert all(a["alert_kind"] == "AI" for a in ai_body["alerts"])

        # ?kind=NEWS filter: only NEWS rows for this ticker.
        resp_news = client.get(
            "/api/alerts/signals", params={"ticker": self.TICKER, "kind": "NEWS"}
        )
        assert resp_news.status_code == 200, resp_news.text
        assert all(a["alert_kind"] == "NEWS" for a in resp_news.json()["alerts"])

    def test_rerun_is_idempotent_no_duplicates(self, seeded):
        """A second generate_and_store run inserts nothing new (dedup)."""
        from sqlalchemy import func, select

        from backend.app.database import create_sync_session
        from backend.app.models.signal_alert import SignalAlert
        from backend.app.services.signal_alert_service import generate_and_store

        first = generate_and_store()
        assert first["inserted"] >= 2

        def _count() -> int:
            session = create_sync_session()
            try:
                return int(
                    session.execute(
                        select(func.count())
                        .select_from(SignalAlert)
                        .where(SignalAlert.ticker == self.TICKER)
                    ).scalar_one()
                )
            finally:
                session.close()

        count_after_first = _count()

        second = generate_and_store()
        assert second["inserted"] == 0  # everything already stored -> dedup
        assert _count() == count_after_first  # no new rows
