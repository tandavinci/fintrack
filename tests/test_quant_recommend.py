"""Offline deterministic tests for the quant lane (tests 7.1).

No network / no yfinance: a synthetic DAILY OHLCV frame with >=60 warmed bars
is engineered into a known bullish regime, then fed to ``recommend`` via the
pre-warmed ``df`` path. The lane must be byte-for-byte reproducible.
"""

from datetime import date

import numpy as np
import pandas as pd

from backend.app.schemas.snapshot import compute_snapshot_id
from quant.data.indicators import TechnicalIndicators
from quant.recommend import HORIZON_DAYS, build_evidence, recommend

AS_OF = date(2024, 1, 15)


def _synthetic_daily(n: int = 160, seed: int = 42) -> pd.DataFrame:
    """Build a deterministic DAILY OHLCV frame on a steady uptrend.

    The upward drift engineers a bullish regime / BUY signal. A fixed seed
    keeps the small noise component identical across runs.
    """
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
    """Return the enriched, warmed-up synthetic daily frame."""
    df = _synthetic_daily()
    enriched = TechnicalIndicators.add_all(df, interval="1d")
    enriched = enriched.dropna(subset=["SMA_50", "RSI"])
    return enriched


def test_fixture_has_enough_warmed_daily_bars():
    enriched = _enriched_daily()
    assert len(enriched) >= 60


def test_recommend_shape_and_fields():
    enriched = _enriched_daily()
    rec = recommend("TEST", AS_OF, df=enriched)

    assert rec.source == "quant"
    assert rec.model_id is None
    assert rec.prompt_version is None
    assert rec.ticker == "TEST"
    assert rec.as_of == AS_OF

    # snapshot_id is a 16-char lowercase hex string.
    assert len(rec.snapshot_id) == 16
    assert all(c in "0123456789abcdef" for c in rec.snapshot_id)

    assert rec.action.value in {"BUY", "HOLD", "SELL"}
    assert 0.0 <= rec.confidence <= 1.0
    assert rec.horizon_days == HORIZON_DAYS == 10

    assert rec.evidence  # non-empty
    assert all(isinstance(e, str) and "=" in e for e in rec.evidence)


def test_recommend_bullish_fixture_is_buy():
    """The engineered uptrend is a stable BUY."""
    enriched = _enriched_daily()
    rec = recommend("TEST", AS_OF, df=enriched)
    assert rec.action.value == "BUY"


def test_recommend_is_byte_identical_across_calls():
    enriched = _enriched_daily()
    rec1 = recommend("TEST", AS_OF, df=enriched)
    rec2 = recommend("TEST", AS_OF, df=_enriched_daily())
    # Full byte-level reproducibility, incl. snapshot_id + evidence.
    assert rec1.model_dump_json() == rec2.model_dump_json()
    assert rec1.snapshot_id == rec2.snapshot_id
    assert rec1.evidence == rec2.evidence


def test_compute_snapshot_id_same_df_same_id():
    enriched = _enriched_daily()
    a = compute_snapshot_id("TEST", AS_OF, enriched)
    b = compute_snapshot_id("TEST", AS_OF, _enriched_daily())
    assert a == b
    assert len(a) == 16


def test_compute_snapshot_id_one_cell_change_differs():
    enriched = _enriched_daily()
    before = compute_snapshot_id("TEST", AS_OF, enriched)
    mutated = enriched.copy()
    # Change a single cell within the snapshot tail / canonical columns.
    mutated.iloc[-1, mutated.columns.get_loc("Close")] += 1.0
    after = compute_snapshot_id("TEST", AS_OF, mutated)
    assert before != after


def test_build_evidence_fixed_order_and_formatting():
    summary = {
        "price": {"current": 188.85, "change_pct": 0.41},
        "moving_averages": {"SMA_20": 181.89, "SMA_50": 171.58},
        "momentum": {"RSI": 60.0, "MACD": 4.8897, "MACD_Signal": 4.8110},
        "volatility": {"ATR": 1.1, "BB_Width": 0.0935},
        "volume": {"Volume_Ratio": 1.0, "Volume": 1000000},
    }
    evidence = build_evidence(summary)
    assert evidence == [
        "price.current=188.8500",
        "price.change_pct=0.4100",
        "moving_averages.SMA_20=181.8900",
        "moving_averages.SMA_50=171.5800",
        "momentum.RSI=60.0000",
        "momentum.MACD=4.8897",
        "momentum.MACD_Signal=4.8110",
        "volatility.ATR=1.1000",
        "volatility.BB_Width=0.0935",
        "volume.Volume_Ratio=1.0000",
    ]


def test_build_evidence_skips_absent_fields():
    summary = {
        "price": {"current": 50.0},
        "moving_averages": {},
        "momentum": {},
        "volatility": {},
        "volume": {},
    }
    evidence = build_evidence(summary)
    assert evidence == ["price.current=50.0000"]


def test_indicator_math_on_known_inputs():
    """Known closes -> deterministic SMA/RSI values."""
    closes = [float(x) for x in range(1, 31)]  # strictly increasing 1..30
    df = pd.DataFrame(
        {
            "Open": closes,
            "High": [c + 0.5 for c in closes],
            "Low": [c - 0.5 for c in closes],
            "Close": closes,
            "Volume": [1000.0] * len(closes),
        }
    )
    df = TechnicalIndicators.add_moving_averages(df, short=5, long=10)
    # SMA_5 of the last 5 values (26,27,28,29,30) = 28.0
    assert df["SMA_5"].iloc[-1] == 28.0
    # SMA_10 of the last 10 values (21..30) mean = 25.5
    assert df["SMA_10"].iloc[-1] == 25.5

    df = TechnicalIndicators.add_rsi(df, period=14)
    # A strictly increasing series has no losses -> RSI saturates at 100.
    assert round(float(df["RSI"].iloc[-1]), 1) == 100.0


def test_regime_classification_on_uptrend_is_bullish():
    from quant.agents.regime_agent import MarketRegime, RegimeAgent

    enriched = _enriched_daily()
    classification = RegimeAgent().classify(enriched)
    assert classification.regime == MarketRegime.BULLISH_TREND
    assert 0.0 <= classification.confidence <= 1.0
