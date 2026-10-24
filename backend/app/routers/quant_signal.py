"""Read-only quant signal endpoint (G3 frontend support).

This router runs the EXISTING rule-based quant engine live and returns a
Recommendation plus the chart series behind it. It is deliberately:
  * read-only — it computes, it never writes;
  * database-free — no table, no session, no auth (nothing user-specific);
  * additive — a new router file; it does not modify any existing router.

It exists so the frontend has real data to display before the Step-3 database
pieces (the persisted `recommendations` table + nightly Celery job) are built.
When those land, this endpoint remains useful as a live/backup path.
"""

from __future__ import annotations

from datetime import date

import anyio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.schemas.recommendation import Recommendation
from quant.agents.regime_agent import RegimeAgent
from quant.backtesting.benchmark import (
    DEFAULT_BENCHMARK,
    fetch_benchmark_closes,
    score_vs_benchmark,
)
from quant.data.indicators import TechnicalIndicators
from quant.data.pipeline import DataPipeline
from quant.recommend import recommend

router = APIRouter(prefix="/api/quant", tags=["quant"])


class ChartPoint(BaseModel):
    date: str
    close: float
    sma20: float | None = None
    sma50: float | None = None


class RegimeInfo(BaseModel):
    label: str
    confidence: float
    reasoning: str


class BenchmarkInfo(BaseModel):
    """Best-effort benchmark context for the UI (may be absent)."""
    index: str
    benchmark_return_pct: float
    excess_return_pct: float
    rating: int


class SignalResponse(BaseModel):
    recommendation: Recommendation
    regime: RegimeInfo
    chart: list[ChartPoint]
    # Best-effort: None when the benchmark fetch/score fails. The benchmark
    # NEVER fails the whole request (it is decoration, not the signal).
    benchmark: BenchmarkInfo | None = None


# How many trailing daily bars to send to the chart (keeps the payload small).
_CHART_TAIL_N = 120


def _compute_signal(ticker: str) -> SignalResponse:
    """Synchronous worker: fetch daily bars once, reuse for signal + chart.

    Runs in a worker thread (see the route) because yfinance does blocking I/O.
    """
    as_of = date.today()

    # One enriched daily fetch, reused for the signal, regime, and chart so the
    # three views agree (and we don't hit yfinance three times).
    pipeline = DataPipeline(ticker=ticker, interval="1d")
    df = pipeline.get_enriched_data(
        end_date=as_of.isoformat(),
        interval="1d",
        lookback_days=200,
        use_cache=False,
    )
    if df is None or getattr(df, "empty", True):
        raise ValueError(
            f"No data returned for {ticker}. Yahoo may be rate-limiting, or the "
            f"ticker may be unavailable. Try a .NS ticker or retry shortly."
        )

    rec = recommend(ticker, as_of=as_of, df=df)
    regime = RegimeAgent().classify(df)

    tail = df.tail(_CHART_TAIL_N)
    chart: list[ChartPoint] = []
    for idx, row in tail.iterrows():
        d = idx.date().isoformat() if hasattr(idx, "date") else str(idx)
        sma20 = row.get("SMA_20")
        sma50 = row.get("SMA_50")
        chart.append(
            ChartPoint(
                date=d,
                close=round(float(row["Close"]), 2),
                sma20=None if sma20 is None or sma20 != sma20 else round(float(sma20), 2),
                sma50=None if sma50 is None or sma50 != sma50 else round(float(sma50), 2),
            )
        )

    benchmark = _compute_benchmark(df, as_of)

    return SignalResponse(
        recommendation=rec,
        regime=RegimeInfo(
            label=regime.regime.value,
            confidence=float(regime.confidence),
            reasoning=regime.reasoning,
        ),
        chart=chart,
        benchmark=benchmark,
    )


def _compute_benchmark(df, as_of: date) -> BenchmarkInfo | None:
    """Best-effort "stock vs index" context for ``df`` vs the default index.

    Compares the TICKER's own buy-and-hold against the index's buy-and-hold,
    then scores + rates it with the pure :func:`score_vs_benchmark`. This is
    deliberately NOT the signal/strategy's realised return (that strategy-vs-
    benchmark score belongs to the later Backtest Lab); the UI card is labelled
    "Stock vs Index" to match. The two windows are approximately — not exactly —
    aligned: the ticker series is the enriched ``df`` (post-indicator-warmup)
    while the index series is ``lookback_days`` of raw closes, so treat the
    figure as recent-window context, not a precise same-bar comparison.

    Returns ``None`` on ANY failure (e.g. the benchmark fetch is rate-limited)
    so the benchmark never breaks the signal request. Runs inside the existing
    worker thread (the caller), so the extra fetch does not touch the event loop.
    """
    try:
        closes = df["Close"].tolist()
        if len(closes) < 2 or not closes[0]:
            return None
        # The ticker's realized buy-and-hold over the window, as one "trade".
        ticker_return = closes[-1] / closes[0] - 1.0

        benchmark_closes = fetch_benchmark_closes(
            index_symbol=DEFAULT_BENCHMARK, as_of=as_of, lookback_days=200
        )
        if len(benchmark_closes) < 2:
            return None

        scored = score_vs_benchmark([ticker_return], benchmark_closes)
        return BenchmarkInfo(
            index=DEFAULT_BENCHMARK,
            benchmark_return_pct=scored["benchmark_total_return_pct"],
            excess_return_pct=scored["excess_return_pct"],
            rating=scored["rating"],
        )
    except Exception:  # noqa: BLE001 - benchmark is decoration; never fail here
        return None


@router.get(
    "/signal/{ticker}",
    response_model=SignalResponse,
    summary="Live rule-based quant signal + regime + chart for a ticker",
)
async def get_quant_signal(ticker: str) -> SignalResponse:
    """Compute a live quant Recommendation for ``ticker`` (read-only).

    The quant engine is synchronous and does blocking network I/O (yfinance),
    so it runs in a worker thread to keep the event loop responsive.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=422, detail="ticker is required")
    try:
        return await anyio.to_thread.run_sync(_compute_signal, ticker)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface a clean message to the UI
        raise HTTPException(
            status_code=502, detail=f"Failed to compute signal: {exc}"
        ) from exc
