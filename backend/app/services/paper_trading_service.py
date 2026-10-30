"""Paper-trading service (Step 6 / G6 deliverable 4).

Turns stored recommendations into a PAPER book per engine source ("quant" /
"deep"), marks open positions to market, and summarizes each book's return
against a buy-and-hold benchmark. "No order execution, paper positions only"
(STEERING rule 9): nothing here places a real order.

Design split (so the suite can exercise the logic offline):
  * The PURE functions below (``paper_return_pct``, ``decide_transition``,
    ``summarize_book``) take plain inputs and do no I/O — they hold the P&L and
    open/close/mark decision rules and are deterministic.
  * The DB/price-touching orchestration (``mark_to_market_book``) composes the
    pure rules with the sync repository, the point-in-time price path, and the
    recommendation repository. It is covered by the integration test.

Point-in-time prices: a position's ENTRY price is the close the opening
recommendation saw at its ``as_of`` (same ``DataPipeline`` path the engine
used); each MARK is the latest close. This keeps the paper book consistent with
"models explain, code computes" — every number is a real close from our data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

from backend.app.database import create_sync_session
from backend.app.models.paper_position import PAPER_NOTIONAL
from quant.backtesting.benchmark import score_vs_benchmark

logger = logging.getLogger(__name__)

# The engine sources that get a paper book (the dual-book, option B).
PAPER_SOURCES: tuple[str, ...] = ("quant", "deep")

Transition = Literal["open", "mark", "close_sell", "close_horizon", "none"]


# --- pure P&L + decision helpers (no I/O, offline-testable) ------------------


def paper_return_pct(entry_price: float, mark_price: float) -> float:
    """Return the fractional return of a LONG paper position.

    ``(mark / entry) - 1``. A BUY-only paper book is modelled (we open on BUY,
    close on SELL); there is no shorting, so the return is always long-side.
    Raises ``ValueError`` on a non-positive entry price (an un-markable entry).
    """
    if entry_price <= 0:
        raise ValueError("entry_price must be positive to compute a return")
    return (mark_price / entry_price) - 1.0


def paper_quantity(entry_price: float, notional: float = PAPER_NOTIONAL) -> float:
    """Fixed-notional quantity: ``notional / entry_price`` (paper sizing)."""
    if entry_price <= 0:
        raise ValueError("entry_price must be positive to size a position")
    return notional / entry_price


def decide_transition(
    *,
    has_open_position: bool,
    action: str,
    days_held: Optional[int],
    horizon_days: int,
) -> Transition:
    """Decide what to do for one ticker/source given the latest recommendation.

    Pure rules (no I/O):
      * No open position + BUY  -> "open".
      * No open position + HOLD/SELL -> "none" (nothing to do).
      * Open position + SELL -> "close_sell" (realize the P&L).
      * Open position + horizon elapsed (``days_held >= horizon_days``) ->
        "close_horizon" (realize), regardless of the action.
      * Open position otherwise (BUY/HOLD, still within horizon) -> "mark".

    ``days_held`` is the calendar days between the open date and the mark date;
    ``None`` is treated as "not yet past horizon" (a same-day first mark).
    """
    act = (action or "").upper()

    if not has_open_position:
        return "open" if act == "BUY" else "none"

    # There is an open position.
    if act == "SELL":
        return "close_sell"
    if days_held is not None and horizon_days > 0 and days_held >= horizon_days:
        return "close_horizon"
    return "mark"


@dataclass(frozen=True)
class BookSummary:
    """A per-source paper book's deterministic summary vs the benchmark."""

    source: str
    closed_positions: int
    open_positions: int
    # Compounded return across CLOSED positions (realized), as a percent.
    realized_return_pct: float
    benchmark_return_pct: float
    excess_return_pct: float
    rating: int


def summarize_book(
    *,
    source: str,
    realized_returns: list[float],
    open_count: int,
    benchmark_closes: list[float],
) -> BookSummary:
    """Summarize one paper book vs buy-and-hold on the benchmark (pure).

    ``realized_returns`` are the FRACTIONAL returns of CLOSED positions. They
    are scored with the shared, deterministic :func:`score_vs_benchmark` (the
    same scorer the backtest grid uses), so the paper-trade verdict is directly
    comparable to the backtest's. Open positions are counted but not scored
    (their P&L is unrealized).
    """
    scored = score_vs_benchmark(realized_returns, benchmark_closes)
    return BookSummary(
        source=source,
        closed_positions=len(realized_returns),
        open_positions=open_count,
        realized_return_pct=scored["strategy_total_return_pct"],
        benchmark_return_pct=scored["benchmark_total_return_pct"],
        excess_return_pct=scored["excess_return_pct"],
        rating=scored["rating"],
    )


# --- price access (point-in-time, deterministic) -----------------------------


