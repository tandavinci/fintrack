"""
Backtesting Engine - The Verification Loop

Implements the core training/verification cycle:
1. CUT-OFF: Feed agent historical data up to a specific point
2. PREDICTION: Ask agent what will happen over the next N periods
3. REVEAL: Pull the actual data for that period
4. FEEDBACK: Compare prediction vs reality, compute error
5. MEMORY: Feed lessons learned back into the agent's context

This is how the agent "follows instructions as part of training" —
each iteration refines its behavior through accumulated lessons.

Supports both daily and intraday intervals. When using 30m candles,
each "period" is a 30-minute bar, not a full day.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from quant.agents.regime_agent import RegimeAgent
from quant.agents.strategy_agent import Prediction, Signal, StrategyAgent
from quant.config import Config
from quant.data.pipeline import DataPipeline


def _format_timestamp(ts, interval: str) -> str:
    """Format a pandas Timestamp appropriately for the interval."""
    if Config.is_intraday(interval):
        return ts.strftime("%Y-%m-%d %H:%M")
    return str(ts.date())


@dataclass
class BacktestResult:
    """Result of a single prediction-verification cycle."""
    prediction: Prediction
    actual_change_pct: float
    actual_direction: str  # "UP", "DOWN", "FLAT"
    direction_correct: bool
    signal_profitable: bool
    error_pct: float  # Absolute error between predicted and actual change
    lesson: str
    prediction_date: str
    verification_period: str  # "start to end"


@dataclass
class BacktestReport:
    """Aggregate report of all backtesting results."""
    ticker: str
    interval: str
    total_predictions: int
    direction_accuracy: float  # % of correct direction calls
    signal_accuracy: float  # % of profitable signals
    avg_error_pct: float
    total_return_pct: float  # Cumulative return if following signals
    sharpe_ratio: float
    max_drawdown_pct: float
    signal_profitability: float  # Alias for frontend compatibility
    results: list[BacktestResult] = field(default_factory=list)
    lessons_learned: list[str] = field(default_factory=list)
    regime_performance: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "interval": self.interval,
            "total_predictions": self.total_predictions,
            "direction_accuracy": round(self.direction_accuracy, 3),
            "signal_accuracy": round(self.signal_accuracy, 3),
            "signal_profitability": round(self.signal_profitability, 3),
            "avg_error_pct": round(self.avg_error_pct, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "lessons_learned": self.lessons_learned,
            "regime_performance": self.regime_performance,
            "results": [
                {
                    "prediction_date": r.prediction_date,
                    "signal": r.prediction.signal.value,
                    "predicted_change": r.prediction.predicted_change_pct,
                    "actual_change": round(r.actual_change_pct, 2),
                    "direction_correct": r.direction_correct,
                    "signal_profitable": r.signal_profitable,
                    "regime": r.prediction.regime_used,
                    "lesson": r.lesson,
                }
                for r in self.results
            ],
        }


class BacktestEngine:
    """
    Runs the verification loop over historical data.

    The engine walks through historical data step by step:
    - At each step, it gives the agent only data up to that point (the cut-off)
    - The agent makes a prediction about the next N periods
    - The engine reveals what actually happened
    - The error is computed and a lesson is generated
    - The lesson is fed back to the agent for the next iteration

    Supports any interval: '30m', '1h', '1d', etc.
    For intraday intervals, horizon represents periods (e.g., 2 = 1 hour for 30m).
    """

    def __init__(
        self,
        ticker: str = None,
        horizon: int = None,
        lookback: int = None,
        step_size: int = None,
        use_llm: bool = False,
        interval: str = None,
        asset_class: str = None,
        llm_provider: str = None,
    ):
        self.ticker = ticker or Config.DEFAULT_TICKER
        self.interval = interval or Config.DEFAULT_INTERVAL
        self.asset_class = asset_class or Config.DEFAULT_ASSET_CLASS
        self.horizon = horizon or Config.DEFAULT_PREDICTION_HORIZON
        self.lookback = lookback or Config.REGIME_LOOKBACK
        self.step_size = step_size or max(1, self.horizon // 2)  # Overlap predictions
        self.use_llm = use_llm

        # Determine llm_provider
        if llm_provider:
            self._llm_provider = llm_provider
        elif use_llm:
            self._llm_provider = "gateway"
        else:
            self._llm_provider = Config.DEFAULT_LLM_PROVIDER

        self.pipeline = DataPipeline(self.ticker, interval=self.interval, asset_class=self.asset_class)
        self.regime_agent = RegimeAgent(lookback=self.lookback)
        self.strategy_agent = StrategyAgent(
            horizon=self.horizon,
            interval=self.interval,
            llm_provider=self._llm_provider,
            asset_class=self.asset_class,
        )

    def run(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        lookback_days: int = None,
    ) -> BacktestReport:
        """
        Run the full backtesting loop.

        Args:
            start_date: Start of the data period.
            end_date: End of the data period.
            lookback_days: How many calendar days of data to use.
                           Auto-capped to 60 for intraday intervals.

        Returns:
            BacktestReport with all results and lessons learned.
        """
        # Enforce intraday lookback cap
        if lookback_days is None:
            lookback_days = Config.get_max_lookback_days(self.interval)
        elif Config.is_intraday(self.interval):
            lookback_days = min(lookback_days, Config.INTRADAY_MAX_LOOKBACK_DAYS)

        # Step 1: Fetch all historical data
        full_data = self.pipeline.get_enriched_data(
            start_date=start_date,
            end_date=end_date,
            lookback_days=lookback_days,
            use_cache=False,
        )

        if len(full_data) < self.lookback + self.horizon + 10:
            raise ValueError(
                f"Not enough data for backtesting. Got {len(full_data)} bars, "
                f"need at least {self.lookback + self.horizon + 10}."
            )

        results: list[BacktestResult] = []
        lessons: list[str] = []

        # Interval label for printing
        period_label = "periods" if Config.is_intraday(self.interval) else "days"
        first_ts = _format_timestamp(full_data.index[0], self.interval)
        last_ts = _format_timestamp(full_data.index[-1], self.interval)

        # Step 2: Walk through the data
        start_idx = self.lookback
        end_idx = len(full_data) - self.horizon

        print(f"\n{'='*60}")
        print(f"BACKTESTING: {self.ticker} ({self.interval} interval)")
        print(f"Data range: {first_ts} to {last_ts}")
        print(f"Total bars: {len(full_data)} | Prediction horizon: {self.horizon} {period_label}")
        print(f"Step size: {self.step_size} | Iterations: ~{(end_idx - start_idx) // self.step_size}")
        print(f"{'='*60}\n")

        iteration = 0
        for cut_off_idx in range(start_idx, end_idx, self.step_size):
            iteration += 1

            # === CUT-OFF ===
            visible_data = self.pipeline.get_slice(full_data, cut_off_idx, self.lookback)

            if len(visible_data) < 20:
                continue

            # === PREDICTION ===
            regime = self.regime_agent.classify(visible_data)
            prediction = self.strategy_agent.predict(visible_data, regime)

            # === REVEAL ===
            future_data = self.pipeline.get_future_slice(
                full_data, cut_off_idx, self.horizon
            )

            if len(future_data) < 2:
                continue

            # Calculate actual outcome
            actual_start_price = visible_data.iloc[-1]["Close"]
            actual_end_price = future_data.iloc[-1]["Close"]
            actual_change_pct = (actual_end_price - actual_start_price) / actual_start_price * 100

            if actual_change_pct > 0.5:
                actual_direction = "UP"
            elif actual_change_pct < -0.5:
                actual_direction = "DOWN"
            else:
                actual_direction = "FLAT"

            # === FEEDBACK ===
            direction_correct = prediction.predicted_direction == actual_direction
            signal_profitable = self._is_signal_profitable(
                prediction.signal, actual_change_pct
            )
            error_pct = abs(prediction.predicted_change_pct - actual_change_pct)

            # === MEMORY ===
            lesson = self._generate_lesson(
                prediction, actual_change_pct, actual_direction, regime
            )
            self.strategy_agent.add_lesson(lesson)
            lessons.append(lesson)

            pred_ts = _format_timestamp(visible_data.index[-1], self.interval)
            future_start_ts = _format_timestamp(future_data.index[0], self.interval)
            future_end_ts = _format_timestamp(future_data.index[-1], self.interval)

            result = BacktestResult(
                prediction=prediction,
                actual_change_pct=actual_change_pct,
                actual_direction=actual_direction,
                direction_correct=direction_correct,
                signal_profitable=signal_profitable,
                error_pct=error_pct,
                lesson=lesson,
                prediction_date=pred_ts,
                verification_period=f"{future_start_ts} to {future_end_ts}",
            )
            results.append(result)

            # Progress logging
            if iteration % 5 == 0 or iteration == 1:
                accuracy_so_far = sum(1 for r in results if r.direction_correct) / len(results) * 100
                print(
                    f"  [{iteration:3d}] {pred_ts} | "
                    f"Signal: {prediction.signal.value:4s} | "
                    f"Predicted: {prediction.predicted_change_pct:+.1f}% | "
                    f"Actual: {actual_change_pct:+.1f}% | "
                    f"Correct: {'Y' if direction_correct else 'N'} | "
                    f"Running accuracy: {accuracy_so_far:.0f}%"
                )

        # Step 3: Compile report
        report = self._compile_report(results, lessons)
        return report

    def _is_signal_profitable(self, signal: Signal, actual_change: float) -> bool:
        """Determine if a signal would have been profitable."""
        if signal == Signal.BUY:
            return actual_change > 0
        elif signal == Signal.SELL:
            return actual_change < 0
        else:  # HOLD
            return abs(actual_change) < 2.0

    def _generate_lesson(
        self,
        prediction: Prediction,
        actual_change: float,
        actual_direction: str,
        regime,
    ) -> str:
        """
        Generate a natural language lesson from the prediction error.
        These lessons accumulate and adjust future agent behavior.
        """
        predicted = prediction.predicted_change_pct
        error = abs(predicted - actual_change)

        if prediction.predicted_direction == actual_direction:
            if error < 1.0:
                return (
                    f"Good prediction in {regime.regime.value} regime. "
                    f"Predicted {predicted:+.1f}%, actual was {actual_change:+.1f}%. "
                    f"Direction and magnitude were accurate."
                )
            else:
                return (
                    f"Direction correct but magnitude off in {regime.regime.value}. "
                    f"Predicted {predicted:+.1f}%, actual was {actual_change:+.1f}%. "
                    f"{'Underestimated' if abs(actual_change) > abs(predicted) else 'Overestimated'} "
                    f"the move by {error:.1f}%."
                )

        if prediction.signal == Signal.BUY and actual_change < -1.0:
            return (
                f"False bullish signal in {regime.regime.value} regime. "
                f"Called BUY expecting +{predicted:.1f}% but market fell {actual_change:.1f}%. "
                f"Consider: Was volume confirming? Was RSI already overbought?"
            )
        elif prediction.signal == Signal.SELL and actual_change > 1.0:
            return (
                f"Missed rally in {regime.regime.value} regime. "
                f"Called SELL expecting {predicted:.1f}% but market rose +{actual_change:.1f}%. "
                f"Was too cautious. Check if momentum was building."
            )
        elif prediction.signal == Signal.HOLD and abs(actual_change) > 3.0:
            if actual_change > 0:
                return (
                    f"Missed rally while holding in {regime.regime.value}. "
                    f"Market moved +{actual_change:.1f}% while on sidelines. "
                    f"Look for breakout signals earlier."
                )
            else:
                return (
                    f"Should have sold in {regime.regime.value}. "
                    f"Market dropped {actual_change:.1f}% while holding. "
                    f"Cut losses earlier when bearish signals appear."
                )
        else:
            return (
                f"Direction wrong in {regime.regime.value}. "
                f"Predicted {prediction.predicted_direction} ({predicted:+.1f}%), "
                f"actual was {actual_direction} ({actual_change:+.1f}%). "
                f"Error margin: {error:.1f}%."
            )

    def _compile_report(
        self, results: list[BacktestResult], lessons: list[str]
    ) -> BacktestReport:
        """Compile individual results into an aggregate backtest report."""
        if not results:
            return BacktestReport(
                ticker=self.ticker,
                interval=self.interval,
                total_predictions=0,
                direction_accuracy=0.0,
                signal_accuracy=0.0,
                signal_profitability=0.0,
                avg_error_pct=0.0,
                total_return_pct=0.0,
                sharpe_ratio=0.0,
                max_drawdown_pct=0.0,
                results=[],
                lessons_learned=[],
            )

        total = len(results)
        direction_correct = sum(1 for r in results if r.direction_correct)
        signal_profitable = sum(1 for r in results if r.signal_profitable)
        errors = [r.error_pct for r in results]

        # Calculate cumulative return
        returns = []
        for r in results:
            if r.prediction.signal == Signal.BUY:
                returns.append(r.actual_change_pct / 100)
            elif r.prediction.signal == Signal.SELL:
                returns.append(-r.actual_change_pct / 100)
            else:
                returns.append(0.0)

        returns_array = np.array(returns)
        cumulative_return = float(np.prod(1 + returns_array) - 1) * 100

        # Sharpe ratio (annualized using interval-aware periods per year)
        if len(returns_array) > 1 and returns_array.std() > 0:
            periods_per_year = Config.get_periods_per_year(self.interval) / self.horizon
            sharpe = float(
                returns_array.mean() / returns_array.std() * np.sqrt(periods_per_year)
            )
        else:
            sharpe = 0.0

        # Max drawdown
        cumulative = np.cumprod(1 + returns_array)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = (cumulative - running_max) / running_max
        max_drawdown = float(abs(drawdowns.min())) * 100 if len(drawdowns) > 0 else 0.0

        # Performance by regime
        regime_perf = {}
        for r in results:
            regime = r.prediction.regime_used
            if regime not in regime_perf:
                regime_perf[regime] = {"total": 0, "correct": 0, "profitable": 0}
            regime_perf[regime]["total"] += 1
            if r.direction_correct:
                regime_perf[regime]["correct"] += 1
            if r.signal_profitable:
                regime_perf[regime]["profitable"] += 1

        for regime in regime_perf:
            t = regime_perf[regime]["total"]
            regime_perf[regime]["direction_accuracy"] = round(
                regime_perf[regime]["correct"] / t * 100, 1
            )
            regime_perf[regime]["signal_accuracy"] = round(
                regime_perf[regime]["profitable"] / t * 100, 1
            )

        # Select most impactful lessons
        unique_lessons = list(dict.fromkeys(lessons))
        key_lessons = [l for l in unique_lessons if "wrong" in l.lower() or "false" in l.lower() or "missed" in l.lower()]
        if len(key_lessons) < 5:
            key_lessons = unique_lessons[-10:]

        sig_prof = signal_profitable / total

        report = BacktestReport(
            ticker=self.ticker,
            interval=self.interval,
            total_predictions=total,
            direction_accuracy=direction_correct / total,
            signal_accuracy=sig_prof,
            signal_profitability=sig_prof,
            avg_error_pct=float(np.mean(errors)),
            total_return_pct=cumulative_return,
            sharpe_ratio=sharpe,
            max_drawdown_pct=max_drawdown,
            results=results,
            lessons_learned=key_lessons[:Config.MAX_LESSONS_IN_MEMORY],
            regime_performance=regime_perf,
        )

        # Print summary
        period_label = "periods" if Config.is_intraday(self.interval) else "days"
        print(f"\n{'='*60}")
        print("BACKTEST SUMMARY")
        print(f"{'='*60}")
        print(f"  Ticker: {self.ticker} | Interval: {self.interval}")
        print(f"  Total predictions: {total}")
        print(f"  Horizon: {self.horizon} {period_label}")
        print(f"  Direction accuracy: {report.direction_accuracy:.1%}")
        print(f"  Signal profitability: {report.signal_accuracy:.1%}")
        print(f"  Avg prediction error: {report.avg_error_pct:.2f}%")
        print(f"  Cumulative return: {report.total_return_pct:+.2f}%")
        print(f"  Sharpe ratio: {report.sharpe_ratio:.3f}")
        print(f"  Max drawdown: {report.max_drawdown_pct:.2f}%")
        print(f"\n  Performance by Regime:")
        for regime, perf in regime_perf.items():
            print(
                f"    {regime:20s}: {perf['total']:3d} trades | "
                f"Direction: {perf['direction_accuracy']:5.1f}% | "
                f"Profitable: {perf['signal_accuracy']:5.1f}%"
            )
        print(f"{'='*60}\n")

        return report
