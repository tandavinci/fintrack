# Golden set

10 BSE tickers with saved price data for repeatable tests (STEERING Step 0.5, rule 4). These back the reproducibility and data-parity gates (G2, G3) and the backtest grid (G6).

## Tickers (CONFIRMED — user decision, Option A)

Mix per steering: large caps, mid caps, 2 volatile. The set spans BOTH primary
markets — `.NS` (NSE) and `.BO` (BSE) — which the steering file names as
co-equal, not a BSE-only mandate.

Large caps:
1. RELIANCE.NS
2. TCS.BO
3. HDFCBANK.NS
4. INFY.NS
5. ICICIBANK.NS

Mid caps:
6. CUMMINSIND.NS
7. PERSISTENT.NS
8. MPHASIS.BO

Volatile (2):
9. YESBANK.BO
10. IDEA.BO  (Vodafone Idea)

### Why 6 symbols are `.NS` and 4 are `.BO` (decision rationale)

During Step-2 cross-FEAT integration (real data, no mocks) Yahoo returned NO
data for six `.BO` symbols — RELIANCE.BO, HDFCBANK.BO, INFY.BO, ICICIBANK.BO,
CUMMINSIND.BO, PERSISTENT.BO — stable across retries, while all six `.NS`
equivalents (and the four kept `.BO` symbols) returned full 200-day windows.

The user chose **Option A**: switch those six to `.NS`, keep the four live `.BO`
symbols on `.BO` so the golden set exercises both exchanges. Rationale:
- steering names `.BO, .NS` as co-equal primary markets (not BSE-only);
- Yahoo's `.BO` coverage is degrading (6/10 dead) while `.NS` is healthy;
- for these large caps, BSE vs NSE prices differ only trivially
  (arbitrage-bound) — immaterial for a personal decision-support tool;
- testing against symbols Yahoo no longer serves defeats the "real data"
  requirement.

This supersedes the earlier "all `.BO`" proposal. Ticker resolution
(`quant.Config.resolve_ticker` / `resolve_market_ticker`) is UNCHANGED — this is
a golden-set/test-ticker selection only.

## Saved data

- Price bars saved as CSV/Parquet here once the data service exists (Step 2). Until then this folder holds only this README.
- Each saved file is point-in-time (rule 7): the exact OHLCV used by a test, so a backtest is reproducible (G3: identical results on two runs).

## Loader test

A test (added in Step 0.7) loads every saved ticker and asserts row counts + date ranges, so "golden data loads in a test" (Gate G0) is checkable.
