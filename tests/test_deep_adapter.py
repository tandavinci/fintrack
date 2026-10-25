"""Offline, deterministic tests for the deep adapter (design 4, tests 7.3).

No network / no Ollama / no real graph: the TradingAgents graph and its
provenance close-fetch are monkeypatched. ``DataPipeline.get_enriched_data`` is
replaced by a synthetic enriched daily frame so ``analyze`` never touches
yfinance, and ``deep.adapter.TradingAgentsGraph`` is replaced by a fake whose
``.propagate`` returns a canned ``(final_state, signal)``. ``deep.adapter``'s
reference to ``get_closes`` (the provenance TA-closes fetch) is replaced with a
series derived from OUR synthetic frame so provenance MATCHES in the happy path.

Coverage (G4):
  (1) happy path: matching provenance + sufficient evidence + "Buy" -> deep BUY,
      snapshot_id == ours, prompt_version == "deep-v2",
  (2) provenance mismatch (divergent TA series) -> HOLD, confidence 0.0,
      rationale startswith "PROVENANCE_MISMATCH:",
  (3) provenance fetch raises/empty -> same HOLD path, reason mentions unavailable,
  (4) REVIEW signal -> HOLD,
  (5) rating mapping: Overweight->BUY, Underweight->SELL, Sell->SELL, Hold->HOLD,
  (6) insufficient evidence (empty/fallback news_report) -> HOLD, confidence<=0.1,
      rationale startswith "INSUFFICIENT_EVIDENCE:",
  (7) number-check still bites: an out-of-snapshot figure reaching the final
      rationale -> NumberCheckError,
  (8) signed/boundary number-check regressions preserved,
  (9) structural: the adapter source imports no FastAPI.
"""

from __future__ import annotations

import inspect
from datetime import date

import numpy as np
import pandas as pd
import pytest

from backend.app.schemas.recommendation import Action
from backend.app.schemas.snapshot import SNAPSHOT_TAIL_N, compute_snapshot_id
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


def _matching_ta_series(enriched: pd.DataFrame) -> pd.Series:
    """A TA close series derived from OUR snapshot tail so provenance MATCHES.

    The provenance gate compares day-to-day percent moves over the shared dates;
    returning the exact closes of our tail makes the moves identical (gap 0).
    """
    tail = enriched.tail(SNAPSHOT_TAIL_N)
    return pd.Series(tail["Close"].values, index=tail.index, dtype=float)


def _patch_graph(monkeypatch, final_state: dict, signal: str):
    """Install a fake ``TradingAgentsGraph`` whose ``.propagate`` returns the
    canned ``(final_state, signal)`` — the real graph never runs in tests."""

    class _FakeGraph:
        def __init__(self, *args, **kwargs):
            pass

        def propagate(self, company_name, trade_date, asset_type="stock", portfolio=None):
            return final_state, signal

    monkeypatch.setattr(adapter, "TradingAgentsGraph", _FakeGraph)


def _patch_provenance_match(monkeypatch, enriched: pd.DataFrame):
    """Make the adapter's ``get_closes`` return OUR tail so provenance passes."""
    series = _matching_ta_series(enriched)
    monkeypatch.setattr(adapter, "get_closes", lambda *a, **k: series)


def _final_state(market: str, news: str, decision: str) -> dict:
    return {
        "market_report": market,
        "news_report": news,
        "fundamentals_report": "",
        "sentiment_report": "",
        "final_trade_decision": decision,
        "final_rating": None,
    }


