"""Integration test for the read-only Backtest endpoint (Step 5 / G5).

Needs LIVE DATA: the endpoint runs the deterministic rule-based
``BacktestEngine`` over a recent daily window, which pulls prices via yfinance
(and/or the price-bar table). This test is ``@pytest.mark.integration`` and
skips-with-reason when yfinance is unreachable (Yahoo rate-limiting / offline) —
`pytest` on a bare machine stays GREEN with it SKIPPED, never failed.

Run with the data path healthy:

    .venv\\Scripts\\python.exe -m pytest -m integration -q tests/test_backtest_router.py

Asserts the endpoint returns the engine's ``to_dict()`` shape: the headline
keys are present and the headline numbers are finite.
"""

from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.integration

# A liquid large-cap golden ticker with reliable Yahoo daily history.
TICKER = "RELIANCE.NS"

# The headline metric keys Backtest Lab renders — all must be present.
_REQUIRED_KEYS = {
    "ticker",
    "interval",
    "total_predictions",
    "direction_accuracy",
    "signal_accuracy",
    "total_return_pct",
    "sharpe_ratio",
    "max_drawdown_pct",
    "results",
}

# Numeric headline metrics that must be finite (not NaN/inf).
_FINITE_KEYS = (
    "direction_accuracy",
    "signal_accuracy",
    "total_return_pct",
    "sharpe_ratio",
    "max_drawdown_pct",
)


@pytest.fixture(scope="module")
def client():
    """ONE TestClient for the module (single event loop — see fusion test)."""
    from fastapi.testclient import TestClient

    from backend.app.main import app

    with TestClient(app) as c:
        yield c


def test_backtest_returns_report_shape(client):
    from tests.integration_helpers import yfinance_up

    if not yfinance_up():
        pytest.skip("yfinance/Yahoo unreachable — live data required for a backtest")

    resp = client.get(f"/api/backtest/{TICKER}")

    # A thin/rate-limited window yields 404 ("not enough data") — skip, not fail,
    # because that is a live-data condition, not a code defect.
    if resp.status_code == 404:
        pytest.skip(f"not enough live data for {TICKER}: {resp.json().get('detail')}")

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Shape: every headline key the UI renders is present.
    assert _REQUIRED_KEYS.issubset(body.keys()), sorted(_REQUIRED_KEYS - body.keys())

    assert body["ticker"] == TICKER
    assert body["interval"] == "1d"
    assert isinstance(body["total_predictions"], int)
    assert isinstance(body["results"], list)

    # Numbers: the headline metrics are finite.
    for key in _FINITE_KEYS:
        assert isinstance(body[key], (int, float)), key
        assert math.isfinite(body[key]), key
