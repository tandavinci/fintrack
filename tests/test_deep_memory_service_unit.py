"""Offline unit tests for the deep-memory mirror service + repo mapping (G4).

No DB / no Ollama / no graph. A tiny markdown memory file is written with
TradingAgents' OWN ``TradingMemoryLog.store_decision`` (so the fixture is in the
exact on-disk format the real parser reads), then
``SyncDeepMemoryRepository.upsert_entries`` is monkeypatched to CAPTURE what the
service hands it. We assert:

  * ``mirror_memory_for_ticker(path, "A")`` passes exactly the 2 A-entries
    (parsed ``load_entries()`` dicts), ``mirror_all`` passes all 3.
  * the repo's ``_row_values`` mapping parses ``date``->``trade_date`` and
    ``resolved`` (present / absent) correctly.
"""

from __future__ import annotations

import os
from datetime import date

from tradingagents.memory.log import TradingMemoryLog

from backend.app.repositories import deep_memory_repository
from backend.app.repositories.deep_memory_repository import _parse_date, _row_values
from backend.app.services import deep_memory_service

A = "AAA.NS"
B = "BBB.NS"


def _write_memory(tmp_path) -> str:
    """Write a 3-entry markdown log (2x A, 1x B) via TradingAgents' own writer."""
    path = os.path.join(str(tmp_path), "trading_memory.md")
    log = TradingMemoryLog({"memory_log_path": path})
    log.store_decision(A, "2024-01-15", "Buy A now.", "Buy")
    log.store_decision(A, "2024-02-15", "Hold A.", "Hold")
    log.store_decision(B, "2024-01-20", "Sell B.", "Sell")
    return path


def test_mirror_for_ticker_passes_only_that_tickers_entries(tmp_path, monkeypatch):
    path = _write_memory(tmp_path)

    captured: list[list[dict]] = []

    def fake_upsert(self, entries):
        captured.append(entries)
        return len(entries)

    monkeypatch.setattr(
        deep_memory_repository.SyncDeepMemoryRepository, "upsert_entries", fake_upsert
    )

    count = deep_memory_service.mirror_memory_for_ticker(path, A)

    assert count == 2
    assert len(captured) == 1
    handed = captured[0]
    assert [e["ticker"] for e in handed] == [A, A]
    assert {e["date"] for e in handed} == {"2024-01-15", "2024-02-15"}
    assert all(e["pending"] is True for e in handed)
    assert handed[0]["decision"] == "Buy A now."


def test_mirror_all_passes_every_entry(tmp_path, monkeypatch):
    path = _write_memory(tmp_path)

    captured: list[list[dict]] = []
    monkeypatch.setattr(
        deep_memory_repository.SyncDeepMemoryRepository,
        "upsert_entries",
        lambda self, entries: captured.append(entries) or len(entries),
    )

    count = deep_memory_service.mirror_all(path)

    assert count == 3
    handed = captured[0]
    assert sorted(e["ticker"] for e in handed) == [A, A, B]


def test_mirror_for_missing_ticker_is_zero_and_no_db_call(tmp_path, monkeypatch):
    path = _write_memory(tmp_path)

    def fail(self, entries):  # pragma: no cover - must not run
        raise AssertionError("upsert must not run when there are no entries")

    monkeypatch.setattr(
        deep_memory_repository.SyncDeepMemoryRepository, "upsert_entries", fail
    )

    assert deep_memory_service.mirror_memory_for_ticker(path, "ZZZ.NS") == 0


# --------------------------------------------------------------------------- #
# repo mapping: date parsing + resolved handling                               #
# --------------------------------------------------------------------------- #


def test_row_values_parses_dates_and_pending():
    entry = {
        "date": "2024-01-15",
        "ticker": A,
        "rating": "Buy",
        "pending": True,
        "raw": None,
        "alpha": None,
        "holding": None,
        "resolved": None,
        "decision": "Buy A.",
        "reflection": "",
    }
    row = _row_values(entry)
    assert row["trade_date"] == date(2024, 1, 15)
    assert row["resolved_date"] is None
    assert row["pending"] is True
    assert row["rating"] == "Buy"
    assert row["decision"] == "Buy A."
    assert row["reflection"] == ""


def test_row_values_settled_entry_maps_resolved_and_display_strings():
    entry = {
        "date": "2024-01-15",
        "ticker": A,
        "rating": "Buy",
        "pending": False,
        "raw": "+12.5%",
        "alpha": "+3.1%",
        "holding": "20d",
        "resolved": "2024-02-14",
        "decision": "Buy A.",
        "reflection": "Worked out.",
    }
    row = _row_values(entry)
    assert row["pending"] is False
    assert row["resolved_date"] == date(2024, 2, 14)
    assert row["raw_return"] == "+12.5%"
    assert row["alpha_return"] == "+3.1%"
    assert row["holding_days"] == "20d"
    assert row["reflection"] == "Worked out."


def test_parse_date_handles_empty_and_invalid():
    assert _parse_date(None) is None
    assert _parse_date("") is None
    assert _parse_date("   ") is None
    assert _parse_date("not-a-date") is None
    assert _parse_date("2024-03-01") == date(2024, 3, 1)
    assert _parse_date(date(2024, 3, 1)) == date(2024, 3, 1)
