# Status Log

Format per STEERING §11. Newest week on top.

## Week 4 (2026-10-22) — Step 3 quant-core productization (Gate G3)

Current step / gate: Step 3 built on branch `feature/g3` (not yet merged to
main). Gate G3 checks PASS. Worked by Pranav (new collaborator) with Kiro.

Built:
- Frontend from scratch (`frontend/`): Vite + React + TS + Tailwind + Recharts
  Signals & Regime dashboard — colour-coded signal card (BUY/HOLD/SELL), regime
  badge, price + SMA Recharts chart, evidence panel, and a benchmark card. Dark
  "minimal but colourful" theme. Falls back to clearly-labelled sample data when
  the backend is unreachable; auto-switches to live data when up.
- Read-only endpoint `GET /api/quant/signal/{ticker}` (additive router
  `backend/app/routers/quant_signal.py`; `main.py` got one import + one
  include_router line). Runs the quant engine in a worker thread; no auth, no
  new tables. Now also returns a best-effort `benchmark` block.
- Persistence backbone: `RecommendationRecord` ORM (`recommendations` table) +
  Alembic migration 003 (FK snapshot_id -> data_snapshots, unique
  (ticker,as_of,source)); `RecommendationRepository` (async) +
  `SyncRecommendationRepository` (sync, upsert); `persist_quant_recommendation`
  service (snapshot-first, then quant recommend, then upsert).
- Nightly fast-lane Celery task `app.tasks.quant_recommender.generate_nightly_signals`
  on a new `quant` queue (task_routes + beat entry, post-market IST crontab),
  iterating holdings + watchlist tickers.
- Benchmark scoring `quant/backtesting/benchmark.py`: pure deterministic
  `score_vs_benchmark` (strategy vs buy-and-hold on `^NSEI` Nifty 50, 1..5
  rating) + an index-aware `fetch_benchmark_closes` (bypasses the stock `.BO`
  resolution for `^`-prefixed index symbols — fixes the `^NSEI.BO` 404).

Measured results:
- Offline suite (`pytest -q -m "not integration"`): **66 passed, 4 skipped,
  21 deselected** — stable across 3 consecutive runs.
- Integration suite (real Postgres + Redis + yfinance): **21 passed** (was 17 at
  G2; +4 new, G2 tests still green — no regression).
- Gate G3: `tests/test_gate_g3.py` **2 integration passed** — rule-based
  backtest reproducible (identical twice) and regime labels present for all 10
  golden tickers; plus offline reproducibility + benchmark-scoring unit tests.
- Frontend `npm run build`: success (tsc + vite; only the >500 kB chunk advisory).

Open items / notes:
- Docker Desktop on Pranav's laptop needed a reinstall before the DB stack came
  up; a root `.env` (git-ignored, local dev creds) was added to drive the app.
- Vite dev proxy must target `127.0.0.1:8000` not `localhost` (Windows IPv6
  `::1` caused ECONNREFUSED).
- yfinance bumped 0.2.40 -> 1.7.0 in the venv early on (TK independently shipped
  the same pin on main); Pranav's branch keeps main's requirements.txt.

## Week 3 (2026-10-21) — Step 2 data unification (FEAT-001 … FEAT-005)

Current step / gate: Step 2 built, committed, reviewed (APPROVED), and pushed to
`origin/main`. Gate G2 (identical bars + indicators across the three lanes) is
IMPLEMENTED and CONFIRMED live: the real-stack run passed for all 10 golden
tickers (see Measured results and `docs/reviews/step-2-integration-results.md`).

