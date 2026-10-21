"""
Market Regime Agent

Classifies the current market environment into one of several regimes:
- BULLISH_TREND: Strong upward momentum
- BEARISH_TREND: Strong downward momentum
- SIDEWAYS: Consolidation / range-bound
- HIGH_VOLATILITY: Elevated volatility without clear direction
- BREAKOUT: Potential breakout forming

Uses a combination of technical indicator analysis and optional LLM reasoning.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from quant.config import Config


class MarketRegime(str, Enum):
    """Possible market regime classifications."""
    BULLISH_TREND = "BULLISH_TREND"
    BEARISH_TREND = "BEARISH_TREND"
    SIDEWAYS = "SIDEWAYS"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    BREAKOUT = "BREAKOUT"


@dataclass
class RegimeClassification:
    """Result of regime classification."""
    regime: MarketRegime
    confidence: float  # 0.0 to 1.0
    reasoning: str
    indicators_used: dict


class RegimeAgent:
    """
    Analyzes the market data over a lookback window and classifies
    the current market regime using technical indicator rules.

    The agent examines:
    - Moving average alignment and slope
    - RSI levels and trend
    - Bollinger Band width (volatility)
    - ATR relative to price (volatility)
    - Price position relative to moving averages
    - Volume patterns
    """

    def __init__(self, lookback: int = None):
        self.lookback = lookback or Config.REGIME_LOOKBACK

    def classify(self, df: pd.DataFrame) -> RegimeClassification:
        """
        Classify the market regime from enriched OHLCV data.

        Args:
            df: DataFrame with OHLCV + technical indicators.
                Must include: SMA_20, SMA_50, RSI, ATR, BB_Width, Volume_Ratio.

        Returns:
            RegimeClassification with regime, confidence, and reasoning.
        """
        # Use the most recent lookback bars
        window = df.tail(self.lookback).copy()

        if len(window) < 20:
            return RegimeClassification(
                regime=MarketRegime.SIDEWAYS,
                confidence=0.3,
                reasoning="Insufficient data for reliable classification.",
                indicators_used={},
            )

        # Extract key metrics
        signals = self._compute_signals(window)

        # Score each regime
        scores = self._score_regimes(signals)

        # Pick the highest scoring regime
        best_regime = max(scores, key=scores.get)
        best_score = scores[best_regime]

        # Normalize confidence
        total_score = sum(scores.values())
        confidence = best_score / total_score if total_score > 0 else 0.5

        # Build reasoning
        reasoning = self._build_reasoning(signals, best_regime, confidence)

        return RegimeClassification(
            regime=best_regime,
            confidence=round(confidence, 3),
            reasoning=reasoning,
            indicators_used=signals,
        )

    def _compute_signals(self, window: pd.DataFrame) -> dict:
        """Compute regime-relevant signals from the data window."""
        latest = window.iloc[-1]
        close_prices = window["Close"].values

        signals = {}

        # 1. Moving Average Trend Direction (slope over lookback)
        if "SMA_20" in window.columns:
            sma20_values = window["SMA_20"].dropna().values
            if len(sma20_values) >= 10:
                sma20_slope = (sma20_values[-1] - sma20_values[-10]) / sma20_values[-10] * 100
                signals["sma20_slope_pct"] = round(float(sma20_slope), 3)

        if "SMA_50" in window.columns:
            sma50_values = window["SMA_50"].dropna().values
            if len(sma50_values) >= 10:
                sma50_slope = (sma50_values[-1] - sma50_values[-10]) / sma50_values[-10] * 100
                signals["sma50_slope_pct"] = round(float(sma50_slope), 3)

        # 2. Price vs Moving Averages
        if "SMA_20" in window.columns and not pd.isna(latest["SMA_20"]):
            signals["price_vs_sma20"] = round(
                float((latest["Close"] - latest["SMA_20"]) / latest["SMA_20"] * 100), 3
            )
        if "SMA_50" in window.columns and not pd.isna(latest["SMA_50"]):
            signals["price_vs_sma50"] = round(
                float((latest["Close"] - latest["SMA_50"]) / latest["SMA_50"] * 100), 3
            )

        # 3. MA Crossover state
        if "SMA_20" in window.columns and "SMA_50" in window.columns:
            if not pd.isna(latest["SMA_20"]) and not pd.isna(latest["SMA_50"]):
                signals["ma_bullish_cross"] = bool(latest["SMA_20"] > latest["SMA_50"])

        # 4. RSI
        if "RSI" in window.columns and not pd.isna(latest["RSI"]):
            signals["rsi"] = round(float(latest["RSI"]), 2)
            rsi_values = window["RSI"].dropna().values
            if len(rsi_values) >= 10:
                signals["rsi_avg"] = round(float(np.mean(rsi_values[-10:])), 2)

        # 5. Volatility (BB Width and ATR)
        if "BB_Width" in window.columns:
            bb_values = window["BB_Width"].dropna().values
            if len(bb_values) > 0:
                signals["bb_width_current"] = round(float(bb_values[-1]), 4)
                signals["bb_width_avg"] = round(float(np.mean(bb_values)), 4)
                # Is volatility expanding or contracting?
                if len(bb_values) >= 10:
                    recent_avg = np.mean(bb_values[-5:])
                    older_avg = np.mean(bb_values[-10:-5])
                    signals["volatility_expanding"] = bool(recent_avg > older_avg * 1.1)

        if "ATR" in window.columns and not pd.isna(latest["ATR"]):
            signals["atr_pct"] = round(float(latest["ATR"] / latest["Close"] * 100), 3)

        # 6. Price range (how tight is the recent range?)
        recent_prices = close_prices[-20:] if len(close_prices) >= 20 else close_prices
        price_range_pct = (max(recent_prices) - min(recent_prices)) / np.mean(recent_prices) * 100
        signals["price_range_pct"] = round(float(price_range_pct), 3)

        # 7. Directional movement (net price change over window)
        net_change = (close_prices[-1] - close_prices[0]) / close_prices[0] * 100
        signals["net_change_pct"] = round(float(net_change), 3)

        # 8. Volume trend
        if "Volume_Ratio" in window.columns:
            vol_ratio_values = window["Volume_Ratio"].dropna().values
            if len(vol_ratio_values) > 0:
                signals["avg_volume_ratio"] = round(float(np.mean(vol_ratio_values[-10:])), 2)

        return signals

    def _score_regimes(self, signals: dict) -> dict:
        """Score each regime based on the computed signals."""
        scores = {
            MarketRegime.BULLISH_TREND: 0.0,
            MarketRegime.BEARISH_TREND: 0.0,
            MarketRegime.SIDEWAYS: 0.0,
            MarketRegime.HIGH_VOLATILITY: 0.0,
            MarketRegime.BREAKOUT: 0.0,
        }

        # --- Bullish signals ---
        if signals.get("sma20_slope_pct", 0) > 1.0:
            scores[MarketRegime.BULLISH_TREND] += 2.0
        if signals.get("sma50_slope_pct", 0) > 0.5:
            scores[MarketRegime.BULLISH_TREND] += 1.5
        if signals.get("price_vs_sma20", 0) > 1.0:
            scores[MarketRegime.BULLISH_TREND] += 1.0
        if signals.get("price_vs_sma50", 0) > 2.0:
            scores[MarketRegime.BULLISH_TREND] += 1.5
        if signals.get("ma_bullish_cross", False):
            scores[MarketRegime.BULLISH_TREND] += 1.0
        if 50 < signals.get("rsi", 50) < 70:
            scores[MarketRegime.BULLISH_TREND] += 1.0
        if signals.get("net_change_pct", 0) > 3.0:
            scores[MarketRegime.BULLISH_TREND] += 2.0

        # --- Bearish signals ---
        if signals.get("sma20_slope_pct", 0) < -1.0:
            scores[MarketRegime.BEARISH_TREND] += 2.0
        if signals.get("sma50_slope_pct", 0) < -0.5:
            scores[MarketRegime.BEARISH_TREND] += 1.5
        if signals.get("price_vs_sma20", 0) < -1.0:
            scores[MarketRegime.BEARISH_TREND] += 1.0
        if signals.get("price_vs_sma50", 0) < -2.0:
            scores[MarketRegime.BEARISH_TREND] += 1.5
        if not signals.get("ma_bullish_cross", True):
            scores[MarketRegime.BEARISH_TREND] += 1.0
        if signals.get("rsi", 50) < 40:
            scores[MarketRegime.BEARISH_TREND] += 1.0
        if signals.get("net_change_pct", 0) < -3.0:
            scores[MarketRegime.BEARISH_TREND] += 2.0

        # --- Sideways signals ---
        if abs(signals.get("net_change_pct", 0)) < 2.0:
            scores[MarketRegime.SIDEWAYS] += 2.0
        if signals.get("price_range_pct", 10) < 5.0:
            scores[MarketRegime.SIDEWAYS] += 2.0
        if abs(signals.get("sma20_slope_pct", 0)) < 0.5:
            scores[MarketRegime.SIDEWAYS] += 1.5
        if 40 < signals.get("rsi", 50) < 60:
            scores[MarketRegime.SIDEWAYS] += 1.0
        if not signals.get("volatility_expanding", False):
            scores[MarketRegime.SIDEWAYS] += 0.5

        # --- High Volatility signals ---
        if signals.get("atr_pct", 0) > 2.5:
            scores[MarketRegime.HIGH_VOLATILITY] += 2.0
        if signals.get("bb_width_current", 0) > signals.get("bb_width_avg", 0) * 1.5:
            scores[MarketRegime.HIGH_VOLATILITY] += 2.0
        if signals.get("price_range_pct", 0) > 8.0:
            scores[MarketRegime.HIGH_VOLATILITY] += 1.5
        if signals.get("avg_volume_ratio", 1.0) > 1.5:
            scores[MarketRegime.HIGH_VOLATILITY] += 1.0

        # --- Breakout signals ---
        if signals.get("volatility_expanding", False):
            scores[MarketRegime.BREAKOUT] += 2.0
        if signals.get("avg_volume_ratio", 1.0) > 1.8:
            scores[MarketRegime.BREAKOUT] += 2.0
        if signals.get("bb_width_current", 0) < signals.get("bb_width_avg", 0) * 0.7:
            # Squeeze before breakout
            scores[MarketRegime.BREAKOUT] += 1.5
        if abs(signals.get("net_change_pct", 0)) > 4.0 and signals.get("avg_volume_ratio", 1.0) > 1.3:
            scores[MarketRegime.BREAKOUT] += 2.0

        # Add a small base score for sideways to avoid all-zero edge cases
        scores[MarketRegime.SIDEWAYS] += 1.0

        return scores

    def _build_reasoning(
        self, signals: dict, regime: MarketRegime, confidence: float
    ) -> str:
        """Build a human-readable reasoning string."""
        parts = [f"Market classified as {regime.value} (confidence: {confidence:.1%})."]

        if regime == MarketRegime.BULLISH_TREND:
            parts.append("Key factors: ")
            factors = []
            if signals.get("net_change_pct", 0) > 0:
                factors.append(f"net price change +{signals['net_change_pct']:.1f}%")
            if signals.get("ma_bullish_cross"):
                factors.append("SMA20 above SMA50 (golden cross)")
            if signals.get("rsi", 0) > 50:
                factors.append(f"RSI at {signals.get('rsi', 0):.0f} (bullish momentum)")
            parts.append(", ".join(factors) + ".")

        elif regime == MarketRegime.BEARISH_TREND:
            parts.append("Key factors: ")
            factors = []
            if signals.get("net_change_pct", 0) < 0:
                factors.append(f"net price change {signals['net_change_pct']:.1f}%")
            if not signals.get("ma_bullish_cross", True):
                factors.append("SMA20 below SMA50 (death cross)")
            if signals.get("rsi", 100) < 50:
                factors.append(f"RSI at {signals.get('rsi', 0):.0f} (bearish momentum)")
            parts.append(", ".join(factors) + ".")

        elif regime == MarketRegime.SIDEWAYS:
            parts.append(
                f"Price range is tight ({signals.get('price_range_pct', 0):.1f}%), "
                f"net change minimal ({signals.get('net_change_pct', 0):.1f}%)."
            )

        elif regime == MarketRegime.HIGH_VOLATILITY:
            parts.append(
                f"ATR is {signals.get('atr_pct', 0):.2f}% of price, "
                f"Bollinger width {signals.get('bb_width_current', 0):.4f}."
            )

        elif regime == MarketRegime.BREAKOUT:
            parts.append(
                f"Volatility expanding with volume ratio at "
                f"{signals.get('avg_volume_ratio', 1.0):.1f}x average."
            )

        return " ".join(parts)

    def get_regime_context(self, df: pd.DataFrame) -> str:
        """
        Get a formatted string suitable for passing to the Strategy Agent.

        Args:
            df: Enriched DataFrame.

        Returns:
            Formatted string describing the current market regime.
        """
        classification = self.classify(df)
        return (
            f"MARKET REGIME: {classification.regime.value}\n"
            f"CONFIDENCE: {classification.confidence:.1%}\n"
            f"ANALYSIS: {classification.reasoning}\n"
            f"SIGNALS: {classification.indicators_used}"
        )