# ---------------------------------------------------------------------------
# (1) Happy path: matching provenance + sufficient evidence + "Buy" -> deep BUY.
# ---------------------------------------------------------------------------
def test_analyze_happy_path_buy(monkeypatch, patched_pipeline):
    from quant.data.indicators import TechnicalIndicators

    enriched = patched_pipeline
    summary = TechnicalIndicators.summarize(enriched)
    rsi = summary["momentum"]["RSI"]
    current = summary["price"]["current"]

    # Reports cite only snapshot-backed figures; the decision text cites them too
    # so the final number-check passes.
    market = f"Trend intact. RSI sits at {rsi}. Price near {current}."
    news = f"No red flags. Price around {current}."
    decision = f"Momentum constructive; RSI {rsi} supports a position."
    _patch_graph(monkeypatch, _final_state(market, news, decision), "Buy")
    _patch_provenance_match(monkeypatch, enriched)

    rec = analyze("TEST", AS_OF)

    assert rec.source == "deep"
    assert rec.action == Action.BUY
    assert rec.model_id == "llama3.2"
    assert rec.prompt_version == "deep-v2"
    assert rec.snapshot_id == compute_snapshot_id("TEST", AS_OF, enriched)
    assert rec.horizon_days == 10
    assert 0.0 < rec.confidence <= 1.0
    assert rec.evidence  # at least one snapshot-backed figure cited


# ---------------------------------------------------------------------------
# (2) Provenance mismatch -> HOLD, confidence 0.0, PROVENANCE_MISMATCH:.
# ---------------------------------------------------------------------------
def test_analyze_provenance_mismatch_holds(monkeypatch, patched_pipeline):
    enriched = patched_pipeline
    # A divergent series: same dates, but prices with wildly different day moves.
    tail = enriched.tail(SNAPSHOT_TAIL_N)
    rng = np.random.RandomState(7)
    divergent = pd.Series(
        100.0 * (1 + rng.uniform(-0.3, 0.3, size=len(tail))),
        index=tail.index,
        dtype=float,
    )
    monkeypatch.setattr(adapter, "get_closes", lambda *a, **k: divergent)
    # The graph must never run on a mismatch; install a graph that would fail.
    _patch_graph(monkeypatch, _final_state("x", "y", "z"), "Buy")

    rec = analyze("TEST", AS_OF)

    assert rec.action == Action.HOLD
    assert rec.confidence == 0.0
    assert rec.rationale.startswith("PROVENANCE_MISMATCH:")
    assert rec.source == "deep"
    assert rec.snapshot_id == compute_snapshot_id(
        "TEST", AS_OF, enriched
    )


# ---------------------------------------------------------------------------
# (3) Provenance fetch raises/empty -> HOLD, reason mentions unavailable.
# ---------------------------------------------------------------------------
def test_analyze_provenance_fetch_raises_holds(monkeypatch, patched_pipeline):
    def _boom(*a, **k):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(adapter, "get_closes", _boom)
    _patch_graph(monkeypatch, _final_state("x", "y", "z"), "Buy")

    rec = analyze("TEST", AS_OF)

    assert rec.action == Action.HOLD
    assert rec.confidence == 0.0
    assert rec.rationale.startswith("PROVENANCE_MISMATCH:")
    assert "unavailable" in rec.rationale.lower()


def test_analyze_provenance_empty_series_holds(monkeypatch, patched_pipeline):
    monkeypatch.setattr(adapter, "get_closes", lambda *a, **k: pd.Series(dtype=float))
    _patch_graph(monkeypatch, _final_state("x", "y", "z"), "Buy")

    rec = analyze("TEST", AS_OF)
    assert rec.action == Action.HOLD
    assert rec.rationale.startswith("PROVENANCE_MISMATCH:")
    assert "unavailable" in rec.rationale.lower()


# ---------------------------------------------------------------------------
# (4) REVIEW signal -> HOLD (not tradeable).
# ---------------------------------------------------------------------------
def test_analyze_review_signal_holds(monkeypatch, patched_pipeline):
    from quant.data.indicators import TechnicalIndicators

    enriched = patched_pipeline
    summary = TechnicalIndicators.summarize(enriched)
    rsi = summary["momentum"]["RSI"]
    market = f"RSI {rsi}."
    news = f"RSI {rsi} holds."
    _patch_graph(monkeypatch, _final_state(market, news, "no clear rating"), "REVIEW")
    _patch_provenance_match(monkeypatch, enriched)

    rec = analyze("TEST", AS_OF)
    assert rec.action == Action.HOLD


