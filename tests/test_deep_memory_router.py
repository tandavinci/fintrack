"""Tests for the read-only Deep Memory endpoint (Step 5 / G5 "Agent Debate").

Two layers:
  * OFFLINE structural test that the router source never imports ``deep.adapter``
    / never calls ``analyze`` — the Agent Debate viewer only READS the
    ``deep_memory`` mirror table; the deep lane is never run in a web request
    (STEERING rule 6). Copied from the ``deep_progress`` structural check (AST
    level, so a prose docstring mention does not trip it).
  * ``@pytest.mark.integration`` end-to-end over the real app + Postgres
    (skip-with-reason when Postgres is down): seed a ``deep_memory`` row and
    assert ``GET /api/deep/memory`` and the ``/{ticker}`` variant return it.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from backend.app.routers import deep_memory


# --------------------------------------------------------------------------- #
# OFFLINE structural boundary (STEERING rule 6)                               #
# --------------------------------------------------------------------------- #


def test_router_source_never_imports_deep_lane():
    """The web layer must not import deep.adapter or reference analyze — the deep
    lane is never invoked from a web request (STEERING rule 6).

    Checked at the AST level so a prose mention in the module docstring (which
    legitimately explains the boundary) does not trip the structural assertion:
    we assert on real import statements and real ``analyze`` calls only.
    """
    source = Path(inspect.getfile(deep_memory)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        # No `from deep.adapter import ...` / `from deep import adapter`.
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert "deep.adapter" not in mod
            if mod == "deep":
                assert all(alias.name != "adapter" for alias in node.names)
        # No `import deep.adapter`.
        if isinstance(node, ast.Import):
            assert all("deep.adapter" not in alias.name for alias in node.names)
        # No call to a bare `analyze(...)`.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "analyze"

    # And the module object never resolved the adapter into its namespace.
    assert not hasattr(deep_memory, "analyze")
    assert not hasattr(deep_memory, "adapter")


def test_endpoint_registered():
    from backend.app.main import app

    paths = [r.path for r in app.routes]
    assert "/api/deep/memory" in paths
    assert "/api/deep/memory/{ticker}" in paths


# --------------------------------------------------------------------------- #
# Integration: real app + Postgres (skip-with-reason when down)               #
# --------------------------------------------------------------------------- #

pytestmark_integration = pytest.mark.integration


@pytestmark_integration
class TestDeepMemoryEndpointIntegration:
    """End-to-end over the real app + Postgres (skips-with-reason when down)."""

    TICKER = "DMEMTEST.NS"  # synthetic — never collides with a real golden ticker
    TRADE_DATE = "2024-03-11"

    @pytest.fixture
    def seeded(self):
        """Seed one deep_memory row for the synthetic ticker; clean up after.

        Skips-with-reason if Postgres is unreachable.
        """
        from sqlalchemy import delete

        from tests.integration_helpers import postgres_up

        if not postgres_up():
            pytest.skip("Postgres unreachable")

        from backend.app.database import create_sync_session
        from backend.app.models.deep_memory import DeepMemoryEntry
        from backend.app.repositories.deep_memory_repository import (
            SyncDeepMemoryRepository,
        )

        def _clean():
            session = create_sync_session()
            try:
                session.execute(
                    delete(DeepMemoryEntry).where(DeepMemoryEntry.ticker == self.TICKER)
                )
                session.commit()
            finally:
                session.close()

        _clean()

        # Seed via the SAME upsert path the service uses (load_entries() shape).
        SyncDeepMemoryRepository().upsert_entry(
            {
                "ticker": self.TICKER,
                "date": self.TRADE_DATE,
                "rating": "Buy",
                "pending": False,
                "raw": "+12.5%",
                "alpha": "+3.1%",
                "holding": "20d",
                "resolved": "2024-04-10",
                "decision": "Buy DMEMTEST on momentum breakout.",
                "reflection": "Played out; trend held through the window.",
            }
        )

        yield
        _clean()

    @pytest.fixture(scope="class")
    def client(self):
        """ONE TestClient for the whole class (single event loop — see fusion test)."""
        from fastapi.testclient import TestClient

        from backend.app.main import app

        with TestClient(app) as c:
            yield c

    def test_memory_list_returns_seeded_row(self, seeded, client):
        resp = client.get(f"/api/deep/memory?ticker={self.TICKER}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] >= 1
        entry = next(e for e in body["entries"] if e["ticker"] == self.TICKER)
        assert entry["rating"] == "Buy"
        assert entry["pending"] is False
        assert entry["decision"].startswith("Buy DMEMTEST")
        assert "Played out" in entry["reflection"]
        assert entry["resolved_date"] == "2024-04-10"

    def test_memory_by_ticker_path_variant(self, seeded, client):
        resp = client.get(f"/api/deep/memory/{self.TICKER}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ticker"] == self.TICKER
        assert body["count"] >= 1
        assert all(e["ticker"] == self.TICKER for e in body["entries"])

    def test_memory_unknown_ticker_is_empty_not_error(self, seeded, client):
        resp = client.get("/api/deep/memory/NOSUCHTICKER.NS")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] == 0
        assert body["entries"] == []
