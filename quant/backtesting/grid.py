"""G6 deliverable 1 — the BACKTEST GRID (STEERING Step 6).

Compares three lanes across the 10 golden tickers, scored vs the Nifty
benchmark, and writes a comparison table (CSV + markdown):

  * **quant** — a WALK-FORWARD backtest of the rule-based ``quant.recommend``
    lane over each committed golden parquet frame (offline, deterministic).
  * **buy-and-hold** — the stock's own first-anchor-to-last close return over
    the same window.
  * **deep** — ONE optional slow cell (``deep.adapter.analyze``) for a single
    ``--deep-ticker``; it needs the gateway + Ollama live and is ~20 min, so it
    is never run across all tickers and its (non-deterministic) output is kept
    in a clearly separated section so the quant/BH/benchmark table stays
    byte-reproducible.

Design notes:
  * This module is PURE at import (no side effects). All I/O (reading parquet,
    writing outputs, the live deep call) happens inside functions.
  * The quant + buy-and-hold + benchmark lanes are driven ENTIRELY from the
    committed golden parquet frames (offline) because past/future-dated live
    yfinance is unreliable — reproducibility is a G6 gate measure.
  * The ONE contract is imported, never forked:
    ``from backend.app.schemas.recommendation import Action`` (via quant.recommend).
  * The benchmark fixture lives in ``tests/golden/benchmark/NSEI_1d.parquet``
    (a subdirectory the ``tests/golden/*_1d.parquet`` globs do NOT pick up, so
    the ten-file / all-stock golden invariants stay intact — see the
    materialization script's header for the full rationale).

Walk-forward parameters (fixed, documented so reviewers can reason about them):
  * ``WARMUP`` indicator warmup is handled by ``add_all(...).dropna`` before any
    anchor is taken (so no anchor sees NaN indicators).
  * ``STEP_ROWS = 5`` — step WEEKLY (every 5 trading rows).
  * ``HORIZON_ROWS = 5`` — realize each signal's forward return over the next 5
    trading days from the frame's actual Close.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from quant.backtesting.benchmark import score_vs_benchmark
from quant.data.indicators import TechnicalIndicators
from quant.recommend import recommend

# --- fixed, reproducible locations & parameters ------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = _REPO_ROOT / "tests" / "golden"
# Benchmark fixture: a subdirectory invisible to the non-recursive
# ``tests/golden/*_1d.parquet`` globs (keeps the 10-file / all-stock invariants).
BENCHMARK_PARQUET = GOLDEN_DIR / "benchmark" / "NSEI_1d.parquet"
DEFAULT_OUT_DIR = _REPO_ROOT / "docs" / "evaluations"

STEP_ROWS = 5  # weekly step
HORIZON_ROWS = 5  # forward return horizon (trading days)
ROUND_DP = 4  # round all reported floats so two runs are byte-identical

# The 10 golden tickers, resolved, in the committed parquet filenames. Kept as a
# literal (rather than importing the integration helper) so this module has no
# test-package dependency; the names must match the committed parquet stems.
GOLDEN_TICKERS = [
    "RELIANCE.NS",
    "TCS.BO",
    "HDFCBANK.NS",
    "INFY.NS",
    "ICICIBANK.NS",
    "CUMMINSIND.NS",
    "PERSISTENT.NS",
    "MPHASIS.BO",
    "YESBANK.BO",
    "IDEA.BO",
]

_OHLCV = ["Open", "High", "Low", "Close", "Volume"]


# --- result rows -------------------------------------------------------------


@dataclass(frozen=True)
class GridRow:
    """One ticker's deterministic quant / buy-and-hold / benchmark result."""

    ticker: str
    quant_return_pct: float
    quant_rating: int
    quant_signals: int
    buy_and_hold_pct: float
    benchmark_pct: float

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "quant_return_pct": self.quant_return_pct,
            "quant_rating": self.quant_rating,
            "quant_signals": self.quant_signals,
            "buy_and_hold_pct": self.buy_and_hold_pct,
            "benchmark_pct": self.benchmark_pct,
        }


@dataclass
class DeepCell:
    """The one optional (non-deterministic) deep-lane cell."""

    ticker: str
    as_of: str
    action: str | None = None
    confidence: float | None = None
    rationale_snippet: str | None = None
    error: str | None = None


@dataclass
class GridResult:
    rows: list[GridRow] = field(default_factory=list)
    deep: DeepCell | None = None


# --- loading -----------------------------------------------------------------