# ---------------------------------------------------------------------------
# (5) Rating mapping: Overweight->BUY, Underweight->SELL, Sell->SELL, Hold->HOLD.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "signal,expected",
    [
        ("Overweight", Action.BUY),
        ("Underweight", Action.SELL),
        ("Sell", Action.SELL),
        ("Hold", Action.HOLD),
    ],
)
def test_analyze_rating_mapping(monkeypatch, patched_pipeline, signal, expected):
    from quant.data.indicators import TechnicalIndicators

    enriched = patched_pipeline
    summary = TechnicalIndicators.summarize(enriched)
    rsi = summary["momentum"]["RSI"]
    current = summary["price"]["current"]
    market = f"RSI {rsi}, price {current}."
    news = f"Context stable at {current}."
    decision = f"Rating reflects RSI {rsi}."
    _patch_graph(monkeypatch, _final_state(market, news, decision), signal)
    _patch_provenance_match(monkeypatch, enriched)

    rec = analyze("TEST", AS_OF)
    assert rec.action == expected


# ---------------------------------------------------------------------------
# (6) Insufficient evidence (empty/fallback news_report) -> HOLD, conf<=0.1.
# ---------------------------------------------------------------------------
def test_analyze_insufficient_evidence_empty_news(monkeypatch, patched_pipeline):
    from quant.data.indicators import TechnicalIndicators

    enriched = patched_pipeline
    summary = TechnicalIndicators.summarize(enriched)
    rsi = summary["momentum"]["RSI"]
    market = f"RSI {rsi}."
    _patch_graph(monkeypatch, _final_state(market, "", "Buy it"), "Buy")
    _patch_provenance_match(monkeypatch, enriched)

    rec = analyze("TEST", AS_OF)
    assert rec.action == Action.HOLD
    assert rec.confidence <= 0.1
    assert rec.rationale.startswith("INSUFFICIENT_EVIDENCE:")
    assert rec.evidence == []


def test_analyze_insufficient_evidence_fallback_news(monkeypatch, patched_pipeline):
    from quant.data.indicators import TechnicalIndicators

    enriched = patched_pipeline
    summary = TechnicalIndicators.summarize(enriched)
    rsi = summary["momentum"]["RSI"]
    market = f"RSI {rsi}."
    _patch_graph(
        monkeypatch,
        _final_state(market, "No significant news for this ticker.", "Buy"),
        "Buy",
    )
    _patch_provenance_match(monkeypatch, enriched)

    rec = analyze("TEST", AS_OF)
    assert rec.action == Action.HOLD
    assert rec.confidence <= 0.1
    assert rec.rationale.startswith("INSUFFICIENT_EVIDENCE:")


def test_analyze_insufficient_evidence_no_snapshot_figure(monkeypatch, patched_pipeline):
    """Populated reports that cite NO snapshot-backed figure are insufficient."""
    enriched = patched_pipeline
    market = "The trend looks constructive and broadly supportive."
    news = "Sentiment is calm with steady coverage across outlets."
    _patch_graph(monkeypatch, _final_state(market, news, "Buy"), "Buy")
    _patch_provenance_match(monkeypatch, enriched)

    rec = analyze("TEST", AS_OF)
    assert rec.action == Action.HOLD
    assert rec.confidence <= 0.1
    assert rec.rationale.startswith("INSUFFICIENT_EVIDENCE:")


# ---------------------------------------------------------------------------
# (7) Number-check still bites: an out-of-snapshot figure in the decision text
#     that reaches the final rationale -> NumberCheckError.
# ---------------------------------------------------------------------------
def test_analyze_number_check_bites(monkeypatch, patched_pipeline):
    from quant.data.indicators import TechnicalIndicators

    enriched = patched_pipeline
    summary = TechnicalIndicators.summarize(enriched)
    rsi = summary["momentum"]["RSI"]
    current = summary["price"]["current"]
    # Evidence gate passes (reports cite snapshot figures), but the DECISION text
    # (which becomes the rationale) carries 123456.78 which is not in the
    # snapshot -> number-check must raise.
    market = f"RSI {rsi}, price {current}."
    news = f"Steady at {current}."
    decision = f"Target implies a move to 123456.78 given RSI {rsi}."
    _patch_graph(monkeypatch, _final_state(market, news, decision), "Buy")
    _patch_provenance_match(monkeypatch, enriched)

    with pytest.raises(NumberCheckError) as excinfo:
        analyze("TEST", AS_OF)
    assert 123456.78 in excinfo.value.offending


