"""Benchmark scoring for the quant backtest (STEERING Step 3, Gate G3).

Scores a strategy's per-trade returns against a buy-and-hold on a market
benchmark (default Nifty 50) and assigns a simple 1..5 rating, the same spirit
as the way TradingAgents scores by rating.

Design split (intentional):
  * :func:`score_vs_benchmark` is PURE math over its inputs — NO network, NO
    I/O — so it is trivially unit-testable offline and deterministic (same
    inputs -> same dict every time).
  * :func:`fetch_benchmark_closes` is the only I/O piece; it reuses the ONE
    data path (:class:`quant.data.pipeline.DataPipeline`) so the benchmark bars
    come from the same fetch/cache/enrich pipeline every engine uses.

Keeping the two apart means the scoring half never touches yfinance/DB and the
offline suite exercises it directly.
"""

from __future__ import annotations

from datetime import date

# Default benchmark: Nifty 50 (Yahoo symbol). The quant data path resolves and
# fetches it like any other ticker.
DEFAULT_BENCHMARK = "^NSEI"


def _compound_return_pct(returns: list[float]) -> float:
    """Compound a list of per-trade fractional returns into a total percent.

    ``[0.10, -0.05]`` -> ``(1.10 * 0.95 - 1) * 100`` = ``4.5``. An empty list
    compounds to ``0.0`` (no trades, no return).
    """
    product = 1.0
    for r in returns:
        product *= 1.0 + r
    return (product - 1.0) * 100.0


def _rating_from_excess(excess_return_pct: float) -> int:
    """Map excess return (strategy minus benchmark, in percent) to a 1..5 rating.

    Deterministic step thresholds (percent of excess return):

        excess >= 10.0        -> 5  (strongly beats the benchmark)
        2.0  <= excess < 10.0 -> 4  (beats the benchmark)
        -2.0 <= excess <  2.0 -> 3  (roughly in line with the benchmark)
        -10.0 <= excess < -2.0 -> 2  (lags the benchmark)
        excess < -10.0        -> 1  (strongly lags the benchmark)

    The band edges are inclusive on the lower bound (``>=``) so the mapping is
    total and unambiguous across the whole real line.
    """
    if excess_return_pct >= 10.0:
        return 5
    if excess_return_pct >= 2.0:
        return 4
    if excess_return_pct >= -2.0:
        return 3
    if excess_return_pct >= -10.0:
        return 2
    return 1


def score_vs_benchmark(
    strategy_returns: list[float],
    benchmark_closes: list[float],
) -> dict:
    """Score strategy per-trade returns against buy-and-hold on the benchmark.

    Args:
        strategy_returns: The strategy's per-trade returns as FRACTIONS (e.g.
            ``0.02`` for +2%). Compounded into a total percent.
        benchmark_closes: The benchmark's close-price series over the same
            window. Buy-and-hold return is ``(last / first - 1) * 100``. Needs
            at least two closes; fewer means no measurable benchmark move and
            the benchmark return is ``0.0``.

    Returns:
        A dict with:
          * ``strategy_total_return_pct`` — compounded strategy return (percent)
          * ``benchmark_total_return_pct`` — buy-and-hold benchmark return (pct)
          * ``excess_return_pct`` — strategy minus benchmark (percentage points)
          * ``rating`` — integer 1..5 from :func:`_rating_from_excess`

    Pure and deterministic: no network, no I/O, no global state. All percents
    are rounded to 4 decimals so two runs over the same inputs are byte-equal.
    """
    strategy_total = _compound_return_pct(strategy_returns)

    if len(benchmark_closes) >= 2 and benchmark_closes[0]:
        benchmark_total = (benchmark_closes[-1] / benchmark_closes[0] - 1.0) * 100.0
    else:
        benchmark_total = 0.0

    # Round first, then rate off the SAME rounded excess that is reported, so
    # the rating always agrees with the displayed number at the band edges (no
    # float-dust disagreement like -1.9999999 rating as if it were < -2.0).
    excess = round(strategy_total - benchmark_total, 4)

    return {
        "strategy_total_return_pct": round(strategy_total, 4),
        "benchmark_total_return_pct": round(benchmark_total, 4),
        "excess_return_pct": excess,
        "rating": _rating_from_excess(excess),
    }


def fetch_benchmark_closes(
    index_symbol: str = DEFAULT_BENCHMARK,
    as_of: date | None = None,
    lookback_days: int = 200,
) -> list[float]:
    """Fetch the benchmark's daily closes via the ONE quant data pipeline.

    This is the only I/O in this module. It reuses
    :meth:`quant.data.pipeline.DataPipeline.get_enriched_data` so the benchmark
    bars flow through the same fetch/cache/enrich path as every other ticker.

    Args:
        index_symbol: Benchmark symbol (defaults to Nifty 50, ``^NSEI``).
        as_of: Anchor date; defaults to today when ``None``.
        lookback_days: Days of history to pull.

    Returns:
        The benchmark's ``Close`` series as a list of floats (may be empty if
        the fetch returns nothing). Kept separate from :func:`score_vs_benchmark`
        so scoring stays pure/offline-testable.

    Note on index symbols: a market index (e.g. ``^NSEI``, ``^BSESN``) is NOT a
    tradeable stock ticker. The DB-backed ``MarketDataService`` resolves bare
    symbols by appending ``.BO`` (a product decision for Indian STOCKS — see the
    G2 "A3" note), which would corrupt ``^NSEI`` into ``^NSEI.BO`` and 404. So
    for a ``^``-prefixed index we fetch the daily closes directly from yfinance
    (indices aren't stored in ``price_bars`` and need no suffix); non-index
    symbols still flow through the one DataPipeline/MarketDataService path.
    """
    end = (as_of or date.today()).isoformat()

    if index_symbol.startswith("^"):
        import yfinance as yf

        start = None
        try:
            from datetime import timedelta

            anchor = date.fromisoformat(end)
            start = (anchor - timedelta(days=lookback_days)).isoformat()
        except ValueError:
            start = None

        hist = yf.Ticker(index_symbol).history(
            start=start, end=end, interval="1d"
        )
        if hist is None or hist.empty or "Close" not in hist.columns:
            return []
        return [float(c) for c in hist["Close"].tolist()]

    from quant.data.pipeline import DataPipeline

    pipeline = DataPipeline(ticker=index_symbol, interval="1d")
    df = pipeline.get_enriched_data(
        end_date=end,
        interval="1d",
        lookback_days=lookback_days,
        use_cache=False,
    )
    if df is None or getattr(df, "empty", True) or "Close" not in df.columns:
        return []
    return [float(c) for c in df["Close"].tolist()]
