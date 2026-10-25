"""Offline unit tests for the deep-lane provenance cross-check (G4).

Pure, deterministic, no network. These prove the ratio gate and the variance
health-signal behave correctly — most importantly the case the whole design
exists for: two series that differ only by a constant split/dividend ADJUSTMENT
factor must still MATCH, because we compare day-to-day percent moves, not
absolute prices.
"""

from __future__ import annotations

from deep.provenance import (
    MIN_OVERLAP_DAYS,
    RATIO_TOLERANCE,
    ProvenanceResult,
    compare_series,
)


def _ladder(start: str, closes: list[float]) -> dict[str, float]:
    """Build an ISO-date -> close mapping over consecutive business-ish days.

    Dates don't need to be real trading days for the logic (we only compare on
    shared keys); sequential YYYY-MM-DD strings keep the test readable.
    """
    from datetime import date, timedelta

    d0 = date.fromisoformat(start)
    return {(d0 + timedelta(days=i)).isoformat(): c for i, c in enumerate(closes)}


# --- The headline case: adjusted vs raw must MATCH on ratios -----------------


def test_constant_adjustment_factor_matches():
    """Series B is series A scaled by a constant 0.5 (a 2-for-1 split style
    adjustment). Absolute prices differ hugely, but every day-to-day percent
    move is identical -> MATCH. This is the whole reason we compare on ratios."""
    raw = [100.0, 102.0, 101.0, 105.0, 104.0, 108.0, 110.0]
    adjusted = [c * 0.5 for c in raw]  # every price halved
    a = _ladder("2024-01-01", raw)
    b = _ladder("2024-01-01", adjusted)

    result = compare_series(a, b)

    assert isinstance(result, ProvenanceResult)
    assert result.match is True
    assert result.max_abs_ratio_diff is not None
    # The gaps should be effectively zero (floating-point dust only).
    assert result.max_abs_ratio_diff < 1e-9
    assert result.overlap_days == len(raw)


def test_identical_series_matches_with_zero_variance():
    """Two identical series: perfect match, zero worst-day gap, zero variance."""
    closes = [50.0, 51.0, 52.5, 52.0, 53.0, 54.0]
    a = _ladder("2024-02-01", closes)
    b = _ladder("2024-02-01", list(closes))

    result = compare_series(a, b)

    assert result.match is True
    assert result.max_abs_ratio_diff == 0.0
    assert result.ratio_diff_variance == 0.0


# --- The gate: a real shape disagreement must MISMATCH ------------------------


def test_divergent_shape_mismatches():
    """One day genuinely moves differently (a real disagreement in the SHAPE of
    the move, not a scaling) -> worst-day gap exceeds tolerance -> MISMATCH."""
    base = [100.0, 102.0, 101.0, 105.0, 104.0, 108.0, 110.0]
    bad = list(base)
    bad[4] = 90.0  # a day that moves very differently from `base`
    a = _ladder("2024-03-01", base)
    b = _ladder("2024-03-01", bad)

    result = compare_series(a, b)

    assert result.match is False
    assert result.max_abs_ratio_diff > RATIO_TOLERANCE
    assert "ratio mismatch" in result.reason


def test_small_wobble_within_tolerance_matches():
    """Tiny day-to-day differences (well under the 1% move-gap tolerance) still
    count as agreement — the sources needn't be bit-identical to match."""
    base = [200.0, 202.0, 201.0, 203.0, 204.0, 206.0]
    # Nudge B's closes by a hair; resulting move-gaps stay < RATIO_TOLERANCE.
    nudged = [c * 1.0005 for c in base]
    a = _ladder("2024-04-01", base)
    b = _ladder("2024-04-01", nudged)

    result = compare_series(a, b)

    assert result.match is True
    assert result.max_abs_ratio_diff <= RATIO_TOLERANCE


# --- Variance as a health signal (reported, not the gate) --------------------


def test_variance_is_low_for_steady_offset_but_gate_still_decides():
    """A CONSTANT scaling has ~zero variance AND matches — this documents why
    variance alone can't be the gate (a steady offset looks 'stable') while the
    ratio gate is what actually passes/fails. Here both agree it's a match."""
    base = [10.0, 10.5, 10.3, 10.8, 11.0, 11.2]
    scaled = [c * 3.0 for c in base]
    result = compare_series(_ladder("2024-05-01", base), _ladder("2024-05-01", scaled))
    assert result.match is True
    assert result.ratio_diff_variance is not None
    assert result.ratio_diff_variance < 1e-12  # steady -> near-zero variance


def test_variance_higher_when_gaps_are_scattered():
    """Scattered per-day gaps produce a larger variance than a single uniform
    gap of the same worst-case size — the health signal reflects jitter."""
    base = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
    scattered = [100.0, 101.3, 101.9, 103.4, 103.7, 105.2, 105.8]
    result = compare_series(
        _ladder("2024-06-01", base),
        _ladder("2024-06-01", scattered),
        tolerance=1.0,  # loosen the gate so we isolate the variance behaviour
    )
    assert result.ratio_diff_variance is not None
    assert result.ratio_diff_variance > 0.0


# --- Overlap handling: uncertainty is never a pass ---------------------------


def test_insufficient_overlap_is_a_mismatch():
    """Fewer than MIN_OVERLAP_DAYS shared dates -> not enough to judge -> the
    result is a NON-match with an explanatory reason (uncertainty != match)."""
    a = _ladder("2024-07-01", [100.0, 101.0])  # only 2 days
    b = _ladder("2024-07-01", [100.0, 101.0])

    result = compare_series(a, b)

    assert result.match is False
    assert result.overlap_days == 2
    assert result.max_abs_ratio_diff is None
    assert "insufficient overlap" in result.reason


def test_only_overlapping_dates_are_compared():
    """Different lookback lengths: B has extra earlier dates A doesn't. Only the
    shared tail is compared, so the extra history is not a disagreement."""
    shared = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    a = _ladder("2024-08-10", shared)
    # B starts 5 days earlier but shares the same dates from 2024-08-10 on.
    b_full = _ladder("2024-08-05", [95.0, 96.0, 97.0, 98.0, 99.0, *shared])

    result = compare_series(a, b_full)

    assert result.match is True
    # Overlap is only the shared window, not B's full length.
    assert result.overlap_days == len(shared)


def test_zero_previous_close_does_not_crash():
    """A zero price yields a 0.0 move rather than a division error."""
    a = _ladder("2024-09-01", [0.0, 100.0, 101.0, 102.0, 103.0, 104.0])
    b = _ladder("2024-09-01", [0.0, 100.0, 101.0, 102.0, 103.0, 104.0])
    result = compare_series(a, b)
    assert result.match is True


def test_defaults_are_sane():
    """Guard the documented defaults so a careless edit is caught."""
    assert RATIO_TOLERANCE == 0.01
    assert MIN_OVERLAP_DAYS == 5
