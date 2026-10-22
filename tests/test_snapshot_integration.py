"""Persisted-snapshot idempotency integration test (design §9.2 #5).

``build_and_store_snapshot(t, as_of)`` called twice must return the SAME
``snapshot_id``, leave exactly ONE ``data_snapshots`` row, and the returned
payload must round-trip against the stored row (modulo JSON numeric
normalization).

Requires Postgres + Redis + live yfinance (the snapshot is built through the
real ``DataPipeline`` -> ``MarketDataService`` path). Skips-with-reason when a
source is unreachable.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import delete, select

from backend.app.database import create_sync_session
from backend.app.models.data_snapshot import DataSnapshot
from backend.app.services.snapshot_service import build_and_store_snapshot
from backend.app.services.ticker_resolution import resolve_market_ticker

from tests.integration_helpers import (  # noqa: F401
    TRADING_DAY_AS_OF,
    count_snapshots,
    require_postgres,
    require_redis,
    require_yfinance,
)

pytestmark = pytest.mark.integration

TICKER = "RELIANCE.NS"


@pytest.fixture
def clean_snapshot_rows():
    """Delete any data_snapshots rows for the test ticker before and after."""
    resolved = resolve_market_ticker(TICKER)

    def _clean():
        session = create_sync_session()
        try:
            session.execute(
                delete(DataSnapshot).where(DataSnapshot.ticker.in_([TICKER, resolved]))
            )
            session.commit()
        finally:
            session.close()

    _clean()
    yield
    _clean()


def test_snapshot_build_is_idempotent(
    require_postgres, require_redis, require_yfinance, clean_snapshot_rows
):
    """Two builds -> same id, one row, payload round-trips with the stored row."""
    id1, payload1 = build_and_store_snapshot(TICKER, TRADING_DAY_AS_OF)
    id2, payload2 = build_and_store_snapshot(TICKER, TRADING_DAY_AS_OF)

    assert id1 == id2, "a repeat build must reproduce the same snapshot_id"
    assert count_snapshots(id1) == 1, "exactly one data_snapshots row must exist"

    # Second call returns the STORED payload unchanged (idempotent read path).
    assert payload2 == payload1

    # Payload round-trips against the DB row modulo JSON numeric normalization.
    session = create_sync_session()
    try:
        row = session.execute(
            select(DataSnapshot).where(DataSnapshot.snapshot_id == id1)
        ).scalar_one()
    finally:
        session.close()

    assert json.loads(json.dumps(row.payload)) == json.loads(json.dumps(payload1))
    assert row.payload["interval"] == "1d"
    assert row.payload["snapshot_tail_n"] == 60
    assert row.payload["as_of"] == TRADING_DAY_AS_OF.isoformat()
