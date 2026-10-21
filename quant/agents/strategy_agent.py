"""
Strategy Agent (The Predictor)

Core agent that generates trading signals by analyzing:
- Historical price patterns (OHLCV)
- Technical indicator readings
- Market regime classification
- Lessons learned from past predictions (memory)

Outputs a structured prediction with:
- Signal (BUY / SELL / HOLD)
- Predicted price direction and magnitude
- Confidence level
- Reasoning chain

The rule-based mode is the offline default and needs no external services.
The optional LLM mode routes through the local gateway (Ollama); any failure
falls back to the rule-based scoring so the system always produces a signal.
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from quant.agents.regime_agent import RegimeAgent, RegimeClassification
from quant.config import Config
from quant.data.indicators import TechnicalIndicators


class Signal(str, Enum):
    """Trading signal types."""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


# JSON schema for the gateway-backed prediction (design 3.4).
_PREDICTION_SCHEMA = {
    "type": "object",
    "properties": {
        "signal": {"type": "string", "enum": ["BUY", "SELL", "HOLD"]},
        "confidence": {"type": "number"},
        "predicted_direction": {"type": "string", "enum": ["UP", "DOWN", "FLAT"]},
        "predicted_change_pct": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": [
        "signal",
        "confidence",
        "predicted_direction",
        "predicted_change_pct",
        "reasoning",
    ],
}


@dataclass
class Prediction:
    """Structured prediction output from the Strategy Agent."""
    signal: Signal
    confidence: float  # 0.0 to 1.0
    predicted_direction: str  # "UP", "DOWN", "FLAT"
    predicted_change_pct: float  # Expected % move over horizon
    horizon_days: int  # Legacy field name kept for API compatibility
    horizon_periods: int  # Number of periods (interval-dependent)
    interval: str  # e.g., "30m", "1d"
    reasoning: str
    regime_used: str
    timestamp: str  # ISO format of prediction datetime
    indicators_snapshot: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "signal": self.signal.value,
            "confidence": self.confidence,
            "predicted_direction": self.predicted_direction,
            "predicted_change_pct": self.predicted_change_pct,
            "horizon_days": self.horizon_days,
            "horizon_periods": self.horizon_periods,
            "interval": self.interval,
            "reasoning": self.reasoning,
            "regime_used": self.regime_used,
            "timestamp": self.timestamp,
            "indicators_snapshot": self.indicators_snapshot,
        }


class StrategyAgent:
    """
    The Strategy Agent analyzes market data and generates predictions.

    This agent supports two modes:
    1. Rule-based mode (default): Uses a systematic scoring approach based on
       technical indicators and regime classification. No external deps.
    2. Gateway LLM mode: Routes a structured prediction prompt through the
       local gateway (Ollama). Falls back to rule-based on any failure.

    The rule-based mode ensures the system works out of the box without
    external dependencies, while the gateway mode adds model-assisted reasoning.
    """

    def __init__(
        self,
        horizon: int = None,
        use_llm: bool = False,
        lessons: list[str] = None,
        interval: str = None,
        llm_provider: str = None,
        asset_class: str = None,
    ):
        self.horizon = horizon or Config.DEFAULT_PREDICTION_HORIZON
        self.interval = interval or Config.DEFAULT_INTERVAL
        self.asset_class = asset_class or Config.DEFAULT_ASSET_CLASS
        self.lessons: list[str] = lessons or []
        self.regime_agent = RegimeAgent()

        # Determine LLM provider.
        # Priority: explicit llm_provider > use_llm flag (gateway) > default.
        self.llm_provider = llm_provider or (
            "gateway" if use_llm else Config.DEFAULT_LLM_PROVIDER
        )

    def predict(
        self,
        df: pd.DataFrame,
        regime: Optional[RegimeClassification] = None,
    ) -> Prediction:
        """
        Generate a prediction based on the provided market data.

        Routes to the configured provider:
        - "rule_based": Multi-factor scoring (no external deps)
        - "gateway": Local gateway (Ollama), falling back to rule-based

        Args:
            df: Enriched DataFrame with OHLCV + technical indicators.
            regime: Pre-computed regime classification (optional).

        Returns:
            Prediction with signal, direction, confidence, and reasoning.
        """
        if regime is None:
            regime = self.regime_agent.classify(df)

        indicator_summary = TechnicalIndicators.summarize(df)

        if self.llm_provider == "gateway":
            return self._predict_with_gateway(df, regime, indicator_summary)
        else:
            return self._predict_rule_based(df, regime, indicator_summary)

    def _predict_rule_based(
        self,
        df: pd.DataFrame,
        regime: RegimeClassification,
        indicator_summary: dict,
    ) -> Prediction:
        """
        Rule-based prediction using a multi-factor scoring model.
        Each factor votes for BUY, SELL, or HOLD with a weight.
        """
        latest = df.iloc[-1]
        scores = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
        reasoning_parts = []

        # --- Factor 1: Regime alignment ---
        regime_factor = self._score_regime(regime)
        scores[regime_factor["signal"]] += regime_factor["weight"]
        reasoning_parts.append(f"Regime ({regime.regime.value}): {regime_factor['reason']}")

        # --- Factor 2: Moving Average position ---
        ma_factor = self._score_moving_averages(latest, df)
        scores[ma_factor["signal"]] += ma_factor["weight"]
        reasoning_parts.append(f"MA Analysis: {ma_factor['reason']}")

        # --- Factor 3: RSI momentum ---
        rsi_factor = self._score_rsi(latest, df)
        scores[rsi_factor["signal"]] += rsi_factor["weight"]
        reasoning_parts.append(f"RSI ({latest.get('RSI', 'N/A'):.1f}): {rsi_factor['reason']}")

        # --- Factor 4: MACD signal ---
        macd_factor = self._score_macd(latest, df)
        scores[macd_factor["signal"]] += macd_factor["weight"]
        reasoning_parts.append(f"MACD: {macd_factor['reason']}")

        # --- Factor 5: Volume confirmation ---
        vol_factor = self._score_volume(latest, df)
        scores[vol_factor["signal"]] += vol_factor["weight"]
        reasoning_parts.append(f"Volume: {vol_factor['reason']}")

        # --- Factor 6: Bollinger Band position ---
        bb_factor = self._score_bollinger(latest)
        scores[bb_factor["signal"]] += bb_factor["weight"]
        reasoning_parts.append(f"Bollinger: {bb_factor['reason']}")

        # Apply lessons learned (adjust scores based on past mistakes)
        scores = self._apply_lessons(scores, regime)

        # Determine final signal
        best_signal = max(scores, key=scores.get)
        total_score = sum(scores.values())
        confidence = scores[best_signal] / total_score if total_score > 0 else 0.33

        # Estimate magnitude
        predicted_change = self._estimate_magnitude(df, best_signal, confidence)

        # Determine direction
        if best_signal == "BUY":
            direction = "UP"
        elif best_signal == "SELL":
            direction = "DOWN"
        else:
            direction = "FLAT"

        timestamp = str(df.index[-1])

        return Prediction(
            signal=Signal(best_signal),
            confidence=round(min(confidence, 0.95), 3),
            predicted_direction=direction,
            predicted_change_pct=round(predicted_change, 2),
            horizon_days=self.horizon,
            horizon_periods=self.horizon,
            interval=self.interval,
            reasoning=" | ".join(reasoning_parts),
            regime_used=regime.regime.value,
            timestamp=timestamp,
            indicators_snapshot=indicator_summary,
        )

    def _score_regime(self, regime: RegimeClassification) -> dict:
        """Score based on market regime."""
        from quant.agents.regime_agent import MarketRegime

        if regime.regime == MarketRegime.BULLISH_TREND:
            return {"signal": "BUY", "weight": 2.0 * regime.confidence, "reason": "bullish trend favors buying"}
        elif regime.regime == MarketRegime.BEARISH_TREND:
            return {"signal": "SELL", "weight": 2.0 * regime.confidence, "reason": "bearish trend favors selling"}
        elif regime.regime == MarketRegime.HIGH_VOLATILITY:
            return {"signal": "HOLD", "weight": 1.5, "reason": "high volatility suggests caution"}
        elif regime.regime == MarketRegime.BREAKOUT:
            return {"signal": "BUY", "weight": 1.0 * regime.confidence, "reason": "potential breakout forming"}
        else:
            return {"signal": "HOLD", "weight": 1.0, "reason": "sideways market, no clear edge"}

    def _score_moving_averages(self, latest: pd.Series, df: pd.DataFrame) -> dict:
        """Score based on price vs moving averages and MA crossover."""
        # Find the SMA columns dynamically (could be SMA_20/SMA_50 or SMA_50/SMA_200)
        sma_cols = [c for c in df.columns if c.startswith("SMA_")]
        if len(sma_cols) < 2:
            return {"signal": "HOLD", "weight": 0.5, "reason": "insufficient MA data"}

        short_sma_col = sma_cols[0]  # Shorter period
        long_sma_col = sma_cols[1]   # Longer period

        if pd.isna(latest.get(short_sma_col)) or pd.isna(latest.get(long_sma_col)):
            return {"signal": "HOLD", "weight": 0.5, "reason": "insufficient MA data"}

        price = latest["Close"]
        sma_short = latest[short_sma_col]
        sma_long = latest[long_sma_col]

        above_both = price > sma_short and price > sma_long
        below_both = price < sma_short and price < sma_long
        golden_cross = sma_short > sma_long

        if above_both and golden_cross:
            return {"signal": "BUY", "weight": 2.0, "reason": "price above both MAs with golden cross"}
        elif above_both:
            return {"signal": "BUY", "weight": 1.5, "reason": "price above both MAs"}
        elif below_both and not golden_cross:
            return {"signal": "SELL", "weight": 2.0, "reason": "price below both MAs with death cross"}
        elif below_both:
            return {"signal": "SELL", "weight": 1.5, "reason": "price below both MAs"}
        else:
            return {"signal": "HOLD", "weight": 1.0, "reason": "price between MAs, mixed signal"}

    def _score_rsi(self, latest: pd.Series, df: pd.DataFrame) -> dict:
        """Score based on RSI levels and divergence."""
        rsi = latest.get("RSI")
        if rsi is None or pd.isna(rsi):
            return {"signal": "HOLD", "weight": 0.5, "reason": "no RSI data"}

        if rsi > 70:
            return {"signal": "SELL", "weight": 1.5, "reason": "overbought territory"}
        elif rsi > 60:
            return {"signal": "BUY", "weight": 0.5, "reason": "strong momentum but approaching overbought"}
        elif rsi < 30:
            return {"signal": "BUY", "weight": 1.5, "reason": "oversold territory, reversal likely"}
        elif rsi < 40:
            return {"signal": "SELL", "weight": 0.5, "reason": "weak momentum"}
        else:
            return {"signal": "HOLD", "weight": 0.5, "reason": "neutral RSI zone"}

    def _score_macd(self, latest: pd.Series, df: pd.DataFrame) -> dict:
        """Score based on MACD crossover and histogram."""
        macd = latest.get("MACD")
        signal_line = latest.get("MACD_Signal")
        histogram = latest.get("MACD_Histogram")

        if macd is None or pd.isna(macd):
            return {"signal": "HOLD", "weight": 0.5, "reason": "no MACD data"}

        # Check histogram direction (recent bars)
        if "MACD_Histogram" in df.columns:
            recent_hist = df["MACD_Histogram"].tail(5).dropna().values
            hist_increasing = len(recent_hist) >= 2 and recent_hist[-1] > recent_hist[-2]
        else:
            hist_increasing = False

        if macd > signal_line and histogram > 0:
            weight = 1.5 if hist_increasing else 1.0
            return {"signal": "BUY", "weight": weight, "reason": "MACD above signal, bullish"}
        elif macd < signal_line and histogram < 0:
            weight = 1.5 if not hist_increasing else 1.0
            return {"signal": "SELL", "weight": weight, "reason": "MACD below signal, bearish"}
        else:
            return {"signal": "HOLD", "weight": 0.5, "reason": "MACD at crossover point"}

    def _score_volume(self, latest: pd.Series, df: pd.DataFrame) -> dict:
        """Score based on volume patterns (confirmation)."""
        vol_ratio = latest.get("Volume_Ratio")
        if vol_ratio is None or pd.isna(vol_ratio):
            return {"signal": "HOLD", "weight": 0.3, "reason": "no volume data"}

        # High volume confirms the trend direction
        price_change = 0
        if len(df) >= 2:
            price_change = (latest["Close"] - df.iloc[-2]["Close"]) / df.iloc[-2]["Close"]

        if vol_ratio > 1.5 and price_change > 0:
            return {"signal": "BUY", "weight": 1.0, "reason": "high volume on up move (confirmation)"}
        elif vol_ratio > 1.5 and price_change < 0:
            return {"signal": "SELL", "weight": 1.0, "reason": "high volume on down move (confirmation)"}
        elif vol_ratio < 0.5:
            return {"signal": "HOLD", "weight": 0.5, "reason": "low volume, lack of conviction"}
        else:
            return {"signal": "HOLD", "weight": 0.3, "reason": "normal volume, no strong signal"}

    def _score_bollinger(self, latest: pd.Series) -> dict:
        """Score based on Bollinger Band position."""
        close = latest.get("Close")
        bb_upper = latest.get("BB_Upper")
        bb_lower = latest.get("BB_Lower")

        if bb_upper is None or pd.isna(bb_upper):
            return {"signal": "HOLD", "weight": 0.3, "reason": "no Bollinger data"}

        # Price touching or exceeding bands
        if close >= bb_upper:
            return {"signal": "SELL", "weight": 1.0, "reason": "price at upper Bollinger Band (resistance)"}
        elif close <= bb_lower:
            return {"signal": "BUY", "weight": 1.0, "reason": "price at lower Bollinger Band (support)"}
        else:
            # Position within band (0 = lower, 1 = upper)
            band_position = (close - bb_lower) / (bb_upper - bb_lower)
            if band_position > 0.8:
                return {"signal": "SELL", "weight": 0.5, "reason": "price near upper band"}
            elif band_position < 0.2:
                return {"signal": "BUY", "weight": 0.5, "reason": "price near lower band"}
            else:
                return {"signal": "HOLD", "weight": 0.3, "reason": "price in middle of bands"}

    def _apply_lessons(self, scores: dict, regime: RegimeClassification) -> dict:
        """
        Adjust scores based on lessons learned from past mistakes.
        This implements the feedback memory loop.
        """
        if not self.lessons:
            return scores

        # Parse lessons for relevant adjustments
        for lesson in self.lessons[-Config.MAX_LESSONS_IN_MEMORY:]:
            lesson_lower = lesson.lower()

            # If lessons mention false breakouts, reduce breakout confidence
            if "false breakout" in lesson_lower or "false signal" in lesson_lower:
                if regime.regime.value == "BREAKOUT":
                    scores["HOLD"] += 1.0
                    scores["BUY"] *= 0.7

            # If lessons mention missing rallies, be slightly more aggressive
            if "missed rally" in lesson_lower or "too cautious" in lesson_lower:
                scores["BUY"] *= 1.2

            # If lessons mention not cutting losses, be more defensive
            if "cut losses" in lesson_lower or "held too long" in lesson_lower:
                scores["SELL"] *= 1.2

            # If lessons mention overtrading in sideways, favor hold
            if "overtrading" in lesson_lower or "sideways" in lesson_lower:
                if regime.regime.value == "SIDEWAYS":
                    scores["HOLD"] += 1.5

        return scores

    def _estimate_magnitude(
        self, df: pd.DataFrame, signal: str, confidence: float
    ) -> float:
        """
        Estimate the expected price change magnitude over the horizon.

        For intraday intervals, the expected move is smaller per period.
        We scale by sqrt(horizon_in_days_equivalent) to account for the
        shorter time frame.
        """
        latest = df.iloc[-1]
        atr = latest.get("ATR", 0)
        price = latest["Close"]

        # Convert horizon (in periods) to equivalent daily fraction
        # For 30m: 2 periods = 1 hour = ~0.154 trading days
        periods_per_day = Config.get_periods_per_day(self.interval)
        horizon_in_days = self.horizon / periods_per_day

        if atr > 0 and price > 0:
            # Expected move = ATR * sqrt(horizon_in_days) * confidence factor
            # ATR is already in per-period units (computed on same interval bars)
            daily_move_pct = (atr / price) * 100
            expected_move = daily_move_pct * np.sqrt(self.horizon) * confidence
        else:
            # Fallback: estimate from recent volatility
            returns = df["Close"].pct_change().tail(20).dropna()
            if len(returns) > 0:
                per_period_vol = returns.std() * 100
                expected_move = per_period_vol * np.sqrt(self.horizon) * confidence
            else:
                expected_move = 0.5 if Config.is_intraday(self.interval) else 2.0

        # Cap at reasonable limits based on interval
        if Config.is_intraday(self.interval):
            expected_move = min(expected_move, 5.0)  # Intraday moves are smaller
        else:
            expected_move = min(expected_move, 15.0)

        if signal == "SELL":
            return -expected_move
        elif signal == "HOLD":
            return expected_move * 0.2
        else:
            return expected_move

    def add_lesson(self, lesson: str):
        """Add a lesson learned from backtesting feedback."""
        self.lessons.append(lesson)
        # Keep only the most recent lessons
        if len(self.lessons) > Config.MAX_LESSONS_IN_MEMORY:
            self.lessons = self.lessons[-Config.MAX_LESSONS_IN_MEMORY:]

    def _predict_with_gateway(
        self,
        df: pd.DataFrame,
        regime: RegimeClassification,
        indicator_summary: dict,
    ) -> Prediction:
        """
        Gateway-backed prediction. Sends a structured prompt through the local
        gateway (Ollama) requesting a small JSON prediction object.

        Parses the response and falls back to rule-based scoring on any failure
        (gateway unreachable, invalid JSON, or missing fields).
        """
        try:
            from gateway.client import generate_sync

            # Build context
            price_history = self._format_price_history(df.tail(20))
            lessons_text = "\n".join(f"- {l}" for l in self.lessons[-10:]) if self.lessons else "None yet."

            # Asset-class specific system prompt
            asset_context = {
                "forex": (
                    "You are a quantitative trading AI analyzing "
                    "forex (currency pair) data. Forex markets are highly liquid, "
                    "trade 24/5, and exhibit strong mean-reversion at intraday timeframes. "
                    "Pay close attention to support/resistance levels, momentum exhaustion, "
                    "and session overlaps (London/NY)."
                ),
                "commodity": (
                    "You are a quantitative trading AI analyzing "
                    "commodity futures data. Commodities are influenced by supply/demand "
                    "fundamentals, geopolitical events, and USD correlation. "
                    "Pay attention to momentum, volume spikes, and mean reversion."
                ),
                "stock": (
                    "You are a quantitative trading AI analyzing stock data. "
                    "Focus on momentum, volume confirmation, and regime classification."
                ),
            }

            system_prompt = (
                f"{asset_context.get(self.asset_class, asset_context['stock'])}\n\n"
                f"Each candlestick represents a {self.interval} period. "
                f"You are predicting the next {self.horizon} periods "
                f"({self.horizon} x {self.interval}).\n\n"
                "Respond with ONLY valid JSON matching the schema: "
                '{"signal": "BUY|SELL|HOLD", "confidence": 0.0-1.0, '
                '"predicted_direction": "UP|DOWN|FLAT", "predicted_change_pct": float, '
                '"reasoning": "brief explanation"}'
            )

            user_prompt = (
                f"## Asset: {self.asset_class.upper()} | Interval: {self.interval}\n\n"
                f"## Market Regime\n{regime.regime.value} (confidence: {regime.confidence:.1%})\n"
                f"Analysis: {regime.reasoning}\n\n"
                f"## Current Indicators\n{json.dumps(indicator_summary, indent=2)}\n\n"
                f"## Recent Price History (last 20 bars, {self.interval} each)\n{price_history}\n\n"
                f"## Prediction Horizon\n{self.horizon} periods of {self.interval}\n\n"
                f"## Lessons from Past Predictions\n{lessons_text}\n\n"
                "Generate your prediction JSON:"
            )

            result_raw = generate_sync(
                user_prompt,
                route="quick",
                system=system_prompt,
                json_schema=_PREDICTION_SCHEMA,
                temperature=Config.LLM_TEMPERATURE,
                max_tokens=256,
            )

            if not result_raw.get("valid"):
                raise ValueError(
                    f"gateway returned invalid prediction: {result_raw.get('error')}"
                )

            result = json.loads(result_raw["text"])
            model_id = result_raw.get("model_id")

            timestamp = str(df.index[-1])

            return Prediction(
                signal=Signal(result["signal"].upper()),
                confidence=round(float(result["confidence"]), 3),
                predicted_direction=result["predicted_direction"].upper(),
                predicted_change_pct=round(float(result["predicted_change_pct"]), 2),
                horizon_days=self.horizon,
                horizon_periods=self.horizon,
                interval=self.interval,
                reasoning=f"[gateway/{model_id}] {result.get('reasoning', 'No reasoning provided')}",
                regime_used=regime.regime.value,
                timestamp=timestamp,
                indicators_snapshot=indicator_summary,
            )

        except Exception as e:
            print(f"Gateway prediction failed ({e}), falling back to rule-based.")
            return self._predict_rule_based(df, regime, indicator_summary)

    def _format_price_history(self, df: pd.DataFrame) -> str:
        """Format recent price bars for LLM context."""
        lines = []
        for idx, row in df.iterrows():
            if Config.is_intraday(self.interval):
                date_str = idx.strftime("%Y-%m-%d %H:%M")
            else:
                date_str = str(idx.date())
            lines.append(
                f"{date_str}: O={row['Open']:.4f} H={row['High']:.4f} "
                f"L={row['Low']:.4f} C={row['Close']:.4f} V={int(row['Volume'])}"
            )
        return "\n".join(lines)
