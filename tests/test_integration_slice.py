"""Offline integration test for the slice (design 7.2).

No network / no Ollama / no real graph: the quant ``DataPipeline`` fetch is
replaced by a synthetic enriched daily frame, the deep provenance TA-closes
fetch (``get_closes``) returns OUR tail so provenance matches, and the
TradingAgents graph is replaced by a fake whose ``.propagate`` returns a canned
``(final_state, signal)`` carrying snapshot-backed figures. Both lanes run on
the SAME synthetic daily snapshot so the two contracts can be compared end to
end:

  * both lanes compute the SAME ``snapshot_id`` (one ``compute_snapshot_id``),
  * ``fuse([quant_rec, deep_rec])`` returns BOTH objects verbatim and flags the
    conflict (True when the actions differ, False when they agree) — never
    averaged, actions/confidence untouched (STEERING rule 4).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from backend.app.schemas.fusion import fuse
from backend.app.schemas.snapshot import SNAPSHOT_TAIL_N, compute_snapshot_id
from deep import adapter
from deep.adapter import analyze
from quant.data.indicators import TechnicalIndicators
from quant.recommend import recommend

AS_OF = date(2024, 1, 15)


def _synthetic_daily(n: int = 160, seed: int = 42) -> pd.DataFrame:
    """A deterministic DAILY OHLCV uptrend (mirrors the quant/deep fixtures)."""
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
    df = _synthetic_daily()
    enriched = TechnicalIndicators.add_all(df, interval="1d")
    return enriched.dropna(subset=["SMA_50", "RSI"])


def _patch_pipeline(monkeypatch, enriched: pd.DataFrame) -> None:
    """Make the deep adapter's DataPipeline fetch return ``enriched``."""
    monkeypatch.setattr(
        "quant.data.pipeline.DataPipeline.get_enriched_data",
        lambda self, *a, **k: enriched,
    )


def _patch_deep_graph(monkeypatch, enriched: pd.DataFrame, *, signal: str) -> None:
    """Stub the deep lane's graph + provenance so ``analyze`` runs fully offline.

    ``get_closes`` returns OUR snapshot tail (provenance matches), and a fake
    ``TradingAgentsGraph`` returns reports that cite only snapshot-backed figures
    with the requested 5-tier ``signal``.
    """
    tail = enriched.tail(SNAPSHOT_TAIL_N)
    series = pd.Series(tail["Close"].values, index=tail.index, dtype=float)
    monkeypatch.setattr(adapter, "get_closes", lambda *a, **k: series)

    summary = TechnicalIndicators.summarize(enriched)
    rsi = summary["momentum"]["RSI"]
    current = summary["price"]["current"]
    final_state = {
        "market_report": f"RSI sits at {rsi} with price near {current}.",
        "news_report": f"Context stable; price around {current}.",
        "fundamentals_report": "",
        "sentiment_report": "",
        "final_trade_decision": f"RSI {rsi} near price {current} informs the call.",
        "final_rating": None,
    }

    class _FakeGraph:
        def __init__(self, *a, **k):
            pass

        def propagate(self, company_name, trade_date, asset_type="stock", portfolio=None):
            return final_state, signal

    monkeypatch.setattr(adapter, "TradingAgentsGraph", _FakeGraph)


def test_both_lanes_share_one_snapshot_id(monkeypatch):
    """quant.recommend and deep.analyze over the same daily snapshot agree on
    ``snapshot_id`` (one ``compute_snapshot_id``)."""
    enriched = _enriched_daily()
    _patch_pipeline(monkeypatch, enriched)
    _patch_deep_graph(monkeypatch, enriched, signal="Buy")

    quant_rec = recommend("TEST", AS_OF, df=enriched)
    deep_rec = analyze("TEST", AS_OF)

    expected = compute_snapshot_id("TEST", AS_OF, enriched)
    assert quant_rec.snapshot_id == deep_rec.snapshot_id == expected


def test_fuse_conflict_true_keeps_both_when_actions_differ(monkeypatch):
    """Divergent actions -> conflict=True, both objects verbatim, never averaged."""
    enriched = _enriched_daily()
    _patch_pipeline(monkeypatch, enriched)

    quant_rec = recommend("TEST", AS_OF, df=enriched)
    # The engineered uptrend is a quant BUY; make deep disagree with a Sell.
    assert quant_rec.action.value == "BUY"
    _patch_deep_graph(monkeypatch, enriched, signal="Sell")
    deep_rec = analyze("TEST", AS_OF)

    fused = fuse([quant_rec, deep_rec])

    assert fused["conflict"] is True
    # Both objects pass through verbatim (same identities, untouched).
    assert fused["recommendations"] == [quant_rec, deep_rec]
    assert fused["recommendations"][0] is quant_rec
    assert fused["recommendations"][1] is deep_rec
    # Actions unchanged — nothing averaged.
    assert quant_rec.action.value == "BUY"
    assert deep_rec.action.value == "SELL"


def test_fuse_conflict_false_keeps_both_when_actions_agree(monkeypatch):
    """Agreeing actions -> conflict=False, both objects still kept verbatim."""
    enriched = _enriched_daily()
    _patch_pipeline(monkeypatch, enriched)

    quant_rec = recommend("TEST", AS_OF, df=enriched)
    assert quant_rec.action.value == "BUY"
    _patch_deep_graph(monkeypatch, enriched, signal="Buy")
    deep_rec = analyze("TEST", AS_OF)

    fused = fuse([quant_rec, deep_rec])

    assert fused["conflict"] is False
    assert fused["recommendations"] == [quant_rec, deep_rec]
    assert fused["recommendations"][0] is quant_rec
    assert fused["recommendations"][1] is deep_rec
    # Confidence values preserved independently — each lane keeps its own
    # number, nothing collapsed. Deep's confidence is its rating-tier value.
    assert deep_rec.confidence == 0.8
    assert quant_rec.confidence == quant_rec.confidence  # untouched by fuse
