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

## Gate G2 — cross-lane equality (THE gate)

For every materialized golden ticker the three lanes (quant `DataPipeline`,
deep adapter pipeline, Tracker `MarketDataService` + the one
`TechnicalIndicators` path) produced **byte-identical enriched frames AND an
identical `compute_snapshot_id`**. `as_of = 2024-01-15` (a BSE session).

| Ticker | price_bars rows | enriched rows | frames_equal | snapshot_id (prefix) | G2_equal |
| --- | --- | --- | --- | --- | --- |
| TCS.BO | 270 | 85 | yes | `bc3906e7658bcd4f` | **yes** |
| MPHASIS.BO | 270 | 85 | yes | `9600d9fd016f0831` | **yes** |
| YESBANK.BO | 270 | 85 | yes | `6768f5ebeaff6e19` | **yes** |
| IDEA.BO | 270 | 85 | yes | `631ae3b7c5a24125` | **yes** |

`tests/test_gate_g2.py::test_gate_g2_cross_lane_frame_and_snapshot_equality` **PASSED**.
`tests/test_news_ingest_integration.py::test_ingest_is_idempotent_on_rerun` **PASSED**
(real Postgres, dedup re-run adds 0 rows).

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

## OPEN decision (blocks full-set APPROVED) — golden `.BO` ticker availability

Real-data finding, stable across retries (`as_of` clock is 2026-10):

| Returns data on Yahoo `.BO` | No data on `.BO` (but works on `.NS`) |
| --- | --- |
| TCS.BO, MPHASIS.BO, YESBANK.BO, IDEA.BO | RELIANCE.BO, HDFCBANK.BO, INFY.BO, ICICIBANK.BO, CUMMINSIND.BO, PERSISTENT.BO |

All 10 `.NS` (NSE) equivalents return data. The golden set in
`tests/golden/README.md` is explicitly flagged "NEEDS USER CONFIRMATION".

Because switching the golden/test tickers from `.BO` to `.NS` (or changing the
default ticker resolution) changes WHICH EXCHANGE'S PRICES the whole system
reads — a user-visible product decision — the fixer did NOT make that change.
Failing integration cases that are blocked purely by this (all trace to dead
`.BO` symbols, `RELIANCE.BO` in particular):

- `tests/test_data_service_integration.py` (4 tests, `GOLDEN = "RELIANCE.BO"`).
- `tests/test_snapshot_integration.py` (`TICKER = "RELIANCE.BO"`).
- `tests/test_golden_materialize.py` — 6 dead-symbol cases.

Awaiting the user's choice of golden tickers / exchange before these can pass.