# ---------------------------------------------------------------------------
# (8) Signed / boundary number-check regressions preserved.
# ---------------------------------------------------------------------------
def _enriched_with_macd(value: float) -> pd.DataFrame:
    """A synthetic enriched frame whose latest MACD equals ``value``."""
    enriched = _enriched_daily().copy()
    enriched.iloc[-1, enriched.columns.get_loc("MACD")] = value
    return enriched


def test_analyze_within_one_percent_boundary_passes(monkeypatch, patched_pipeline):
    from quant.data.indicators import TechnicalIndicators

    enriched = patched_pipeline
    summary = TechnicalIndicators.summarize(enriched)
    rsi = summary["momentum"]["RSI"]
    current = summary["price"]["current"]
    nudged = round(rsi * 1.005, 4)  # within 1% relative tolerance of stored RSI
    market = f"RSI roughly {nudged}, price {current}."
    news = f"Calm at {current}."
    decision = f"RSI about {nudged}."
    _patch_graph(monkeypatch, _final_state(market, news, decision), "Buy")
    _patch_provenance_match(monkeypatch, enriched)

    rec = analyze("TEST", AS_OF)
    assert rec.source == "deep"
    assert rec.action == Action.BUY


def test_analyze_signed_macd_faithful_passes(monkeypatch):
    enriched = _enriched_with_macd(-0.42)
    monkeypatch.setattr(
        "quant.data.pipeline.DataPipeline.get_enriched_data",
        lambda self, *a, **k: enriched,
    )
    macd = -0.42
    market = f"MACD is negative at {macd}, momentum weak."
    news = f"MACD at {macd} confirms."
    decision = f"MACD {macd} supports caution."
    _patch_graph(monkeypatch, _final_state(market, news, decision), "Hold")
    _patch_provenance_match(monkeypatch, enriched)

    rec = analyze("TEST", AS_OF)
    assert rec.source == "deep"
    assert rec.action == Action.HOLD


def test_analyze_signed_macd_flipped_rejected(monkeypatch):
    from quant.data.indicators import TechnicalIndicators

    enriched = _enriched_with_macd(-0.42)
    monkeypatch.setattr(
        "quant.data.pipeline.DataPipeline.get_enriched_data",
        lambda self, *a, **k: enriched,
    )
    summary = TechnicalIndicators.summarize(enriched)
    current = summary["price"]["current"]
    # Reports carry a legitimate snapshot-backed figure (price) so the evidence
    # gate passes; the DECISION text (which becomes the rationale) carries the
    # sign-flipped +0.42 that is NOT in the snapshot (which holds -0.42), so the
    # number-check must reject it.
    market = f"MACD reads +0.42, momentum building; price {current}."
    news = f"Price steady at {current}."
    decision = "MACD +0.42 argues for upside."
    _patch_graph(monkeypatch, _final_state(market, news, decision), "Buy")
    _patch_provenance_match(monkeypatch, enriched)

    with pytest.raises(NumberCheckError) as excinfo:
        analyze("TEST", AS_OF)
    assert 0.42 in excinfo.value.offending


# ---------------------------------------------------------------------------
# Graph run failure -> DeepValidationError.
# ---------------------------------------------------------------------------
def test_analyze_graph_failure_raises_deep_validation(monkeypatch, patched_pipeline):
    enriched = patched_pipeline
    _patch_provenance_match(monkeypatch, enriched)

    class _BoomGraph:
        def __init__(self, *a, **k):
            pass

        def propagate(self, *a, **k):
            raise RuntimeError("graph exploded")

    monkeypatch.setattr(adapter, "TradingAgentsGraph", _BoomGraph)

    with pytest.raises(DeepValidationError):
        analyze("TEST", AS_OF)


# ---------------------------------------------------------------------------
# (9) Structural: the adapter source imports no FastAPI (STEERING rule 5).
# ---------------------------------------------------------------------------
def test_adapter_source_has_no_fastapi_import():
    src = inspect.getsource(adapter).lower()
    assert "fastapi" not in src
    assert "import request" not in src
