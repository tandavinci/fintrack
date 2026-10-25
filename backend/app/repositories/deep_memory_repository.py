"""Deep-memory repository (Step 4 / G4 memory-log PostgreSQL mirror).

Async upsert + sync variant for ``deep_memory``. One ``load_entries()`` dict
maps to one row via :func:`_row_values`; the upsert uses Postgres
``INSERT ... ON CONFLICT (ticker, trade_date) DO UPDATE`` so re-mirroring the
same entry overwrites it in place (idempotent). When a pending entry later
settles the SAME row updates: ``pending`` flips False and
``resolved_date``/``reflection`` fill in.

The input is the exact shape TradingAgents' own
``TradingMemoryLog.load_entries()`` returns — never reparsed here. Date strings
(``date``, ``resolved``) are parsed to ``datetime.date``; an empty/invalid
``resolved`` becomes ``None``.

Session lifecycle mirrors ``recommendation_repository``: an injected session is
reused and left open; a session the repo opens itself is closed in ``finally``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.app.database import async_session_factory, create_sync_session
from backend.app.models.deep_memory import DeepMemoryEntry

# Columns updated on conflict (identity columns ticker/trade_date excluded, and
# created_at left untouched so the original insert time survives an update).
_UPSERT_UPDATE_COLS = (
    "rating",
    "pending",
    "raw_return",
    "alpha_return",
    "holding_days",
    "resolved_date",
    "decision",
    "reflection",
)


def _parse_date(value) -> Optional[date]:
    """Parse a ``YYYY-MM-DD`` string to a date; empty/invalid -> None.

    Accepts an already-parsed ``date`` unchanged (defensive — the parser emits
    strings, but a caller passing a date should not break the mapping).
    """
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _row_values(entry: dict) -> dict:
    """Map ONE ``load_entries()`` dict to a ``deep_memory`` column dict.

    Date strings are parsed; ``resolved`` -> ``resolved_date`` (None when
    absent/invalid). ``decision``/``reflection`` default to '' so the NOT NULL
    columns always have a value.
    """
    return {
        "ticker": entry["ticker"],
        "trade_date": _parse_date(entry["date"]),
        "rating": entry.get("rating"),
        "pending": bool(entry.get("pending")),
        "raw_return": entry.get("raw"),
        "alpha_return": entry.get("alpha"),
        "holding_days": entry.get("holding"),
        "resolved_date": _parse_date(entry.get("resolved")),
        "decision": entry.get("decision") or "",
        "reflection": entry.get("reflection") or "",
    }


def _upsert_statement(entry: dict):
    """Build the ON CONFLICT DO UPDATE statement for one memory entry."""
    stmt = pg_insert(DeepMemoryEntry).values(_row_values(entry))
    set_ = {col: getattr(stmt.excluded, col) for col in _UPSERT_UPDATE_COLS}
    set_["updated_at"] = func.now()
    return stmt.on_conflict_do_update(
        index_elements=["ticker", "trade_date"],
        set_=set_,
    )


class DeepMemoryRepository:
    """Repository for ``deep_memory`` DB access (async core)."""

    def __init__(self, session: Optional[AsyncSession] = None):
        self._session = session

    def _get_session(self) -> AsyncSession:
        return self._session if self._session is not None else async_session_factory()

    async def upsert_entry(self, entry: dict) -> None:
        """Upsert ONE parsed memory dict keyed on (ticker, trade_date)."""
        session = self._get_session()
        owns = self._session is None
        try:
            await session.execute(_upsert_statement(entry))
            await session.commit()
        finally:
            if owns:
                await session.close()

    async def upsert_entries(self, entries: list[dict]) -> int:
        """Upsert each entry (via the per-entry primitive); return the count."""
        count = 0
        for entry in entries:
            await self.upsert_entry(entry)
            count += 1
        return count


class SyncDeepMemoryRepository:
    """Synchronous variant of :class:`DeepMemoryRepository`."""

    def __init__(self, session: Optional[Session] = None):
        self._session = session

    def _get_session(self) -> Session:
        return self._session if self._session is not None else create_sync_session()

    def upsert_entry(self, entry: dict) -> None:
        """Upsert ONE parsed memory dict keyed on (ticker, trade_date)."""
        session = self._get_session()
        owns = self._session is None
        try:
            session.execute(_upsert_statement(entry))
            session.commit()
        finally:
            if owns:
                session.close()

    def upsert_entries(self, entries: list[dict]) -> int:
        """Upsert each entry (via the per-entry primitive); return the count."""
        count = 0
        for entry in entries:
            self.upsert_entry(entry)
            count += 1
        return count
