"""Real-infra integration test for the paper-trading DB lifecycle (G6 deliverable 4).

Requires Postgres (migrated to head >= 007). Skips-with-reason (never fails) when
Postgres is down, mirroring the rest of the integration suite. Run with the stack
up and migrated:

    docker compose -f infra/docker-compose.yml up -d
    .venv\\Scripts\\python.exe -m alembic -c backend/alembic.ini upgrade head
    .venv\\Scripts\\python.exe -m pytest -m integration -q tests/test_paper_trading_integration.py

Exercises the ``paper_positions`` round-trip through
``SyncPaperPositionRepository`` directly (no live yfinance needed): OPEN is
idempotent on (ticker, source, opened_as_of); MARK updates the unrealized P&L in
place; CLOSE realizes it; and the quant/deep books are independent (the dual
book). Prices are supplied by the test, so the assertions are deterministic.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select

from backend.app.database import create_sync_session
from backend.app.models.paper_position import PAPER_NOTIONAL, PaperPosition
from backend.app.repositories.paper_position_repository import (
    SyncPaperPositionRepository,
)
from backend.app.services.paper_trading_service import paper_return_pct

from tests.integration_helpers import require_postgres  # noqa: F401 (fixture)

pytestmark = pytest.mark.integration

TEST_TICKER = "PAPERTEST.NS"


@pytest.fixture
def clean_paper_positions():
    """Delete paper_positions rows for the test ticker before and after."""

    def _clean():
        session = create_sync_session()
        try:
            session.execute(
                delete(PaperPosition).where(PaperPosition.ticker == TEST_TICKER)
            )
            session.commit()
        finally:
            session.close()

    _clean()
    yield _clean
    _clean()


def test_open_mark_close_lifecycle(require_postgres, clean_paper_positions):
    """A position opens, marks to market, then closes — all persisted in place."""
    repo = SyncPaperPositionRepository()
    opened = date.today() - timedelta(days=5)
    entry = 100.0

    # OPEN
    assert repo.open_position(
        ticker=TEST_TICKER, source="quant", opened_as_of=opened, entry_price=entry
    ) is True

    pos = repo.get_open(TEST_TICKER, "quant")
    assert pos is not None
    assert pos.status == "open"
    assert pos.entry_price == pytest.approx(entry)
    assert pos.quantity == pytest.approx(PAPER_NOTIONAL / entry)
    assert pos.notional == pytest.approx(PAPER_NOTIONAL)

    # MARK (price up 10%)
    mark_date = opened + timedelta(days=3)
    mark_price = 110.0
    repo.mark_open(
        pos.id,
        mark_date=mark_date,
        mark_price=mark_price,
        unrealized_pnl_pct=paper_return_pct(entry, mark_price),
    )
    marked = repo.get_open(TEST_TICKER, "quant")
    assert marked.last_mark_price == pytest.approx(110.0)
    assert marked.unrealized_pnl_pct == pytest.approx(0.10)
    assert marked.status == "open"  # still open after a mark

    # CLOSE (sell signal, price up 15% from entry)
    close_date = opened + timedelta(days=5)
    exit_price = 115.0
    repo.close_position(
        pos.id,
        closed_as_of=close_date,
        exit_price=exit_price,
        realized_pnl_pct=paper_return_pct(entry, exit_price),
        close_reason="sell_signal",
    )

    session = create_sync_session()
    try:
        row = session.execute(
            select(PaperPosition).where(PaperPosition.id == pos.id)
        ).scalar_one()
        assert row.status == "closed"
        assert row.closed_as_of == close_date
        assert row.exit_price == pytest.approx(115.0)
        assert row.realized_pnl_pct == pytest.approx(0.15)
        assert row.close_reason == "sell_signal"
    finally:
        session.close()

    # No OPEN position remains for (ticker, source).
    assert repo.get_open(TEST_TICKER, "quant") is None


def test_open_is_idempotent_on_business_key(require_postgres, clean_paper_positions):
    """Re-opening the same (ticker, source, opened_as_of) is a no-op, not a dup."""
    repo = SyncPaperPositionRepository()
    opened = date.today() - timedelta(days=2)

    first = repo.open_position(
        ticker=TEST_TICKER, source="quant", opened_as_of=opened, entry_price=100.0
    )
    second = repo.open_position(
        ticker=TEST_TICKER, source="quant", opened_as_of=opened, entry_price=999.0
    )
    assert first is True
    assert second is False  # conflict -> no new row

    rows = repo.list_by_source("quant", status="open")
    mine = [r for r in rows if r.ticker == TEST_TICKER]
    assert len(mine) == 1
    # The first insert's entry price is preserved (DO NOTHING, not overwrite).
    assert mine[0].entry_price == pytest.approx(100.0)


def test_quant_and_deep_books_are_independent(require_postgres, clean_paper_positions):
    """The dual book: the same ticker holds a separate position per source."""
    repo = SyncPaperPositionRepository()
    opened = date.today() - timedelta(days=1)

    assert repo.open_position(
        ticker=TEST_TICKER, source="quant", opened_as_of=opened, entry_price=100.0
    ) is True
    assert repo.open_position(
        ticker=TEST_TICKER, source="deep", opened_as_of=opened, entry_price=200.0
    ) is True

    quant_pos = repo.get_open(TEST_TICKER, "quant")
    deep_pos = repo.get_open(TEST_TICKER, "deep")
    assert quant_pos is not None and deep_pos is not None
    assert quant_pos.id != deep_pos.id
    assert quant_pos.entry_price == pytest.approx(100.0)
    assert deep_pos.entry_price == pytest.approx(200.0)
