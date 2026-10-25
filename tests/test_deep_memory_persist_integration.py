"""Real-infra integration test for the deep-memory mirror (Step 4 / G4).

Requires Postgres (migrated to >= 004). Skips-with-reason (never fails) when
Postgres is down, mirroring the rest of the integration suite. Run with the
stack up and migrated:

    docker compose -f infra/docker-compose.yml up -d
    .venv\\Scripts\\python.exe -m alembic -c backend/alembic.ini upgrade head
    .venv\\Scripts\\python.exe -m pytest -m integration -q tests/test_deep_memory_persist_integration.py

Asserts the mirror lands one row with the right columns, then that re-mirroring
an UPDATED (settled) entry updates the SAME row in place (idempotent — one row,
pending flips False, resolved_date/reflection fill in).
"""

from __future__ import annotations

import os
from datetime import date

import pytest
from sqlalchemy import delete, select
from tradingagents.memory.log import TradingMemoryLog

from backend.app.database import create_sync_session
from backend.app.models.deep_memory import DeepMemoryEntry
from backend.app.services.deep_memory_service import mirror_memory_for_ticker

from tests.integration_helpers import require_postgres  # noqa: F401 (fixture)

pytestmark = pytest.mark.integration

TICKER = "ZZTEST.NS"  # synthetic — never collides with a real golden ticker
TRADE_DATE = "2024-01-15"


@pytest.fixture
def clean_deep_memory():
    """Delete deep_memory rows for the synthetic ticker before and after."""

    def _clean():
        session = create_sync_session()
        try:
            session.execute(delete(DeepMemoryEntry).where(DeepMemoryEntry.ticker == TICKER))
            session.commit()
        finally:
            session.close()

    _clean()
    yield _clean
    _clean()


def _row(session):
    return session.execute(
        select(DeepMemoryEntry).where(
            DeepMemoryEntry.ticker == TICKER,
            DeepMemoryEntry.trade_date == date(2024, 1, 15),
        )
    ).scalar_one()


def _all_rows(session):
    return session.execute(
        select(DeepMemoryEntry).where(DeepMemoryEntry.ticker == TICKER)
    ).scalars().all()


def test_mirror_lands_row_then_settles_in_place_idempotent(
    require_postgres, clean_deep_memory, tmp_path
):
    path = os.path.join(str(tmp_path), "trading_memory.md")
    log = TradingMemoryLog({"memory_log_path": path})

    # 1) Pending decision -> mirror lands one row.
    log.store_decision(TICKER, TRADE_DATE, "Buy ZZTEST now.", "Buy")
    assert mirror_memory_for_ticker(path, TICKER) == 1

    session = create_sync_session()
    try:
        row = _row(session)
        assert row.rating == "Buy"
        assert row.pending is True
        assert row.resolved_date is None
        assert row.reflection == ""
        assert row.decision == "Buy ZZTEST now."
        assert len(_all_rows(session)) == 1
        original_id = row.id
    finally:
        session.close()

    # 2) Settle the SAME entry, re-mirror -> the SAME row updates in place.
    log.update_with_outcome(
        TICKER,
        TRADE_DATE,
        raw_return=12.5,
        alpha_return=3.1,
        holding_days=20,
        reflection="Worked out as expected.",
        resolution_date="2024-02-14",
    )
    assert mirror_memory_for_ticker(path, TICKER) == 1

    session = create_sync_session()
    try:
        rows = _all_rows(session)
        assert len(rows) == 1, "re-mirroring a settled entry must UPSERT, not duplicate"
        row = rows[0]
        assert row.id == original_id, "same row updated in place (PK unchanged)"
        assert row.pending is False
        assert row.resolved_date == date(2024, 2, 14)
        assert row.reflection == "Worked out as expected."
        assert row.raw_return is not None and row.alpha_return is not None
        assert row.holding_days is not None
    finally:
        session.close()