def load_enriched_golden(ticker: str) -> pd.DataFrame:
    """Load a golden stock frame and return its ENRICHED, warmup-trimmed form.

    Reads ``tests/golden/<ticker>_1d.parquet`` (RAW OHLCV), enriches with the
    shared indicator pipeline and drops the warmup rows (NaN ``SMA_50``/``RSI``)
    so every anchor the walk-forward takes has complete indicators. Offline.
    """
    path = GOLDEN_DIR / f"{ticker}_1d.parquet"
    raw = pd.read_parquet(path, engine="pyarrow")
    enriched = TechnicalIndicators.add_all(raw, interval="1d").dropna(
        subset=["SMA_50", "RSI"]
    )
    return enriched


def load_benchmark_closes() -> pd.Series:
    """Load the committed Nifty benchmark Close series (indexed by date)."""
    df = pd.read_parquet(BENCHMARK_PARQUET, engine="pyarrow")
    return pd.Series(df["Close"].values, index=df.index, dtype=float)


# --- the quant walk-forward --------------------------------------------------


def walk_forward_quant_returns(
    ticker: str, enriched: pd.DataFrame
) -> list[float]:
    """Walk the enriched frame WEEKLY and realize each quant signal's fwd return.

    At each anchor (``STEP_ROWS`` apart, starting at the first enriched row) call
    ``recommend`` on the slice UP TO AND INCLUDING the anchor (no lookahead),
    then realize the forward return over the next ``HORIZON_ROWS`` trading days
    from the frame's actual Close. Map the action to a fractional return:
    BUY -> +fwd, SELL -> -fwd, HOLD -> 0.0. Anchors without ``HORIZON_ROWS``
    future rows are skipped.

    Deterministic: ``recommend`` is rule-based and the frame is the committed
    golden, so the returned list is identical on every run.
    """
    closes = enriched["Close"].to_numpy(dtype=float)
    index = enriched.index
    n = len(enriched)
    returns: list[float] = []

    anchor_idx = 0
    while anchor_idx < n:
        # Need HORIZON_ROWS future rows to realize the forward return.
        if anchor_idx + HORIZON_ROWS >= n:
            break

        anchor_ts = index[anchor_idx]
        anchor_date = (
            anchor_ts.date() if hasattr(anchor_ts, "date") else date.today()
        )
        slice_df = enriched.iloc[: anchor_idx + 1]
        rec = recommend(ticker, anchor_date, df=slice_df)

        fwd = closes[anchor_idx + HORIZON_ROWS] / closes[anchor_idx] - 1.0
        action = rec.action.value
        if action == "BUY":
            returns.append(fwd)
        elif action == "SELL":
            returns.append(-fwd)
        else:  # HOLD
            returns.append(0.0)

        anchor_idx += STEP_ROWS

    return returns


def _benchmark_window_closes(
    bench: pd.Series, enriched: pd.DataFrame
) -> list[float]:
    """Benchmark closes over the frame's [first .. last] date window.

    Sliced by the enriched frame's own date range so the benchmark buy-and-hold
    (computed inside ``score_vs_benchmark``) spans the same window as the quant
    walk-forward. Falls back to positional alignment only if the date slice is
    empty (shouldn't happen for the committed goldens).
    """
    start, end = enriched.index.min(), enriched.index.max()
    window = bench.loc[(bench.index >= start) & (bench.index <= end)]
    if window.empty:
        return [float(c) for c in bench.tolist()]
    return [float(c) for c in window.tolist()]


# --- per-ticker row ----------------------------------------------------------


def compute_row(ticker: str, bench: pd.Series) -> GridRow:
    """Compute one deterministic grid row (quant / BH / benchmark)."""
    enriched = load_enriched_golden(ticker)

    quant_returns = walk_forward_quant_returns(ticker, enriched)
    bench_window = _benchmark_window_closes(bench, enriched)
    scored = score_vs_benchmark(quant_returns, bench_window)

    # Buy-and-hold on the STOCK itself over the same anchor window: first anchor
    # close to last close. First anchor is enriched row 0; last close is the
    # frame's final Close.
    closes = enriched["Close"].to_numpy(dtype=float)
    bh_pct = (closes[-1] / closes[0] - 1.0) * 100.0 if len(closes) >= 2 else 0.0

    return GridRow(
        ticker=ticker,
        quant_return_pct=round(scored["strategy_total_return_pct"], ROUND_DP),
        quant_rating=int(scored["rating"]),
        quant_signals=len(quant_returns),
        buy_and_hold_pct=round(bh_pct, ROUND_DP),
        benchmark_pct=round(scored["benchmark_total_return_pct"], ROUND_DP),
    )


