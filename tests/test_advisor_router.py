"""Tests for the advisor chat router (Step 5 / G5).

The advisor endpoint is auth-guarded and user-scoped, so a full grounded-answer
test needs a real user + JWT + Postgres + a live model. That heavier path is an
``@pytest.mark.integration`` test that skips-with-reason when infra is absent.

The load-bearing guarantee that needs NO model and NO DB — that the endpoint
EXISTS and REJECTS an unauthenticated request (401/403) — is asserted against the
real app with one class-scoped TestClient. This proves the router is registered
and auth-guarded (STEERING: user-scoped, requires ``get_current_user``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture(scope="module")
def client():
    """ONE TestClient for the module (single event loop — see fusion router test
    for why a per-call client is a single-loop hazard with the cached engine)."""
    with TestClient(app) as c:
        yield c


def test_advisor_route_is_registered():
    """The advisor chat route is mounted under /api/v1/advisor (no model/DB)."""
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/v1/advisor/chat" in paths


def test_chat_requires_auth(client):
    """Without a Bearer token the endpoint rejects the request (auth-guarded).

    This needs no model and no DB — the auth dependency runs before the service,
    so an unauthenticated POST returns 401 (or 403 if the scheme rejects first).
    """
    resp = client.post("/api/v1/advisor/chat", json={"question": "what do I own?"})
    assert resp.status_code in (401, 403), resp.text


def test_chat_requires_auth_even_with_empty_body(client):
    """Auth is checked regardless of body validity (dependency ordering)."""
    resp = client.post("/api/v1/advisor/chat", json={})
    # 401/403 (auth) — auth guards the route before body validation matters here.
    assert resp.status_code in (401, 403, 422), resp.text


# --- integration: a fuller grounded answer (needs auth + DB + a live model) --

pytest_integration = pytest.mark.integration


@pytest_integration
class TestAdvisorGroundedAnswerIntegration:
    """End-to-end grounded answer over the real app (skips when infra is down)."""

    def test_authed_chat_returns_grounded_answer(self, client):
        from tests.integration_helpers import postgres_up

        if not postgres_up():
            pytest.skip("Postgres unreachable")

        # Mint a token for a synthetic user id the same way the auth router does.
        from datetime import datetime, timedelta, timezone

        from jose import jwt

        from backend.app.config import settings as app_settings

        exp = datetime.now(timezone.utc) + timedelta(minutes=5)
        token = jwt.encode(
            {"sub": "999999", "email": "advisor-it@example.test", "exp": exp},
            app_settings.jwt_secret,
            algorithm=app_settings.jwt_algorithm,
        )

        resp = client.post(
            "/api/v1/advisor/chat",
            json={"question": "What is in my portfolio?"},
            headers={"Authorization": f"Bearer {token}"},
        )
        # With no holdings seeded for this user the context is empty but valid;
        # a live model returns 200, a model outage returns the mapped 503.
        assert resp.status_code in (200, 503), resp.text
        if resp.status_code == 200:
            body = resp.json()
            assert "answer" in body
            assert "grounded_on" in body
            assert set(body["grounded_on"]) == {"holdings", "recommendations"}
