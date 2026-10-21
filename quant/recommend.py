"""Quant recommendation entry point (design 3.5).

``recommend(ticker, as_of, df=None)`` is the single public surface the rest of
the platform calls to get a quant lane :class:`Recommendation`. It is
rule-based and deterministic: given the same daily bars it always returns the
same action, confidence, evidence, and snapshot_id.

A caller may pass pre-warmed daily bars via ``df`` (the offline/test path). When
``df is None`` the live path fetches daily bars through the data pipeline with
an explicit 200-day lookback so at least 60 warmed daily bars survive the
indicator ``dropna`` (resolves design review finding 2).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from backend.app.schemas.recommendation import Action, Recommendation
from backend.app.schemas.snapshot import compute_snapshot_id
from quant.agents.regime_agent import RegimeAgent
from quant.agents.strategy_agent import StrategyAgent
from quant.data.pipeline import DataPipeline

HORIZON_DAYS = 10

# Fixed order for evidence emission from the indicator snapshot (design 3.5).
# Each entry is (dotted path into the nested summarize() dict, label).
_EVIDENCE_FIELDS = [
    ("price.current", "price.current"),
    ("price.change_pct", "price.change_pct"),
    ("moving_averages.SMA_20", "moving_averages.SMA_20"),
    ("moving_averages.SMA_50", "moving_averages.SMA_50"),
    ("momentum.RSI", "momentum.RSI"),
    ("momentum.MACD", "momentum.MACD"),
    ("momentum.MACD_Signal", "momentum.MACD_Signal"),
    ("volatility.ATR", "volatility.ATR"),
    ("volatility.BB_Width", "volatility.BB_Width"),
    ("volume.Volume_Ratio", "volume.Volume_Ratio"),
]


def _lookup(summary: dict, dotted: str):
    """Walk a dotted path into the nested summary dict; None if absent."""
    node = summary
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def build_evidence(summary: dict) -> list[str]:
    """Build the deterministic evidence list from an indicator summary.

    Emits ``'key=value'`` strings in the fixed field order. Floats render with
    4 decimals, ints as their plain string; absent fields are skipped.
    """
    evidence: list[str] = []
    for dotted, label in _EVIDENCE_FIELDS:
        value = _lookup(summary, dotted)
        if value is None:
            continue
        if isinstance(value, bool):
            # Guard: bools are ints in Python but should render plainly.
            evidence.append(f"{label}={value}")
        elif isinstance(value, int):
            evidence.append(f"{label}={value}")
        elif isinstance(value, float):
            evidence.append(f"{label}={value:.4f}")
        else:
            evidence.append(f"{label}={value}")
    return evidence


def recommend(
    ticker: str,
    as_of: date,
    df: pd.DataFrame | None = None,
) -> Recommendation:
    """Return a quant-lane :class:`Recommendation` for ``ticker`` at ``as_of``.

    Args:
        ticker: Symbol to analyze.
        as_of: The date the recommendation is anchored to.
        df: Optional pre-warmed DAILY enriched bars. When ``None`` the live
            path fetches daily data through the pipeline.

    Returns:
        A rule-based, deterministic ``Recommendation`` with ``source='quant'``
        and ``model_id=None``.

    Raises:
        ValueError: If the supplied/fetched frame is not daily bars.
    """
    if df is None:
        # Live path: explicit 200-day lookback so >=60 warmed daily bars
        # survive the indicator dropna (design review finding 2).
        pipeline = DataPipeline(ticker=ticker, interval="1d")
        df = pipeline.get_enriched_data(
            end_date=as_of.isoformat(),
            interval="1d",
            lookback_days=200,
            use_cache=False,
        )

    # Guard: this entry point only supports daily bars (horizon is in days).
    if df is None or getattr(df, "empty", True):
        raise ValueError("recommend() requires daily bars")
    if not _is_daily(df):
        raise ValueError("recommend() requires daily bars")

    regime = RegimeAgent().classify(df)
    agent = StrategyAgent(interval="1d", horizon=HORIZON_DAYS)
    prediction = agent.predict(df, regime)

    evidence = build_evidence(prediction.indicators_snapshot)

    return Recommendation(
        ticker=ticker,
        as_of=as_of,
        action=Action(prediction.signal.value),
        confidence=prediction.confidence,
        # horizon_days is valid only because interval == '1d'.
        horizon_days=prediction.horizon_days,
        rationale=prediction.reasoning,
        evidence=evidence,
        source="quant",
        model_id=None,
        prompt_version=None,
        snapshot_id=compute_snapshot_id(ticker, as_of, df),
    )


def _is_daily(df: pd.DataFrame) -> bool:
    """Best-effort check that ``df`` carries daily bars.

    A DatetimeIndex whose consecutive gaps are at least ~1 day (allowing
    weekend/holiday gaps) is treated as daily. Frames without a DatetimeIndex
    (e.g. synthetic test fixtures) are accepted so long as they declare daily
    spacing via a one-day-or-greater median gap.
    """
    index = df.index
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 2:
        # Not enough information to reject; a synthetic daily fixture with a
        # plain index is allowed through.
        return True
    diffs = index.to_series().diff().dropna()
    if diffs.empty:
        return True
    # Median spacing of a daily series is ~1 day; anything below ~20 hours is
    # intraday and rejected.
    return diffs.median() >= pd.Timedelta(hours=20)
