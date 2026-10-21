"""Offline, deterministic tests for the deep adapter (design 4, tests 7.3).

No network / no Ollama: ``deep.adapter.generate_sync`` is monkeypatched to
return a canned Recommendation JSON, and the quant ``DataPipeline`` fetch is
replaced by a synthetic enriched daily frame so ``analyze`` never touches
yfinance. These cover the three branches in FEAT-004 step 7:
  (a) all figures in snapshot  -> Recommendation(source='deep'),
  (b) a planted out-of-tolerance figure -> NumberCheckError (+ a boundary
      figure within 1% that passes),
  (c) signed regression: a faithful ``-0.42`` passes, a sign-flipped ``+0.42``
      is rejected,
plus a structural assertion that the adapter source imports no FastAPI.
"""

from __future__ import annotations

import inspect
import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from backend.app.schemas.snapshot import compute_snapshot_id
from deep import adapter
from deep.adapter import DeepValidationError, NumberCheckError, analyze

AS_OF = date(2024, 1, 15)


def _synthetic_daily(n: int = 160, seed: int = 42) -> pd.DataFrame:
    """A deterministic DAILY OHLCV frame on a steady uptrend (mirrors the quant
    fixture) so the enriched snapshot is reproducible without a network fetch."""
    idx = pd.bdate_range("2023-06-01", periods=n)
    rng = np.random.RandomState(seed)
    close = np.empty(n)
    p = 100.0
    for i in range(n):
        p = p * (1 + 0.004) + rng.uniform(-0.05, 0.05)
        close[i] = p
    openp = close * (1 - 0.001)
    high = np.maximum(openp, close) * (1 + 0.002)
    low = np.minimum(openp, close) * (1 - 0.002)
    vol = np.full(n, 1_000_000.0)
    df = pd.DataFrame(
        {"Open": openp, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )
    df.index.name = "Datetime"
    return df


def _enriched_daily() -> pd.DataFrame:
    from quant.data.indicators import TechnicalIndicators

    df = _synthetic_daily()
    enriched = TechnicalIndicators.add_all(df, interval="1d")
    return enriched.dropna(subset=["SMA_50", "RSI"])


@pytest.fixture
def patched_pipeline(monkeypatch):
    """Replace the live DataPipeline fetch with the synthetic enriched frame."""
    enriched = _enriched_daily()

    def _fake_get_enriched(self, *args, **kwargs):
        return enriched

    monkeypatch.setattr(
        "quant.data.pipeline.DataPipeline.get_enriched_data", _fake_get_enriched
    )
    return enriched


def _patch_gateway(monkeypatch, rec_dict: dict):
    """Stub ``generate_sync`` (imported into the adapter namespace) to return a
    valid gateway response carrying ``rec_dict`` as the JSON text."""

    def _fake_generate_sync(prompt, **kwargs):
        return {
            "text": json.dumps(rec_dict),
            "valid": True,
            "model_id": "llama3.2",
        }

    monkeypatch.setattr(adapter, "generate_sync", _fake_generate_sync)


def _base_rec(**overrides) -> dict:
    rec = {
        "ticker": "TEST",
        "as_of": AS_OF.isoformat(),
        "action": "BUY",
        "confidence": 0.7,
        "horizon_days": 10,
        "rationale": "Trend is intact.",
        "evidence": [],
        "source": "deep",
        "snapshot_id": "placeholder",
    }
    rec.update(overrides)
    return rec


# ---------------------------------------------------------------------------
# (a) All figures present in the snapshot -> a deep Recommendation.
# ---------------------------------------------------------------------------
def test_analyze_all_figures_in_snapshot_returns_deep(monkeypatch, patched_pipeline):
    from quant.data.indicators import TechnicalIndicators

    enriched = patched_pipeline
    summary = TechnicalIndicators.summarize(enriched)
    rsi = summary["momentum"]["RSI"]
    current = summary["price"]["current"]

    rec_dict = _base_rec(
        rationale=f"RSI sits at {rsi} with price near {current}.",
        evidence=[f"RSI={rsi}", f"price.current={current}"],
    )
    _patch_gateway(monkeypatch, rec_dict)

    rec = analyze("TEST", AS_OF)

    assert rec.source == "deep"
    assert rec.model_id == "llama3.2"
    assert rec.prompt_version == "deep-v1"
    assert rec.snapshot_id == compute_snapshot_id("TEST", AS_OF, enriched)


# ---------------------------------------------------------------------------
# (b) Planted out-of-tolerance figure -> NumberCheckError; boundary passes.
# ---------------------------------------------------------------------------
def test_analyze_planted_figure_raises_number_check(monkeypatch, patched_pipeline):
    # 123456.78 sits in the gap between the price-scale numbers (~188) and the
    # raw Volume value (1,000,000), so it is outside tolerance of every
    # allowable snapshot number.
    rec_dict = _base_rec(
        rationale="Target implies a move to 123456.78 soon.",
        evidence=["target=123456.78"],
    )
    _patch_gateway(monkeypatch, rec_dict)

    with pytest.raises(NumberCheckError) as excinfo:
        analyze("TEST", AS_OF)
    assert 123456.78 in excinfo.value.offending


def test_analyze_within_one_percent_boundary_passes(monkeypatch, patched_pipeline):
    from quant.data.indicators import TechnicalIndicators

    enriched = patched_pipeline
    summary = TechnicalIndicators.summarize(enriched)
    rsi = summary["momentum"]["RSI"]
    # A figure within 1% relative tolerance of the stored RSI must pass.
    nudged = round(rsi * 1.005, 4)

    rec_dict = _base_rec(
        rationale=f"RSI roughly {nudged}.",
        evidence=[f"RSI~{nudged}"],
    )
    _patch_gateway(monkeypatch, rec_dict)

    rec = analyze("TEST", AS_OF)
    assert rec.source == "deep"


# ---------------------------------------------------------------------------
# (c) Signed regression: faithful -0.42 passes, sign-flipped +0.42 rejected.
# ---------------------------------------------------------------------------
def _enriched_with_macd(value: float) -> pd.DataFrame:
    """A synthetic enriched frame whose latest MACD equals ``value``."""
    enriched = _enriched_daily().copy()
    enriched.iloc[-1, enriched.columns.get_loc("MACD")] = value
    return enriched


def test_analyze_signed_macd_faithful_passes(monkeypatch):
    enriched = _enriched_with_macd(-0.42)
    monkeypatch.setattr(
        "quant.data.pipeline.DataPipeline.get_enriched_data",
        lambda self, *a, **k: enriched,
    )
    rec_dict = _base_rec(
        rationale="MACD is negative at -0.42, momentum is weak.",
        evidence=["momentum.MACD=-0.42"],
    )
    _patch_gateway(monkeypatch, rec_dict)

    rec = analyze("TEST", AS_OF)
    assert rec.source == "deep"


def test_analyze_signed_macd_flipped_rejected(monkeypatch):
    enriched = _enriched_with_macd(-0.42)
    monkeypatch.setattr(
        "quant.data.pipeline.DataPipeline.get_enriched_data",
        lambda self, *a, **k: enriched,
    )
    # Sign-flipped +0.42 is NOT in the snapshot (which holds -0.42).
    rec_dict = _base_rec(
        rationale="MACD reads +0.42, momentum is building.",
        evidence=["momentum.MACD=+0.42"],
    )
    _patch_gateway(monkeypatch, rec_dict)

    with pytest.raises(NumberCheckError) as excinfo:
        analyze("TEST", AS_OF)
    assert 0.42 in excinfo.value.offending


# ---------------------------------------------------------------------------
# Gateway invalid output -> DeepValidationError.
# ---------------------------------------------------------------------------
def test_analyze_gateway_invalid_raises_deep_validation(monkeypatch, patched_pipeline):
    def _fake(prompt, **kwargs):
        return {"text": "not json", "valid": False, "model_id": "llama3.2"}

    monkeypatch.setattr(adapter, "generate_sync", _fake)
    with pytest.raises(DeepValidationError):
        analyze("TEST", AS_OF)


# ---------------------------------------------------------------------------
# Structural: the adapter source imports no FastAPI (STEERING rule 5).
# ---------------------------------------------------------------------------
def test_adapter_source_has_no_fastapi_import():
    src = inspect.getsource(adapter).lower()
    assert "fastapi" not in src
    assert "import request" not in src
