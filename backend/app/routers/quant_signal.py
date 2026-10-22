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


class SignalResponse(BaseModel):
    recommendation: Recommendation
    regime: RegimeInfo
    chart: list[ChartPoint]


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

    return SignalResponse(
        recommendation=rec,
        regime=RegimeInfo(
            label=regime.regime.value,
            confidence=float(regime.confidence),
            reasoning=regime.reasoning,
        ),
        chart=chart,
    )


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
