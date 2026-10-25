"""Provenance cross-check for the deep lane (G4 design — data agreement gate).

When the real TradingAgents graph is wired in (G4), it fetches its OWN price
history (a ~5-year, dividend/split-ADJUSTED Yahoo series cached to CSV), which
is a SEPARATE fetch from the project's own snapshot (a ~200-day series through
MarketDataService). Two independent fetches of the same stock can legitimately
disagree in their ABSOLUTE prices — most importantly because one may be
split/dividend *adjusted* and the other not. That is normal, not an error.

So we do NOT compare absolute prices. We compare DAY-TO-DAY PERCENT MOVES
(ratios): a split or dividend rescales every price but leaves the percent change
between consecutive days (almost) unchanged, so comparing on returns cancels the
adjusted-vs-raw offset out.

Two measures, with different jobs (see the G4 discussion):

  1. The GATE (pass/fail) — ``max_abs_ratio_diff``: the worst single day's gap
     between the two series' percent moves, over the overlapping dates. If the
     worst day exceeds ``RATIO_TOLERANCE`` the two sources genuinely disagree
     about the SHAPE of the move -> PROVENANCE_MISMATCH. This is the decision.

  2. The HEALTH SIGNAL (reported, not blocking) — ``ratio_diff_variance``: how
     scattered the day-by-day gaps are. Low variance = the two sources track
     each other tightly (high trust); a spike = one jittery day worth a look.
     Variance ALONE cannot be the gate: a steady constant offset has ~zero
     variance yet is a real disagreement, so variance is a companion to the
     max-gap gate, never a replacement. Here it is computed and returned for
     confidence/telemetry; thresholds are deferred until we see real data.

Everything here is PURE: no network, no I/O, no global state. Same inputs ->
same result. That keeps it fully offline-testable and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass

# Gate tolerance: the worst-day gap between the two series' day-to-day percent
# moves, as a FRACTION (0.01 == 1 percentage point of return difference). Tight
# by design — once we compare on returns, the adjusted/raw offset is gone, so a
# faithful second fetch should track within a small tolerance. Revisit on real
# data (the design's "confirm on real data" item).
RATIO_TOLERANCE = 0.01

# Minimum overlapping days needed to judge agreement. Fewer than this and we
# cannot responsibly claim the sources agree -> treated as a mismatch by the
# caller (uncertainty is not a pass).
MIN_OVERLAP_DAYS = 5


@dataclass(frozen=True)
class ProvenanceResult:
    """The outcome of comparing two close-price series on their percent moves.

    Attributes:
        match: True only when there is enough overlap AND the worst-day ratio
            gap is within ``RATIO_TOLERANCE``. False means PROVENANCE_MISMATCH
            (or too little overlap to judge).
        overlap_days: number of shared dates compared (the percent-move series
            has one fewer point than the shared closes).
        max_abs_ratio_diff: the GATE metric — worst single-day gap between the
            two percent-move series. ``None`` when there was not enough overlap.
        ratio_diff_variance: the HEALTH metric — variance of the per-day gaps.
            ``None`` when there was not enough overlap.
        reason: a short human-readable explanation (empty on a clean match).
    """

    match: bool
    overlap_days: int
    max_abs_ratio_diff: float | None
    ratio_diff_variance: float | None
    reason: str


def _pct_moves(closes: list[float]) -> list[float]:
    """Day-to-day percent moves as fractions: ``(c[i] - c[i-1]) / c[i-1]``.

    A split/dividend rescales every ``c`` by a constant factor, which cancels in
    this ratio — so adjusted and unadjusted series yield (almost) the same moves.
    A zero previous close yields a 0.0 move (no division by zero).
    """
    moves: list[float] = []
    for prev, cur in zip(closes[:-1], closes[1:]):
        moves.append((cur - prev) / prev if prev else 0.0)
    return moves


def _align_on_dates(
    series_a: dict[str, float],
    series_b: dict[str, float],
) -> list[float]:
    """Return the sorted overlapping dates present in BOTH series.

    Each series is a mapping of ISO date string -> close price. Comparing only
    shared dates means a different lookback length (200d vs 5y) or a missing day
    in one source does not by itself count as disagreement.
    """
    return sorted(set(series_a) & set(series_b))


def compare_series(
    series_a: dict[str, float],
    series_b: dict[str, float],
    *,
    tolerance: float = RATIO_TOLERANCE,
    min_overlap: int = MIN_OVERLAP_DAYS,
) -> ProvenanceResult:
    """Compare two close-price series on their day-to-day percent moves.

    Args:
        series_a: our snapshot's closes, ISO-date -> close.
        series_b: TradingAgents' closes, ISO-date -> close.
        tolerance: worst-day ratio-gap gate (fraction). Defaults to
            ``RATIO_TOLERANCE``.
        min_overlap: minimum shared dates required to judge. Defaults to
            ``MIN_OVERLAP_DAYS``.

    Returns:
        A :class:`ProvenanceResult`. ``match`` is True only with enough overlap
        and a worst-day gap within ``tolerance``. Insufficient overlap is a
        non-match (uncertainty is never a pass).

    Pure and deterministic: no network, no I/O.
    """
    shared_dates = _align_on_dates(series_a, series_b)

    # Need at least min_overlap shared closes -> (min_overlap - 1) moves.
    if len(shared_dates) < min_overlap:
        return ProvenanceResult(
            match=False,
            overlap_days=len(shared_dates),
            max_abs_ratio_diff=None,
            ratio_diff_variance=None,
            reason=(
                f"insufficient overlap: {len(shared_dates)} shared day(s), "
                f"need >= {min_overlap} (uncertainty is not a match)"
            ),
        )

    closes_a = [series_a[d] for d in shared_dates]
    closes_b = [series_b[d] for d in shared_dates]

    moves_a = _pct_moves(closes_a)
    moves_b = _pct_moves(closes_b)

    # Per-day gap between the two percent-move series.
    diffs = [abs(a - b) for a, b in zip(moves_a, moves_b)]
    max_abs = max(diffs)

    # Population variance of the per-day gaps (the health signal). Pure python
    # so no numpy dependency is pulled into this module.
    mean = sum(diffs) / len(diffs)
    variance = sum((d - mean) ** 2 for d in diffs) / len(diffs)

    within = max_abs <= tolerance
    reason = (
        ""
        if within
        else (
            f"ratio mismatch: worst-day move gap {max_abs:.4f} exceeds "
            f"tolerance {tolerance:.4f}"
        )
    )

    return ProvenanceResult(
        match=within,
        overlap_days=len(shared_dates),
        max_abs_ratio_diff=round(max_abs, 6),
        ratio_diff_variance=round(variance, 8),
        reason=reason,
    )
