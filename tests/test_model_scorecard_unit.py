"""Offline unit tests for the model qualification scorecard (G6 deliverable 3).

These exercise ONLY the pure helpers plus a callable smoke check. They make NO
model call and NO network call, so they pass with no infra. The real full
scorecard (which burns gemma2:9b time) is run manually by the reviewer via the
CLI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway import scorecard as sc


# ---------------------------------------------------------------------------
# number_in_allowed
# ---------------------------------------------------------------------------

class TestNumberInAllowed:
    def test_exact_match_in_set(self):
        assert sc.number_in_allowed(101.5, [101.5, 98.2]) is True

    def test_out_of_set_is_false(self):
        # 200 is nowhere near any allowed value -> preservation failure.
        assert sc.number_in_allowed(200.0, [101.5, 98.2]) is False

    def test_within_absolute_tolerance(self):
        # Default abs_tol is 0.01; 101.505 is within it of 101.5.
        assert sc.number_in_allowed(101.505, [101.5]) is True

    def test_just_outside_absolute_tolerance(self):
        # 101.52 is 0.02 away from 101.5; abs_tol=0.01 and rel_tol*101.5≈0.10.
        # rel tolerance dominates here, so bump to a value outside BOTH.
        assert sc.number_in_allowed(101.52, [101.5], rel_tol=0.0) is False

    def test_relative_tolerance_on_large_value(self):
        # rel_tol=0.001 of 1_000_000 is 1000, so 1_000_500 is allowed.
        assert sc.number_in_allowed(1_000_500.0, [1_000_000.0]) is True

    def test_relative_tolerance_edge_fails(self):
        # 1_002_000 is 2000 off; rel tolerance is 1000 -> not allowed.
        assert sc.number_in_allowed(1_002_000.0, [1_000_000.0]) is False

    def test_empty_allowed_is_false(self):
        assert sc.number_in_allowed(1.0, []) is False


# ---------------------------------------------------------------------------
# parse_leading_number
# ---------------------------------------------------------------------------

class TestParseLeadingNumber:
    def test_plain(self):
        assert sc.parse_leading_number("42") == 42.0

    def test_decimal(self):
        assert sc.parse_leading_number("101.5") == 101.5

    def test_percent(self):
        assert sc.parse_leading_number("42%") == 42.0

    def test_dollar(self):
        assert sc.parse_leading_number("$1234.50") == 1234.5

    def test_commas(self):
        assert sc.parse_leading_number("1,234,567") == 1234567.0

    def test_dollar_and_commas(self):
        assert sc.parse_leading_number("$1,234.50") == 1234.5

    def test_leading_plus(self):
        assert sc.parse_leading_number("+3.14") == 3.14

    def test_negative(self):
        assert sc.parse_leading_number("-5.5") == -5.5

    def test_first_number_wins(self):
        assert sc.parse_leading_number("price is 98.2 not 101.5") == 98.2

    def test_garbage_is_none(self):
        assert sc.parse_leading_number("no number here") is None

    def test_empty_is_none(self):
        assert sc.parse_leading_number("") is None


# ---------------------------------------------------------------------------
# summarize_latencies
# ---------------------------------------------------------------------------

class TestSummarizeLatencies:
    def test_mean_median_and_tps(self):
        # latencies 100ms, 200ms, 300ms -> mean 200, median 200.
        # tokens 10, 20, 30 -> tps per call: 100, 100, 100 -> mean 100.
        out = sc.summarize_latencies([100, 200, 300], [10, 20, 30])
        assert out["latency_ms_mean"] == 200.0
        assert out["latency_ms_median"] == 200.0
        assert out["tokens_per_sec_mean"] == pytest.approx(100.0)

    def test_median_even_count(self):
        out = sc.summarize_latencies([100, 300], [5, 15])
        assert out["latency_ms_median"] == 200.0  # mean of the two middle values

    def test_zero_latency_excluded_from_tps(self):
        # The second call has 0 latency -> excluded from tps, kept for latency.
        out = sc.summarize_latencies([100, 0], [10, 10])
        assert out["tokens_per_sec_mean"] == pytest.approx(100.0)
        assert out["latency_ms_mean"] == 50.0

    def test_empty_inputs_safe_zeros(self):
        out = sc.summarize_latencies([], [])
        assert out == {
            "latency_ms_mean": 0.0,
            "latency_ms_median": 0.0,
            "tokens_per_sec_mean": 0.0,
        }

    def test_no_positive_token_calls_zero_tps(self):
        out = sc.summarize_latencies([100, 200], [0, 0])
        assert out["tokens_per_sec_mean"] == 0.0
        assert out["latency_ms_mean"] == 150.0


# ---------------------------------------------------------------------------
# choose_recommended
# ---------------------------------------------------------------------------

def _score(model, *, validity, numbers, median_latency, skipped=False):
    return sc.ModelScore(
        model=model,
        validity_total=20,
        validity_valid=int(round(validity * 20)),
        validity_rate=validity,
        number_total=5,
        number_preserved=int(round(numbers * 5)),
        number_preserved_rate=numbers,
        latency_count=20,
        latency_ms_mean=median_latency,
        latency_ms_median=median_latency,
        tokens_per_sec_mean=10.0,
        skipped=skipped,
    )


class TestChooseRecommended:
    def test_llama_passes_and_is_faster_wins(self):
        # Both pass validity and tie on numbers; llama is faster -> llama.
        llama = _score("llama3.2", validity=0.98, numbers=1.0, median_latency=800)
        gemma = _score("gemma2:9b", validity=0.97, numbers=1.0, median_latency=4000)
        model, reason = sc.choose_recommended([llama, gemma])
        assert model == "llama3.2"
        assert "fits the 4 GB VRAM budget" in reason

    def test_only_gemma_passes_validity_wins(self):
        # llama fails the 0.95 bar; gemma passes -> gemma, even though slower.
        llama = _score("llama3.2", validity=0.80, numbers=1.0, median_latency=800)
        gemma = _score("gemma2:9b", validity=0.96, numbers=1.0, median_latency=4000)
        model, reason = sc.choose_recommended([llama, gemma])
        assert model == "gemma2:9b"
        assert "only qualifying model" in reason

    def test_passing_model_with_worse_numbers_not_preferred(self):
        # Both pass validity; gemma preserves numbers better -> gemma wins even
        # though llama is faster, because number-preservation gates the tie.
        llama = _score("llama3.2", validity=0.99, numbers=0.60, median_latency=800)
        gemma = _score("gemma2:9b", validity=0.96, numbers=1.00, median_latency=4000)
        model, _reason = sc.choose_recommended([llama, gemma])
        assert model == "gemma2:9b"

    def test_no_model_passes_recommends_least_bad_with_warning(self):
        llama = _score("llama3.2", validity=0.70, numbers=1.0, median_latency=800)
        gemma = _score("gemma2:9b", validity=0.85, numbers=1.0, median_latency=4000)
        model, reason = sc.choose_recommended([llama, gemma])
        assert model == "gemma2:9b"  # higher (least-bad) validity
        assert "warning" in reason.lower()

    def test_skipped_models_are_not_candidates(self):
        llama = _score("llama3.2", validity=0.98, numbers=1.0, median_latency=800)
        gemma = _score("gemma2:9b", validity=0.0, numbers=0.0, median_latency=0, skipped=True)
        model, _reason = sc.choose_recommended([llama, gemma])
        assert model == "llama3.2"

    def test_all_skipped_returns_empty(self):
        a = _score("llama3.2", validity=0.0, numbers=0.0, median_latency=0, skipped=True)
        b = _score("gemma2:9b", validity=0.0, numbers=0.0, median_latency=0, skipped=True)
        model, reason = sc.choose_recommended([a, b])
        assert model == ""
        assert "No model" in reason


# ---------------------------------------------------------------------------
# Writers + callable smoke (no infra)
# ---------------------------------------------------------------------------

class TestWritersAndSmoke:
    def test_write_scorecard_produces_both_files(self, tmp_path: Path):
        llama = _score("llama3.2", validity=0.98, numbers=1.0, median_latency=800)
        gemma = sc.ModelScore(
            model="gemma2:9b",
            skipped=True,
            note="not pulled in Ollama (absent from /api/tags) — skipped",
        )
        model, reason = sc.choose_recommended([llama, gemma])
        card = sc.Scorecard(
            scores=[llama, gemma],
            recommended_model=model,
            recommendation_reason=reason,
        )
        paths = sc.write_scorecard(card, tmp_path)

        md = Path(paths["markdown"])
        js = Path(paths["json"])
        assert md.exists() and js.exists()

        md_text = md.read_text(encoding="utf-8")
        assert "Model qualification scorecard" in md_text
        assert "llama3.2" in md_text
        assert "4 GB VRAM" in md_text
        assert "skipped" in md_text  # the skipped gemma row

        import json

        data = json.loads(js.read_text(encoding="utf-8"))
        assert data["recommended_model"] == "llama3.2"
        assert len(data["scores"]) == 2
        # Deterministic rounding applied.
        assert data["scores"][0]["validity_rate"] == 0.98

    def test_live_runner_entry_points_are_callable(self):
        # We only assert these are callables; we do NOT invoke them (they touch
        # Ollama). This guards against accidental rename/removal.
        for name in (
            "run_validity",
            "run_number_preservation",
            "score_model",
            "build_scorecard",
            "model_available",
        ):
            assert callable(getattr(sc, name))

    def test_number_cases_are_well_formed(self):
        # ~5 deterministic cases, each with a non-empty allowed set.
        assert 3 <= len(sc.NUMBER_CASES) <= 8
        for case in sc.NUMBER_CASES:
            assert case["allowed"]
            assert all(isinstance(a, (int, float)) for a in case["allowed"])
            assert "target" in case

    def test_signal_schema_is_strict(self):
        # additionalProperties false + the two required keys (never loosened).
        assert sc.SIGNAL_SCHEMA["additionalProperties"] is False
        assert set(sc.SIGNAL_SCHEMA["required"]) == {"signal", "confidence"}
