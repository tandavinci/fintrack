"""Deep-memory mirror service (Step 4 / G4 memory-log PostgreSQL mirror).

Copies TradingAgents' OWN markdown memory log into the ``deep_memory`` table
using TradingAgents' OWN parser (``TradingMemoryLog.load_entries()``) — the
markdown stays the backup export and its source is never edited (STEERING
rule 2). We never reparse the markdown ourselves.

``mirror_memory_for_ticker`` is the write-as-you-go primitive: after ONE
ticker's deep run the caller mirrors ONLY that ticker's entries (cheap — a few
rows), not the whole log. It is SYNCHRONOUS (the deep lane is a sync callable
driven by the Celery ``deep`` worker; STEERING rule 6).

Errors are allowed to RAISE here so a unit test can assert behavior directly;
the CALLER (``deep_service.persist_deep_recommendation``) wraps the call in
try/except so a mirror failure never fails the recommendation.
"""

from __future__ import annotations

import logging

from tradingagents.memory.log import TradingMemoryLog

from backend.app.repositories.deep_memory_repository import SyncDeepMemoryRepository

logger = logging.getLogger(__name__)


def _load_entries(memory_log_path: str) -> list[dict]:
    """Load all entries from the markdown log via TradingAgents' own parser."""
    log = TradingMemoryLog({"memory_log_path": memory_log_path})
    return log.load_entries()


def mirror_memory_for_ticker(memory_log_path: str, ticker: str) -> int:
    """Mirror ONLY ``ticker``'s entries from the markdown log into ``deep_memory``.

    Write-as-you-go: called right after one ticker's deep run, so it upserts just
    that ticker's handful of rows. Returns the number of entries mirrored. Lets
    exceptions propagate — the caller decides to isolate them.
    """
    entries = [e for e in _load_entries(memory_log_path) if e.get("ticker") == ticker]
    if not entries:
        return 0
    return SyncDeepMemoryRepository().upsert_entries(entries)


def mirror_all(memory_log_path: str) -> int:
    """Mirror EVERY entry from the markdown log (full backfill / settlement pass).

    Use this for a periodic resync — e.g. after TradingAgents settles pending
    entries in a batch — so the DB mirror catches every updated row. Returns the
    number of entries mirrored.
    """
    entries = _load_entries(memory_log_path)
    if not entries:
        return 0
    return SyncDeepMemoryRepository().upsert_entries(entries)
