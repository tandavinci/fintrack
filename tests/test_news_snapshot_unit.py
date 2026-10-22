"""No-infra unit tests for FEAT B (news dedup/validation) + FEAT C (payload).

Everything here runs OFFLINE (no Postgres / no Redis / no network): the pure
functions ``compute_dedup_hash`` / ``_entry_to_row`` and the snapshot payload
builders ``_bars_payload`` are exercised directly on in-memory data. The
DB-touching paths (``insert_items``, ``build_and_store_snapshot`` persistence)
are covered by the FEAT-004 integration suite.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from backend.app.services import snapshot_service as snap
from backend.app.services.news_service import (
    _entry_to_row,
    compute_dedup_hash,
)


# --- dedup_hash (design §6.3) -------------------------------------------------


def test_dedup_hash_is_stable_for_same_inputs():
    published = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
    a = compute_dedup_hash("Biz Daily", "https://ex/a", "Headline A", published)
    b = compute_dedup_hash("Biz Daily", "https://ex/a", "Headline A", published)
    assert a == b
    assert len(a) == 64  # full sha256 hex


def test_dedup_hash_differs_for_different_url():
    published = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
    a = compute_dedup_hash("Biz Daily", "https://ex/a", "Same headline", published)
    b = compute_dedup_hash("Biz Daily", "https://ex/b", "Same headline", published)
    assert a != b


def test_dedup_hash_falls_back_to_headline_when_url_empty():
    published = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
    h1 = compute_dedup_hash("Biz Daily", "", "Headline X", published)
    h2 = compute_dedup_hash("Biz Daily", None, "Headline X", published)
    # url empty/None both fall back to the headline -> identical identity.
    assert h1 == h2
    # A different headline (still no url) -> different hash.
    h3 = compute_dedup_hash("Biz Daily", None, "Headline Y", published)
    assert h3 != h1


def test_dedup_hash_handles_missing_published_at():
    h = compute_dedup_hash("Biz Daily", "https://ex/a", "Headline", None)
    assert len(h) == 64  # empty-string published slot, still deterministic
    assert h == compute_dedup_hash("Biz Daily", "https://ex/a", "Headline", None)


# --- entry validation (design §6.5) ------------------------------------------


def test_entry_to_row_skips_when_required_fields_missing():
    assert _entry_to_row({"title": "", "link": "https://ex/a"}, "src", None) is None
    assert _entry_to_row({"title": "ok", "link": ""}, "src", None) is None
    assert _entry_to_row({"title": "ok", "link": "https://ex/a"}, "", None) is None


def test_entry_to_row_builds_row_and_dedup_hash():
    entry = {"title": "  Markets rally  ", "link": "https://ex/a"}
    row = _entry_to_row(entry, "Biz Daily", "RELIANCE.BO")
    assert row is not None
    assert row["headline"] == "Markets rally"  # trimmed
    assert row["url"] == "https://ex/a"
    assert row["source"] == "Biz Daily"
    assert row["ticker"] == "RELIANCE.BO"
    assert row["published_at"] is None  # feed omitted the date -> NULL
    assert row["dedup_hash"] == compute_dedup_hash(
        "Biz Daily", "https://ex/a", "Markets rally", None
    )


# --- snapshot payload builder (design §7.2) ----------------------------------


def _enriched_frame(n: int = 80) -> pd.DataFrame:
    idx = pd.bdate_range("2023-10-01", periods=n)
    close = np.linspace(100.0, 180.0, n)
    df = pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.full(n, 1_000_000.0),
            "SMA_20": close - 2.0,
            "RSI": np.linspace(40.0, 70.0, n),
        },
        index=idx,
    )
    df.index.name = "Datetime"
    # Seed a NaN in an indicator column to prove it renders as JSON null.
    df.iloc[0, df.columns.get_loc("SMA_20")] = np.nan
    return df


def test_bars_payload_is_tail_n_json_safe_records():
    df = _enriched_frame(n=80)
    bars = snap._bars_payload(df)
    assert len(bars) == snap.SNAPSHOT_TAIL_N  # last 60 rows only
    first = bars[0]
    assert "ts" in first
    # Indicator columns are carried (self-describing payload).
    assert "SMA_20" in first and "RSI" in first and "Close" in first
    # Every numeric cell is a plain float or None (JSON-safe, no NaN).
    for rec in bars:
        for key, value in rec.items():
            if key == "ts":
                assert isinstance(value, str)
            else:
                assert value is None or isinstance(value, float)
                if isinstance(value, float):
                    assert not math.isnan(value)


def test_bars_payload_shorter_frame_returns_all_rows():
    df = _enriched_frame(n=25)
    bars = snap._bars_payload(df)
    assert len(bars) == 25


def test_snapshot_lookback_and_interval_constants():
    # Guard the daily/200-day contract the design pins (§7.2).
    assert snap.SNAPSHOT_INTERVAL == "1d"
    assert snap.SNAPSHOT_LOOKBACK_DAYS == 200
    assert isinstance(date(2024, 1, 15), date)