# --- the deep cell (optional, slow, non-deterministic) -----------------------

DEEP_AS_OF = date(2026, 10, 21)  # a recent date within the golden window


def run_deep_cell(deep_ticker: str, as_of: date = DEEP_AS_OF) -> DeepCell:
    """Run the ONE deep-lane cell for ``deep_ticker`` (needs gateway + Ollama).

    Wrapped so one failure never aborts the grid: any exception is recorded as
    the cell's ``error`` string. Imported lazily so the offline grid never pulls
    the deep adapter / TradingAgents import chain.
    """
    cell = DeepCell(ticker=deep_ticker, as_of=as_of.isoformat())
    try:
        from deep.adapter import analyze

        rec = analyze(deep_ticker, as_of)
        cell.action = rec.action.value
        cell.confidence = rec.confidence
        snippet = (rec.rationale or "").strip().replace("\n", " ")
        cell.rationale_snippet = snippet[:200]
    except Exception as exc:  # noqa: BLE001 - record per-ticker, never abort
        cell.error = f"{type(exc).__name__}: {exc}"
    return cell


# --- the grid ----------------------------------------------------------------


def run_grid(
    tickers: list[str] | None = None,
    deep_ticker: str | None = None,
    out_dir: Path | str | None = None,
) -> GridResult:
    """Run the backtest grid and write ``backtest_grid.csv`` + ``.md``.

    Args:
        tickers: tickers to score (defaults to the 10 golden tickers).
        deep_ticker: if given AND it is one of ``tickers``, additionally run the
            ONE slow deep cell for it (needs the gateway + Ollama live).
        out_dir: output directory (defaults to ``docs/evaluations/``; created).

    Returns:
        A :class:`GridResult`. Side effect: writes the CSV + markdown.
    """
    tickers = list(tickers) if tickers else list(GOLDEN_TICKERS)
    out_path = Path(out_dir) if out_dir is not None else DEFAULT_OUT_DIR
    out_path.mkdir(parents=True, exist_ok=True)

    bench = load_benchmark_closes()
    rows = [compute_row(t, bench) for t in tickers]

    deep: DeepCell | None = None
    if deep_ticker:
        if deep_ticker not in tickers:
            deep = DeepCell(
                ticker=deep_ticker,
                as_of=DEEP_AS_OF.isoformat(),
                error=f"deep_ticker {deep_ticker!r} is not in the grid tickers",
            )
        else:
            deep = run_deep_cell(deep_ticker)

    result = GridResult(rows=rows, deep=deep)
    _write_csv(result, out_path / "backtest_grid.csv")
    _write_md(result, out_path / "backtest_grid.md")
    return result


# --- output writers ----------------------------------------------------------

_CSV_HEADER = [
    "ticker",
    "quant_return_pct",
    "quant_rating",
    "quant_signals",
    "buy_and_hold_pct",
    "benchmark_pct",
]


def _write_csv(result: GridResult, path: Path) -> None:
    """Write the DETERMINISTIC quant/BH/benchmark table as CSV.

    The deep cell is intentionally EXCLUDED from the CSV so the file is
    byte-identical on two offline runs (the deep lane is non-deterministic LLM
    output). ``\\n`` line terminator (not the platform default) so the bytes are
    identical across OSes.
    """
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(_CSV_HEADER)
        for row in result.rows:
            d = row.as_dict()
            writer.writerow(
                [
                    d["ticker"],
                    f"{d['quant_return_pct']:.4f}",
                    d["quant_rating"],
                    d["quant_signals"],
                    f"{d['buy_and_hold_pct']:.4f}",
                    f"{d['benchmark_pct']:.4f}",
                ]
            )


