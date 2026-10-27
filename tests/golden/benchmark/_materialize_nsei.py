"""One-off materialization of the committed Nifty benchmark golden fixture.

Writes ``tests/golden/benchmark/NSEI_1d.parquet`` — the ``^NSEI`` close series
over the golden window (G6 deliverable 1, benchmark lane).

WHY A SEPARATE SUBDIRECTORY (`tests/golden/benchmark/`), not `tests/golden/`:
the offline suite globs ``tests/golden/*_1d.parquet`` NON-recursively and
(a) ``tests/test_gate_g3.py`` asserts EXACTLY 10 files, and
(b) ``tests/test_golden_loader.py`` asserts every matched file is a RAW OHLCV
    STOCK frame. Dropping ``_NSEI_1d.parquet`` beside the ten stock goldens
    would break both. A subdirectory is invisible to a non-recursive glob, so
    the ten-file / all-stock invariants stay true and the benchmark fixture
    lives next to the goldens it aligns with.

REPRODUCIBILITY: the golden window is 2026-04..2026-10 (a FUTURE window — the
goldens are synthetic point-in-time frames), so a live ``^NSEI`` fetch returns
EMPTY. This script tries the live fetch first (per the deliverable's recipe);
when it comes back empty it synthesizes a DETERMINISTIC ^NSEI close series
aligned to the exact union of golden trading dates (fixed seed, no wall-clock),
normalized to the SAME shape as the stock goldens
(``["Open","High","Low","Close","Volume"]`` float64, tz-naive DatetimeIndex
named "Datetime"). The committed parquet is what every run reads, so the
benchmark lane is byte-reproducible regardless of how the file was produced.

Run with the venv python (from the repo root)::

    .venv/Scripts/python.exe tests/golden/benchmark/_materialize_nsei.py
"""

from __future__ import annotations

import glob
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
GOLDEN_DIR = HERE.parent
OUT = HERE / "NSEI_1d.parquet"
OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def _golden_date_union() -> pd.DatetimeIndex:
    """The sorted union of trading dates across all ten stock goldens."""
    dates: set = set()
    for path in sorted(GOLDEN_DIR.glob("*_1d.parquet")):
        df = pd.read_parquet(path, engine="pyarrow")
        dates.update(df.index.tolist())
    idx = pd.DatetimeIndex(sorted(dates), name="Datetime")
    return idx


def _try_live() -> pd.DataFrame | None:
    try:
        import yfinance as yf

        hist = yf.Ticker("^NSEI").history(
            start="2026-04-01", end="2026-10-22", interval="1d"
        )
    except Exception as exc:  # noqa: BLE001 - offline / future window -> synth
        print(f"[materialize] live fetch failed ({exc!r}); using deterministic synth")
        return None
    if hist is None or hist.empty:
        print("[materialize] live fetch returned EMPTY (future window); using synth")
        return None
    print(f"[materialize] live fetch returned {len(hist)} rows")
    if getattr(hist.index, "tz", None) is not None:
        hist.index = hist.index.tz_localize(None)
    hist.index.name = "Datetime"
    return hist[OHLCV].astype("float64")


def _synth(idx: pd.DatetimeIndex) -> pd.DataFrame:
    """Deterministic ^NSEI-like close series aligned to the golden dates.

    Fixed seed, no wall-clock. A gentle upward drift around a Nifty-scale level
    (~22000) with small bounded noise so the benchmark lane has a realistic,
    reproducible buy-and-hold move over the window.
    """
    n = len(idx)
    rng = np.random.RandomState(20260406)  # fixed: window start as the seed
    close = np.empty(n)
    level = 22000.0
    for i in range(n):
        level = level * (1 + 0.0007) + rng.uniform(-40.0, 40.0)
        close[i] = level
    openp = close * (1 - 0.0005)
    high = np.maximum(openp, close) * (1 + 0.001)
    low = np.minimum(openp, close) * (1 - 0.001)
    vol = np.full(n, 0.0)  # indices carry no meaningful volume; keep it float64
    df = pd.DataFrame(
        {"Open": openp, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )
    df.index.name = "Datetime"
    return df[OHLCV].astype("float64")


def main() -> None:
    if not sorted(GOLDEN_DIR.glob("*_1d.parquet")):
        raise SystemExit("no stock goldens found to align the benchmark against")

    live = _try_live()
    if live is not None and len(live) >= 60:
        df = live
    else:
        idx = _golden_date_union()
        df = _synth(idx)
        print(f"[materialize] synthesized {len(df)} rows over {idx.min()}..{idx.max()}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, engine="pyarrow")

    back = pd.read_parquet(OUT, engine="pyarrow")
    assert list(back.columns) == OHLCV, back.columns
    assert len(back) >= 60, f"only {len(back)} rows"
    assert back.index.tz is None
    assert back.index.name == "Datetime"
    print(f"[materialize] wrote {OUT} with {len(back)} rows -> OK")


if __name__ == "__main__":
    main()
