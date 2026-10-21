# Golden set

10 BSE tickers with saved price data for repeatable tests (STEERING Step 0.5, rule 4). These back the reproducibility and data-parity gates (G2, G3) and the backtest grid (G6).

## Proposed tickers (NEEDS USER CONFIRMATION)

Mix per steering: large caps, mid caps, 2 volatile. All `.BO` (BSE) with `.NS` (NSE) resolution handled by the data service.

Large caps:
1. RELIANCE.BO
2. TCS.BO
3. HDFCBANK.BO
4. INFY.BO
5. ICICIBANK.BO

Mid caps:
6. CUMMINSIND.BO
7. PERSISTENT.BO
8. MPHASIS.BO

Volatile (2):
9. YESBANK.BO
10. IDEA.BO  (Vodafone Idea)

## Saved data

- Price bars saved as CSV/Parquet here once the data service exists (Step 2). Until then this folder holds only this README.
- Each saved file is point-in-time (rule 7): the exact OHLCV used by a test, so a backtest is reproducible (G3: identical results on two runs).

## Loader test

A test (added in Step 0.7) loads every saved ticker and asserts row counts + date ranges, so "golden data loads in a test" (Gate G0) is checkable.