def _format_table(result: GridResult) -> str:
    """Render the deterministic grid as a markdown table string."""
    lines = [
        "| Ticker | Quant return % | Quant rating (vs Nifty) | Quant signals | "
        "Buy & hold % | Benchmark % |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in result.rows:
        lines.append(
            f"| {row.ticker} | {row.quant_return_pct:.4f} | {row.quant_rating} | "
            f"{row.quant_signals} | {row.buy_and_hold_pct:.4f} | "
            f"{row.benchmark_pct:.4f} |"
        )
    return "\n".join(lines)


def _write_md(result: GridResult, path: Path) -> None:
    """Write the human-readable markdown summary.

    The deterministic table (quant/BH/benchmark) is byte-reproducible. The deep
    cell is placed in a CLEARLY SEPARATED section so its non-deterministic LLM
    output never affects the reproducible table.
    """
    parts = [
        "# Backtest grid — quant vs buy-and-hold vs deep (G6 deliverable 1)",
        "",
        "Three lanes across the 10 golden tickers, scored against the Nifty 50 "
        "(`^NSEI`) benchmark. The quant lane is a WEEKLY walk-forward "
        f"(step {STEP_ROWS} trading days, forward horizon {HORIZON_ROWS} trading "
        "days) of the rule-based `quant.recommend` lane over the committed "
        "golden parquet frames — fully offline and deterministic. Buy-and-hold "
        "is the stock's own first-anchor-to-last close return over the same "
        "window. The benchmark column is Nifty buy-and-hold over the same date "
        "window. All floats are rounded to 4 dp so two runs are byte-identical.",
        "",
        "## Deterministic grid (reproducible)",
        "",
        _format_table(result),
        "",
    ]

    parts += [
        "## Deep lane (1 ticker only on this hardware)",
        "",
        "The deep lane (TradingAgents multi-agent graph via the local gateway + "
        "Ollama) is ~20 min per ticker on the 4 GB VRAM / `llama3.2` stack, so "
        "the grid runs it for at most ONE `--deep-ticker` and never across all "
        "ten. Its output is non-deterministic LLM text and is reported here "
        "separately from the reproducible table above. The full multi-ticker "
        "deep grid is deferred to faster hardware (known caveat).",
        "",
    ]
    if result.deep is None:
        parts.append(
            "_No deep cell was run (offline grid). Pass `--deep-ticker <TICKER>` "
            "with the gateway + Ollama up to populate this section._"
        )
    else:
        d = result.deep
        if d.error:
            parts.append(f"- **{d.ticker}** (as_of {d.as_of}): error — {d.error}")
        else:
            conf = f"{d.confidence:.2f}" if d.confidence is not None else "n/a"
            parts.append(
                f"- **{d.ticker}** (as_of {d.as_of}): action **{d.action}**, "
                f"confidence {conf}"
            )
            if d.rationale_snippet:
                parts.append(f"  - rationale: {d.rationale_snippet}")
    parts.append("")

    path.write_text("\n".join(parts), encoding="utf-8")


# --- CLI ---------------------------------------------------------------------


def _print_table(result: GridResult) -> None:
    """Print a concise fixed-width summary table to stdout."""
    header = (
        f"{'Ticker':<14} {'Quant%':>10} {'Rating':>7} {'Signals':>8} "
        f"{'BuyHold%':>10} {'Bench%':>10}"
    )
    print(header)
    print("-" * len(header))
    for row in result.rows:
        print(
            f"{row.ticker:<14} {row.quant_return_pct:>10.4f} "
            f"{row.quant_rating:>7} {row.quant_signals:>8} "
            f"{row.buy_and_hold_pct:>10.4f} {row.benchmark_pct:>10.4f}"
        )
    if result.deep is not None:
        d = result.deep
        print()
        if d.error:
            print(f"deep[{d.ticker}] as_of {d.as_of}: ERROR {d.error}")
        else:
            conf = f"{d.confidence:.2f}" if d.confidence is not None else "n/a"
            print(f"deep[{d.ticker}] as_of {d.as_of}: {d.action} (conf {conf})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m quant.backtesting.grid",
        description=(
            "G6 backtest grid: quant + buy-and-hold + benchmark over the 10 "
            "golden tickers (offline, deterministic). Optionally run ONE slow "
            "deep cell with --deep-ticker (needs the gateway + Ollama live)."
        ),
    )
    parser.add_argument(
        "--deep-ticker",
        default=None,
        help=(
            "Run the ONE slow deep-lane cell for this golden ticker "
            "(e.g. RELIANCE.NS). Needs the gateway + Ollama; ~20 min. Omit to "
            "run only the fast offline quant+BH+benchmark grid."
        ),
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help="Output directory for backtest_grid.csv/.md (default: docs/evaluations/).",
    )
    args = parser.parse_args(argv)

    result = run_grid(deep_ticker=args.deep_ticker, out_dir=args.out_dir)
    _print_table(result)
    out = Path(args.out_dir)
    print()
    print(f"wrote {out / 'backtest_grid.csv'}")
    print(f"wrote {out / 'backtest_grid.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
