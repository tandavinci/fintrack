# Step 2 — Cross-FEAT integration verification results

Convergence-fixer run against the real stack (no mocks, no stubs). Recorded for
the reviewer per the Gate G2 requirement.

## Environment

- Repo: `c:\GIT\Fintech\repo` (direct, no worktree).
- Interpreter: `c:\GIT\Fintech\repo\.venv\Scripts\python.exe`.
- Stack: `docker compose -f infra/docker-compose.yml up -d` — `pgvector/pgvector:pg16`
  and `redis:7-alpine`, both healthchecks green.
- DB/Redis URLs (compose defaults):
  - `DATABASE_URL=postgresql+asyncpg://fintrack:fintrack_secret@localhost:5432/fintrack`
  - `DATABASE_URL_SYNC=postgresql+psycopg2://fintrack:fintrack_secret@localhost:5432/fintrack`
  - `REDIS_URL=redis://localhost:6379/0`

## Commands run

1. `python -c "import backend.app.main"` + `len(schemas.__all__)==34` → import wiring OK.
2. `python -m alembic -c backend/alembic.ini upgrade head` (from repo root) →
   `-> 001 -> 002`. Verified: `alembic_version=002`, tables `price_bars`,
   `news_items`, `data_snapshots` present, `vector` extension installed.
3. `python -m pytest -q` (offline) → non-integration tests green.
4. `python -m pytest -m integration -q` (stack up) → see outcomes below.

## Gate G2 — cross-lane equality (THE gate) — ALL 10 GOLDEN TICKERS

For every one of the 10 golden tickers the three lanes (quant `DataPipeline`,
deep adapter pipeline, Tracker `MarketDataService` + the one
`TechnicalIndicators` path) produced **byte-identical enriched frames AND an
identical `compute_snapshot_id`**. `as_of = 2024-01-15` (a trading session).
Real measured values (no fabricated rows); every ticker returned data, so there
are no skips.

| Ticker | price_bars rows | enriched rows | frames_equal | snapshot_id (prefix) | G2_equal |
| --- | --- | --- | --- | --- | --- |
| RELIANCE.NS | 270 | 85 | yes | `36548e2edbdb15a1` | **yes** |
| TCS.BO | 270 | 85 | yes | `b1c42546c2e5451d` | **yes** |
| HDFCBANK.NS | 270 | 85 | yes | `26ffd937ddf55b7b` | **yes** |
| INFY.NS | 270 | 85 | yes | `2d14a8de9af087cf` | **yes** |
| ICICIBANK.NS | 270 | 85 | yes | `822c3002b7614cfe` | **yes** |
| CUMMINSIND.NS | 270 | 85 | yes | `767ff7a23a9b7304` | **yes** |
| PERSISTENT.NS | 270 | 85 | yes | `e285bdd0426c66ef` | **yes** |
| MPHASIS.BO | 270 | 85 | yes | `4574aac62a99017e` | **yes** |
| YESBANK.BO | 270 | 85 | yes | `6768f5ebeaff6e19` | **yes** |
| IDEA.BO | 270 | 85 | yes | `631ae3b7c5a24125` | **yes** |

Full live integration suite: **17 passed, 0 failed** (`pytest -m integration`):
- `test_gate_g2.py::test_gate_g2_cross_lane_frame_and_snapshot_equality` PASSED
  (across all 10 materialized golden tickers).
- `test_data_service_integration.py` — all 4 PASSED (real fetch lands rows,
  cached second read hits no network, cold==DB==Redis snapshot_id on a
  trading-day `as_of`, historical read is DB-authoritative).
- `test_golden_materialize.py` — all 10 PASSED (fixtures materialized).
- `test_news_ingest_integration.py::test_ingest_is_idempotent_on_rerun` PASSED
  (real Postgres, dedup re-run adds 0 rows).
- `test_snapshot_integration.py::test_snapshot_build_is_idempotent` PASSED
  (same snapshot_id twice, exactly one `data_snapshots` row, payload
  round-trips).

## Cross-FEAT seam fixes applied (committed)

Commit `step-2: fix cross-FEAT integration seams ...`:

1. **yfinance pin `0.2.40` → `1.7.0`.** Yahoo added a bot-protection check that
   0.2.40's plain-`requests` transport cannot clear — every fetch returned 0
   rows ("Expecting value / no timezone found") even though raw HTTPS to Yahoo
   returns 200. yfinance ≥ ~0.2.5x switched to `curl_cffi` (browser TLS/JA3
   impersonation) to get past it. 1.7.0 verified live to return real OHLCV.
   Ref: https://github.com/ranaroussi/yfinance/issues/2469
2. **Shared event loop for the async integration path** (`tests/integration_helpers.run_async`).
   Repeated `asyncio.run(...)` each created+closed a fresh loop, leaving the
   module-level async SQLAlchemy engine + async Redis client bound to a CLOSED
   loop → "Event loop is closed" / asyncpg "'NoneType' object has no attribute
   'send'". One persistent loop (matching the app's single-loop production
   model) fixes it. Production code unchanged.
3. **Robust `require_yfinance` probe.** It hardcoded `RELIANCE.BO` (now a dead
   BSE symbol on Yahoo), so one unavailable golden ticker made the ENTIRE
   integration suite skip while the data path was healthy. The probe now
   succeeds on the first golden ticker that returns rows.

## Golden `.BO` ticker availability — RESOLVED (user decision, Option A)

Real-data finding, stable across retries: six `.BO` symbols (RELIANCE.BO,
HDFCBANK.BO, INFY.BO, ICICIBANK.BO, CUMMINSIND.BO, PERSISTENT.BO) returned NO
data from Yahoo, while all six `.NS` equivalents — and the four live `.BO`
symbols (TCS.BO, MPHASIS.BO, YESBANK.BO, IDEA.BO) — returned full 200-day
windows.

**User chose Option A:** switch the six dead symbols to `.NS`, keep the four
live symbols on `.BO`, so the golden set spans BOTH exchanges. The hardcoded
`RELIANCE.BO` in `test_data_service_integration.py` and
`test_snapshot_integration.py` was switched to `RELIANCE.NS`. Ticker resolution
(`quant.Config.resolve_ticker`) was NOT changed — golden-set/test-ticker
selection only. Rationale recorded in `tests/golden/README.md`. Final golden set:

```
RELIANCE.NS, TCS.BO, HDFCBANK.NS, INFY.NS, ICICIBANK.NS,
CUMMINSIND.NS, PERSISTENT.NS, MPHASIS.BO, YESBANK.BO, IDEA.BO
```

After the switch, the full live integration suite is green (17 passed, above).