Built across all FEATs:
- FEAT-001 — relocated the full BSE Tracker backend shell into
  `backend/` under the monorepo. Mechanical `app.`→`backend.app.` rename only
  (rule 5: relocate, do not rewrite), 57 files, verified byte-identical to SRC-T
  with the prefix substitution. The three contract files
  (`recommendation`/`snapshot`/`fusion`) are byte-unchanged; `schemas/__init__.py`
  is the merged 34-name export (SRC-T's 29 + the 5 contract names). SRC-T at
  `c:\GIT\Fintech\Tracker-Tool` is left untouched as the running backup until G2
  passes live.
- FEAT-002 — the single unified `MarketDataService`
  (`backend/app/services/data_service.py`): service → Redis → Postgres →
  yfinance, `.BO`/`.NS` resolution, one window-normalization step with an
  EXCLUSIVE `end`/`as_of` boundary, and byte-faithful `float64` frames on every
  path. Added the `PriceBar`, `NewsItem`, `DataSnapshot` models and Alembic
  revision 002 (all three tables + the `vector` extension), the shared sync
  engine in `database.py`, the dependency merge + `packages.find` switch, and the
  ONE `DataFetcher.fetch_historical` seam that routes quant AND deep through the
  service (the G2 core). `price_bars` OHLCV is `DOUBLE PRECISION` (binary64).
- FEAT-003 — news/announcements ingest (RSS-first via `feedparser`, BSE
  announcements behind a default-off flag) with a dedup-hash + point-in-time
  `recent_for_ticker` (news reads are INCLUSIVE `published_at <= as_of`), the
  persisted `DataSnapshot` service (`build_and_store_snapshot`, synchronous,
  idempotent via `ON CONFLICT (snapshot_id) DO NOTHING`, `snapshot_id` from the
  IMPORTED `compute_snapshot_id` — never forked), the forward-compatible lane
  sinks (`snapshot_sink=None` no-op in Step 2), the `infra/` compose + env
  stack, and the `docs/news-sources.md` terms-of-use deliverable.
- FEAT-004 — the real-infra integration suite behind an `integration` marker,
  the Gate G2 cross-lane equality gate (`tests/test_gate_g2.py`), the golden-set
  materialization test, and the offline golden-loader test. Added `pyarrow` for
  lossless parquet golden fixtures. Every integration test skips-with-reason
  (never fails) when Postgres/Redis/yfinance/RSS is unreachable.
- FEAT-005 — this wrap-up: the Week-3 status entry, ADR
  `0007-g2-data-determinism.md`, the news-sources ToU cross-reference, the
  per-logical-unit `step-2:` commits, and the user-authorized push to
  `origin/main`.

Measured results:
- Offline suite (`.venv` python, `pytest -q`, no watch mode): **79 passed,
  0 failed** as the pre-push gate — the 39 pre-existing slice tests stay green
  plus the new offline unit, loader, data-service, and golden-loader tests;
  integration tests skip-with-reason when infra is absent. (In the earlier
  fixture-less environment this read 59 passed / 20 skipped; the 10 golden
  parquet fixtures are now committed under `tests/golden/`, so the golden-loader
  tests run instead of skipping.) Zero failures.
- Gate G2 element-wise frame equality + equal `snapshot_id` across
  tracker/quant/deep: **CONFIRMED live.** The real-stack run (Dockerized
  pgvector Postgres + Redis + live yfinance) passed for all 10 golden tickers at
  `as_of = 2024-01-15` — each ticker 270 `price_bars` rows → 85 enriched rows,
  `frames_equal = yes`, and equal `compute_snapshot_id` across the cold /
  Postgres / Redis paths. Full live integration suite: **17 passed, 0 failed**.
  The 10 golden parquet fixtures are materialized and committed. Row counts and
  per-ticker snapshot-id prefixes are recorded in
  `docs/reviews/step-2-integration-results.md`.

Open items (carried, not blockers to the Step-2 code landing):
- **A8 — golden ticker list + live G2 run. RESOLVED.** The golden set is
  confirmed as the 6 `.NS` + 4 `.BO` set (user-approved Option A after six `.BO`
  symbols returned no yfinance data); `tests/golden/README.md` records the
  rationale as CONFIRMED. The live G2 run was executed on a Docker-capable
  machine with reachable yfinance: the parquet goldens are materialized and
  committed and the G2 gate passed live (results above and in
  `docs/reviews/step-2-integration-results.md`).
- **A3 — `.BO` default for bare Indian symbols.** Resolution appends `.BO` to a
  bare stock symbol; US tickers passed bare would be mis-suffixed. A product
  decision, flagged in the design (§11 A3).
- **A6 — BSE announcements default-off.** `NEWS_BSE_ANNOUNCEMENTS_ENABLED=false`;
  the `bsedata` announcements endpoint reachability + ToS are unverified
  (design §11 A6). Per-source terms of use, attribution, and the allowed-use note
  (personal research, non-redistributed) are documented in
  `docs/news-sources.md`, and the determinism decisions are recorded in ADR
  `0007-g2-data-determinism.md`.

Decision notes added: 0007 (G2 data determinism — exclusive-end boundary +
DOUBLE PRECISION / exact-float Redis serialization).

## Week 2 (2026-10-21) — Unified slice complete (FEAT-001 … FEAT-005)

Current step / gate: Slice done; Gate G1 (structured-output validity) passed.

Built across all FEATs:
- FEAT-001 — local LLM gateway: server-free core (`gateway/client.py`) that is
  the only component talking to Ollama and the single `num_ctx=8192`
  enforcement point; `generate_sync` (/quick + /deep) with one-retry-then-
  structured-error; a `threading` priority gate shared by the async FastAPI
  routes and in-process callers; per-call JSONL logging; and the deterministic
  hash-embedding fallback. Pinned TradingAgents submodule added
  (`third_party/TradingAgents` @ `v0.7.0`, SHA
  `e8f5503e9b86d5c2710b0c76b1f686703968c9df`).
- FEAT-002 — shared contract: the ONE `Recommendation` schema plus the
  contract-adjacent `snapshot.py` (`compute_snapshot_id`, `SNAPSHOT_TAIL_N=60`)
  and `fusion.py` (`fuse` — keep both + conflict flag, never average).
- FEAT-003 — quant core: migrated rule-based library (`quant/`) with
  `recommend(ticker, as_of, df=None)`, deterministic evidence, daily-bar guard.
- FEAT-004 — deep adapter: `deep.analyze(...)` as a plain callable (no web
  framework import), gateway `/deep` call under the shared schema, and the
  sign-preserving number-check so no unchecked model figure escapes.
- FEAT-005 — integration + stability tests and this wrap-up: integration test
  (both lanes share one `snapshot_id`; `fuse` keep-both in both directions),
  live gateway test, and the N=20 validity-rate gate.

Measured results (from `gateway/logs/calls.jsonl`, this hardware — RTX 2050
4 GB, `llama3.2` 3B, `num_ctx=8192`):
- Structured-output validity rate: **20/20 = 1.000** on the dedicated
  `SLICE_VALIDITY_N=20` harness (≥ 0.95 Gate G1 passes). Across all logged
  schema-constrained `/deep` calls in the 3 stability runs: 105/105 = 1.000.
- Gateway throughput: mean ≈ **4.5 tok/s**, peak ≈ 7.4 tok/s. Generations are
  short (schema-constrained signal JSON + terse free-form replies), so the
  per-call token counts are small; this is the measured steady state on the
  4 GB-VRAM laptop and is consistent with the 8k-context decision in ADR 0004
  (the 27 tok/s figure there was a warm single-ticker benchmark, not the mixed
  short-call test traffic measured here).
- Full-suite stability: three consecutive `pytest` runs each reported
  **39 passed, 0 failed** — identical pass counts, zero flakiness.

Blockers: none outstanding. TradingAgents is pinned and checked out (no
documented-blocker was needed); the real LangGraph wiring remains a documented
TODO seam in `deep/adapter.py` (config-only, STEERING rule 2) for a later step.

Decision notes added: 0005 (hash-embedding fallback), 0006 (editable-install
import wiring). Runbook now documents the ONE import line, the `pip install -e .`
step, and the fresh-checkout order.

## Week 1 (2026-10-21)

Current step / gate: Step 0 (Foundations) — scaffolding, pre-G0.

Done:
- Verified the real state of all three source repos (see scope.md). Confirmed `c:\GIT\Fintech\agents` is Polymarket Agents, NOT TradingAgents — TradingAgents must be added as a submodule.
- Verified local toolchain: git 2.51, Python 3.12 via `py` launcher (PATH `python` is a broken Store stub), Node 24.15, Docker 29.6, Ollama 0.40.6 (only `llama3.2` ~3B pulled).
- Created the monorepo skeleton at `c:\GIT\Fintech\repo` with the STEERING §4 layout.
- Wrote docs: scope.md, unification-plan.md, status.md, backlog.md, decisions/0001-0003, runbook.md, tests/golden/README.

Blocked / awaiting user decision:
- Monorepo location confirmation (proposed `c:\GIT\Fintech\repo`).
- TradingAgents submodule URL/tag to pin (proposed `TauricResearch/TradingAgents` @ latest release tag).
- Golden-set exact ticker list (proposed in tests/golden/README).
- Whether to `git init` MONO now or fold into an existing repo.

Risks (top 3):
1. 8 GB VRAM is tight for an 8B model at 16k context — deep lane speed may force weekly-only runs (stop/pivot rule). Mitigation: measure in Step 0.5 before committing.
2. Two source repos share an owner (`tandavinci`) and have divergent frontends (Recharts vs Chart.js); merge must be staged to avoid breaking the Tracker (rule 5).
3. yfinance reliability for `.BO`/`.NS` and past-dated fundamentals withheld — backtests must use technical + news inputs only (rule 7).

Next week (top 3):
1. Confirm the four blocked decisions with the user; then `git init` MONO + add submodule (0.2, 0.3).
2. Record the three baselines (0.4) and pull + benchmark an 8B 4-bit model (0.5).
3. Build the golden set and its loader test (0.7); pass Gate G0.

Decision notes added: 0001 (monorepo strategy), 0002 (Windows Docker/Ollama route), 0003 (TradingAgents pinning).