def close_price_as_of(ticker: str, as_of: date) -> Optional[float]:
    """The last close at/through ``as_of`` via the ONE quant data path.

    Uses ``DataPipeline.get_enriched_data`` with an explicit ``end_date`` so the
    price is point-in-time consistent with what an engine saw. Returns ``None``
    when no bars resolve (e.g. the past-dated-empty yfinance caveat) so callers
    can skip rather than fabricate a price.
    """
    from quant.data.pipeline import DataPipeline

    pipeline = DataPipeline(ticker=ticker, interval="1d")
    df = pipeline.get_enriched_data(
        end_date=as_of.isoformat(),
        interval="1d",
        lookback_days=200,
        use_cache=False,
    )
    if df is None or getattr(df, "empty", True) or "Close" not in df.columns:
        return None
    return float(df["Close"].iloc[-1])


# --- mark-to-market orchestration (DB + prices; covered by integration test) -


def mark_to_market_ticker(
    *,
    ticker: str,
    source: str,
    as_of: date,
    repo=None,
    rec_repo=None,
) -> Transition:
    """Advance ONE ticker's paper book for ``source`` to ``as_of``.

    Reads the latest stored recommendation of ``source`` for ``ticker``, decides
    the transition (open / mark / close), and writes it through the sync paper
    repository using point-in-time close prices. Returns the transition taken
    (``"none"`` when there is nothing to do or a price/recommendation is
    missing). Per-ticker failures are the caller's concern (the task isolates
    them); this function raises on a genuine error.

    Args:
        ticker: symbol to advance.
        source: "quant" or "deep" — which book + which recommendation source.
        as_of: the mark date (today for a nightly run).
        repo: injected ``SyncPaperPositionRepository`` (one is created if None).
        rec_repo: injected ``SyncRecommendationRepository`` (created if None).
    """
    from backend.app.repositories.paper_position_repository import (
        SyncPaperPositionRepository,
    )
    from backend.app.repositories.recommendation_repository import (
        SyncRecommendationRepository,
    )

    repo = repo or SyncPaperPositionRepository()
    rec_repo = rec_repo or SyncRecommendationRepository()

    latest = rec_repo.latest_by_source(ticker)
    rec = latest.get(source)
    if rec is None:
        return "none"

    open_pos = repo.get_open(ticker, source)
    days_held = (as_of - open_pos.opened_as_of).days if open_pos else None

    transition = decide_transition(
        has_open_position=open_pos is not None,
        action=rec.action,
        days_held=days_held,
        horizon_days=rec.horizon_days,
    )

    if transition == "none":
        return "none"

    if transition == "open":
        entry = close_price_as_of(ticker, rec.as_of)
        if entry is None or entry <= 0:
            return "none"
        repo.open_position(
            ticker=ticker,
            source=source,
            opened_as_of=rec.as_of,
            entry_price=entry,
        )
        return "open"

    # From here a position is open; we need a current mark price.
    mark = close_price_as_of(ticker, as_of)
    if mark is None or mark <= 0:
        return "none"

    if transition == "mark":
        repo.mark_open(
            open_pos.id,
            mark_date=as_of,
            mark_price=mark,
            unrealized_pnl_pct=paper_return_pct(open_pos.entry_price, mark),
        )
        return "mark"

    # close_sell / close_horizon
    reason = "sell_signal" if transition == "close_sell" else "horizon"
    repo.close_position(
        open_pos.id,
        closed_as_of=as_of,
        exit_price=mark,
        realized_pnl_pct=paper_return_pct(open_pos.entry_price, mark),
        close_reason=reason,
    )
    return transition


def mark_to_market_book(
    *,
    source: str,
    tickers: list[str],
    as_of: date,
) -> dict:
    """Advance a whole paper book (one ``source``) across ``tickers``.

    Per-ticker failures are isolated (logged, counted) so the run completes.
    Returns a summary dict of the transitions taken.
    """
    summary = {
        "source": source,
        "opened": 0,
        "marked": 0,
        "closed": 0,
        "skipped": 0,
        "errors": 0,
    }
    repo = None
    rec_repo = None
    try:
        from backend.app.repositories.paper_position_repository import (
            SyncPaperPositionRepository,
        )
        from backend.app.repositories.recommendation_repository import (
            SyncRecommendationRepository,
        )

        repo = SyncPaperPositionRepository()
        rec_repo = SyncRecommendationRepository()

        for ticker in tickers:
            try:
                t = mark_to_market_ticker(
                    ticker=ticker,
                    source=source,
                    as_of=as_of,
                    repo=repo,
                    rec_repo=rec_repo,
                )
                if t == "open":
                    summary["opened"] += 1
                elif t == "mark":
                    summary["marked"] += 1
                elif t in ("close_sell", "close_horizon"):
                    summary["closed"] += 1
                else:
                    summary["skipped"] += 1
            except Exception:
                summary["errors"] += 1
                logger.exception(
                    "paper mark-to-market failed for %s (%s)", ticker, source
                )
    finally:
        # Repos opened their own sessions per call; nothing to close here.
        pass

    return summary
