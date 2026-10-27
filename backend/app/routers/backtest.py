"""Read-only Backtest endpoint (Step 5 / G5 "Backtest Lab" page).

``GET /api/backtest/{ticker}`` runs the EXISTING deterministic rule-based
``BacktestEngine`` over a RECENT daily window and returns ``report.to_dict()``
(direction_accuracy, signal_accuracy, total_return_pct, sharpe_ratio,
max_drawdown_pct, total_predictions, results[], ...). It is deliberately:
  * read-only — it computes a backtest and returns it; it never writes;
  * additive — a new router file; it does not modify any existing router;
  * rule-based only — ``use_llm`` is left off, so the deep lane / gateway is
    NEVER touched from this web request (STEERING rule 6).

The engine does blocking yfinance/DB work, so (like ``quant_signal``) it is
offloaded to a worker thread via ``anyio.to_thread.run_sync`` to keep the event
loop responsive. A RECENT window (``lookback_days`` ending today) is used
because past-dated 2024 windows return empty through the data service.
"""

from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from quant.backtesting.engine import BacktestEngine

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

# Default trailing daily bars to backtest over. 200 calendar days comfortably
# clears the engine's minimum-bars guard for the default rule-based horizon.
_DEFAULT_LOOKBACK_DAYS = 200
_MIN_LOOKBACK_DAYS = 90
_MAX_LOOKBACK_DAYS = 365


class BacktestResponse(BaseModel):
    """The engine's ``to_dict()`` report, passed through verbatim.

    Declared as an open model (``extra="allow"``) so the response is exactly the
    engine's dict shape — the single source of truth for backtest fields — with
    no re-listing of keys that could drift from the engine.
    """

    model_config = {"extra": "allow"}

    ticker: str
    interval: str
    total_predictions: int
    direction_accuracy: float
    signal_accuracy: float
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float


def _to_jsonable(value: Any) -> Any:
    """Coerce numpy scalars/containers in the engine's dict to native Python.

    The engine computes with numpy, so ``to_dict()`` can carry ``numpy.bool_``
    (``direction_correct``/``signal_profitable``) and ``numpy.floating``/
    ``numpy.integer`` scalars that pydantic/JSON cannot serialize. We walk the
    dict ONCE here (read-only on our own response copy — the engine is never
    modified) and convert those to ``bool``/``float``/``int``. Non-finite floats
    (NaN/inf) become ``None`` so the JSON stays valid.
    """
    # numpy is a hard dependency of the engine; import locally to keep the
    # router importable even if that ever changes.
    import math

    import numpy as np

    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _run_backtest(ticker: str, lookback_days: int) -> dict:
    """Synchronous worker: run the deterministic rule-based backtest.

    Runs in a worker thread (see the route) because the engine does blocking
    network/DB I/O (yfinance + the price-bar table). Raises ``ValueError`` when
    there isn't enough data (surfaced as a 404 by the route). The engine's dict
    is sanitized to JSON-serializable native types before it leaves the thread.
    """
    engine = BacktestEngine(ticker=ticker, interval="1d")  # rule-based (use_llm off)
    report = engine.run(lookback_days=lookback_days)
    return _to_jsonable(report.to_dict())


@router.get(
    "/{ticker}",
    response_model=BacktestResponse,
    summary="Deterministic rule-based backtest over a recent window (read-only)",
)
async def get_backtest(
    ticker: str,
    lookback_days: int = Query(
        _DEFAULT_LOOKBACK_DAYS, ge=_MIN_LOOKBACK_DAYS, le=_MAX_LOOKBACK_DAYS
    ),
) -> BacktestResponse:
    """Run a rule-based backtest for ``ticker`` over a recent window (read-only).

    404 when there isn't enough data (e.g. Yahoo rate-limited or a thin ticker);
    502 on any other engine failure. The engine is synchronous + blocking, so it
    runs in a worker thread to keep the event loop responsive.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=422, detail="ticker is required")
    try:
        result = await anyio.to_thread.run_sync(_run_backtest, ticker, lookback_days)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface a clean message to the UI
        raise HTTPException(
            status_code=502, detail=f"Failed to run backtest: {exc}"
        ) from exc
    return BacktestResponse(**result)
