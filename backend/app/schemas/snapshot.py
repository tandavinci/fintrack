"""Snapshot identity for the shared Recommendation contract.

The snapshot_id is a deterministic fingerprint of the exact data an engine
saw when it produced a Recommendation. Both the quant and deep lanes compute
it the same way so two recommendations over the same data share one id, and a
recommendation can be traced back to the rows that justified it.

Determinism rules:
- Only the last ``SNAPSHOT_TAIL_N`` rows are considered, in their existing
  (oldest -> newest) order.
- Only a fixed, sorted set of indicator columns participates, intersected with
  the columns actually present on the frame, so the id is stable across
  frames that carry extra scratch columns.
- Every cell is rendered as a fixed-precision float; missing values render as
  the literal string ``nan`` so NaN/None collapse to one representation.
"""

import hashlib

SNAPSHOT_TAIL_N = 60

# Fixed, sorted canonical column set. Only columns present on the frame are used.
_CANONICAL_COLUMNS = [
    "ATR",
    "BB_Lower",
    "BB_Middle",
    "BB_Upper",
    "BB_Width",
    "Close",
    "EMA_12",
    "EMA_26",
    "High",
    "Low",
    "MACD",
    "MACD_Histogram",
    "MACD_Signal",
    "Open",
    "RSI",
    "SMA_20",
    "SMA_50",
    "Volume",
    "Volume_Ratio",
    "Volume_SMA",
]


def _render_cell(value) -> str:
    """Render one cell as fixed-precision float, or ``nan`` for missing data."""
    if value is None:
        return "nan"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "nan"
    if f != f:  # NaN
        return "nan"
    return f"{f:.6f}"


def compute_snapshot_id(ticker, as_of, df) -> str:
    """Return a 16-char sha256 digest of the tail of ``df`` for ``ticker``.

    ``as_of`` is a ``date``/``datetime`` (anything with ``isoformat``). ``df`` is
    a pandas DataFrame; only its last ``SNAPSHOT_TAIL_N`` rows and the canonical
    columns present on it participate.
    """
    tail = df.tail(SNAPSHOT_TAIL_N)
    cols = [c for c in _CANONICAL_COLUMNS if c in tail.columns]

    lines = [f"{ticker}|{as_of.isoformat()}|{','.join(cols)}"]
    for _, row in tail.iterrows():
        lines.append(",".join(_render_cell(row[c]) for c in cols))

    canonical = "\n".join(lines)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
