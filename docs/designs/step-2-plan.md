# Step 2 — Data Unification: Implementation Plan

Derived from the APPROVED design `docs/designs/step-2-data-unification.md` (the
design is authoritative; this plan only sequences it). Grounded in the live
source read during exploration — every file path below exists or is created as
stated. Gate: **G2** (`unification-plan.md` §Step 2) — for every golden ticker,
tracker + quant core + deep engine read identical prices and indicators.

## Environment & commands (discovered; use these verbatim)

- MONO = `c:\GIT\Fintech\repo`. SRC-T = `c:\GIT\Fintech\Tracker-Tool`.
- Python 3.12 via the `py` launcher; the ONLY interpreter for commands is the
  venv: `c:\GIT\Fintech\repo\.venv\Scripts\python.exe`. `python`/`python3` on
  PATH are broken Store stubs — never call them.
- Install: `.venv\Scripts\python.exe -m pip install -r requirements.txt` then
  `.venv\Scripts\python.exe -m pip install -e .` (re-run the editable install
  only after `pyproject.toml`'s package list changes — step 3 changes it).
- Import check (the "build"):
  `.venv\Scripts\python.exe -c "import backend.app.main"` (and
  `import backend.app.schemas.recommendation`).
- Offline tests (must stay green, currently 39):
  `.venv\Scripts\python.exe -m pytest -q`.
- Integration tests (require the stack up):
  `.venv\Scripts\python.exe -m pytest -m integration -q`.
- GIT QUIRK: MONO sits inside an unrelated outer git repo (`C:\GIT\.git`). Scope
  EVERY git command with `git -C c:\GIT\Fintech\repo ...` and stage specific
  files; never `git add .`, never run git from a parent dir.
- Docker: `docker compose -f infra/docker-compose.yml up -d` / `... down`.
- Alembic runs FROM THE REPO ROOT: `alembic -c backend/alembic.ini upgrade head`
  (invoke via `.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade head`).

---

- [ ] 1. Relocate the SRC-T backend shell into `MONO/backend/` (design §3).
      Copy SRC-T `backend/app/{models,repositories,routers,services,tasks,utils}`,
      `cache.py`, `celery_app.py`, `config.py`, `database.py`, `dependencies.py`,
      `main.py`, plus `alembic/`, `alembic.ini`, `seed_data.py`, `Dockerfile`,
      `requirements.txt` into `MONO/backend/`. DO NOT overwrite the three contract
      files `backend/app/schemas/{recommendation,snapshot,fusion}.py`. MERGE
      `backend/app/schemas/__init__.py` (design §3.3): copy SRC-T's `__init__`
      verbatim (29 `__all__` names) and PREPEND the 5 contract symbols
      (`Action, Recommendation, SNAPSHOT_TAIL_N, compute_snapshot_id, fuse`) with
      `backend.app.schemas.*` import paths → merged `__all__` length 34. Rewrite
      every intra-package import `app.` → `backend.app.` across the relocated
      modules AND `alembic/env.py` (all three imports there). Rewrite
      `alembic.ini` `script_location = backend/alembic`, keep `prepend_sys_path = .`.
      SRC-T stays untouched as the running backup.
      Files: `backend/app/**` (new relocated modules), `backend/app/schemas/__init__.py`
      (merged), `backend/alembic/**`, `backend/alembic.ini`, `backend/seed_data.py`,
      `backend/Dockerfile`, `backend/requirements.txt`.
      Verify: `.venv\Scripts\python.exe -c "from backend.app.schemas import Recommendation, AlertCreate, compute_snapshot_id; import backend.app.schemas as s; assert len(s.__all__)==34; print('ok')"` succeeds (after step 2's install).

- [ ] 2. Merge dependencies and switch package discovery (design §3.4, §3.5).
      Merge the Tracker runtime deps into root `requirements.txt` keeping the
      slice's exact pins for every overlapping name (`fastapi uvicorn pydantic
      pydantic-settings httpx pytest pytest-asyncio` unchanged; `httpx` is
      overlapping — keep `==0.27.*`). Add pinned Tracker-only deps: `sqlalchemy
      alembic asyncpg psycopg2-binary celery redis pytrends bsedata pytz`, plus
      `email-validator python-jose[cryptography] passlib[bcrypt] reportlab` ONLY
      if reachable from the `backend.app.main` import graph (grep-gate per §3.4),
      `feedparser` (new, FEAT B), and test-only `hypothesis factory-boy`. Switch
      `pyproject.toml` to `[tool.setuptools.packages.find]` with
      `include=["backend*","quant*","deep*","gateway*"]`,
      `exclude=["third_party*","frontend*","tests*"]`.
      Files: `requirements.txt`, `pyproject.toml`.
      Verify: `.venv\Scripts\python.exe -m pip install -r requirements.txt && .venv\Scripts\python.exe -m pip install -e . && .venv\Scripts\python.exe -c "import backend.app.main; print('ok')"` succeeds; `.venv\Scripts\python.exe -m pytest -q` still reports the existing offline tests green.

- [ ] 3. Add the `PriceBar` model + Alembic revision 002 creating ALL THREE
      Step-2 tables (design §5). `PriceBar` with OHLCV as `Float(precision=53)`
      (DOUBLE PRECISION), `volume BigInteger`, tz-naive `ts`, unique
      `(ticker, interval, ts)`, lookup index. Revision 002 (`down_revision="001"`)
      first runs `CREATE EXTENSION IF NOT EXISTS vector`, then creates
      `price_bars`, `news_items` (§6.2), and `data_snapshots` (§7.1) in one
      migration (design decision A10). Add a sync engine/session helper in
      `backend/app/database.py` (asyncpg→psycopg2 URL swap + `sessionmaker`,
      mirroring SRC-T `insight_calculator._create_sync_session`) and a
      `DATABASE_URL_SYNC`/news settings in `config.py`.
      Files: `backend/app/models/price_bar.py`, `backend/app/models/news_item.py`,
      `backend/app/models/data_snapshot.py`, `backend/app/database.py`,
      `backend/app/config.py`, `backend/alembic/versions/<ts>_002_step2_data_tables.py`.
      Verify: `.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade head` creates the three tables + `vector` extension (stack up); `alembic downgrade -1` then `upgrade head` round-trips.

- [ ] 4. Build the unified `MarketDataService` + repository + ticker resolution,
      and re-point the quant `DataFetcher` seam (design §4). Create
      `ticker_resolution.resolve_market_ticker` (delegates to quant
      `Config.resolve_ticker`, defaults bare Indian stocks to `.BO`);
      `price_bar_repository` (async upsert via `pg_insert().on_conflict_do_update`
      on `(ticker,interval,ts)`, point-in-time read `ts < end`);
      `data_service.MarketDataService.get_ohlcv` (async) with §4.4c window
      normalization (concrete `start/end`, EXCLUSIVE `end`, intraday 60-day clamp),
      read path Redis→Postgres→yfinance, two-regime coverage (§4.4a), byte-faithful
      float frames (§4.4b), plus a `get_ohlcv_sync` facade. Extend `cache.py` with
      `PREFIX_OHLCV` + intraday/daily TTLs and the exact-float JSON serialization
      (§4.5). Re-point ONLY `quant.data.fetcher.DataFetcher.fetch_historical` to
      call `get_ohlcv_sync` (pass `display_ticker` + `asset_class`); keep its
      signature + return contract identical; leave `fetch_latest` on yfinance.
      Files: `backend/app/services/ticker_resolution.py`,
      `backend/app/repositories/price_bar_repository.py`,
      `backend/app/services/data_service.py`, `backend/app/cache.py`,
      `quant/data/fetcher.py`.
      Verify: offline suite green (`pytest -q`) incl. a new no-infra key-parity +
      fetch-shape unit test (§9.4); integration test #1/#2/#2a/#3 pass with the
      stack up — rows land in `price_bars`, second read skips yfinance, and
      cold==DB==Redis `snapshot_id` with a trading-day `as_of`.

- [ ] 5. News ingest (FEAT B, design §6). `NewsItem` model already migrated in
      step 3; add `news_repository` (`recent_for_ticker` with inclusive
      `published_at <= as_of`, `ON CONFLICT (dedup_hash) DO NOTHING`) and
      `news_service` (RSS via `feedparser`, dedup hash per §6.3, BSE announcements
      behind default-off `NEWS_BSE_ANNOUNCEMENTS_ENABLED` flag, partial-failure
      summary dict). Write `docs/news-sources.md` with per-source name/URL/ToU/
      attribution/allowed-use (hard deliverable).
      Files: `backend/app/repositories/news_repository.py`,
      `backend/app/services/news_service.py`, `docs/news-sources.md`.
      Verify: offline unit tests for the dedup-hash function pass; integration
      test #4 passes — ingest a locally-served fixture feed, re-run adds no
      duplicate rows.

- [ ] 6. Persisted snapshot (FEAT C, design §7). `DataSnapshot` model already
      migrated in step 3; add `snapshot_service.build_and_store_snapshot(ticker,
      as_of)` — builds the enriched daily frame through the SAME `DataPipeline`
      path (now routed through the service), imports the EXISTING
      `compute_snapshot_id` (never forked), idempotent insert `ON CONFLICT
      (snapshot_id) DO NOTHING`, JSONB payload (bars tail + indicator summary +
      headlines). Add the backward-compatible optional `snapshot_sink=None`
      trailing param to `quant.recommend.recommend` and `deep.adapter.analyze`
      (no Step-2 caller passes a non-None sink; offline behavior byte-identical).
      Files: `backend/app/services/snapshot_service.py`, `quant/recommend.py`,
      `deep/adapter.py`.
      Verify: offline suite green (default `None` sink → no-op; existing
      `test_integration_slice.py` call sites unchanged); integration test #5 +
      #5a pass — snapshot idempotency (same id, one row, payload round-trips) and
      historical read is DB-authoritative (zero yfinance hits).

- [ ] 7. Infra stack + runbook (design §8). Create `infra/docker-compose.yml`
      (`name: fintech-infra`, `pgvector/pgvector:pg16` + `redis:7-alpine`, named
      volumes, healthchecks) and `infra/.env.example` (async + sync DB URLs,
      Redis URL, Ollama host route, placeholder `.invalid` RSS feed +
      default-off BSE flag). Append a "Step 2 — data stack" section to
      `docs/runbook.md` (cp env, compose up, `alembic upgrade head`, smoke test,
      tear down).
      Files: `infra/docker-compose.yml`, `infra/.env.example`, `docs/runbook.md`.
      Verify: `docker compose -f infra/docker-compose.yml config` parses; `up -d`
      brings both healthchecks green; `alembic -c backend/alembic.ini upgrade head`
      succeeds against the stack.

- [ ] 8. Real-data integration test suite + golden materialization (design §9).
      Register the `integration` marker in `pyproject.toml`. Add
      `@pytest.mark.integration` tests under `tests/` with skip-with-reason
      fixtures for unreachable Postgres/Redis/yfinance/RSS (mirror
      `test_gateway_live._ollama_up` + `pytest.skip`): golden-ticker fetch →
      `price_bars` rows + cached second read (#1/#2), cold==DB==Redis snapshot_id
      with a trading-day `as_of` (#2a), Gate G2 element-wise frame equality +
      equal `compute_snapshot_id` across tracker/quant/deep (#3), news dedup
      re-run (#4), snapshot idempotency (#5), historical no-refetch (#5a), skip
      cases (#6). Materialize the golden set: fetch each proposed `.BO` ticker's
      200-day daily bars through `MarketDataService` and save to
      `tests/golden/<TICKER>_1d.parquet`; add a FAST offline loader test asserting
      row counts + date ranges.
      Files: `tests/test_data_service_integration.py`,
      `tests/test_gate_g2.py`, `tests/test_news_ingest_integration.py`,
      `tests/test_snapshot_integration.py`, `tests/test_golden_loader.py`,
      `tests/golden/*.parquet`, `pyproject.toml`.
      Verify: `.venv\Scripts\python.exe -m pytest -q` keeps the offline suite
      green (39 + new offline tests, integration skipped); with the stack up
      `.venv\Scripts\python.exe -m pytest -m integration -q` passes incl. the G2
      gate.

- [ ] 9. Finalize: docs, commits per logical unit, push (design §9 FINAL + Gate
      G2). Update `docs/status.md` with a Week-3 Step-2 entry; add a G2 decision
      note under `docs/decisions/` (exclusive-`end` boundary + DOUBLE PRECISION
      storage rationale) and the terms-of-use note (cross-ref `docs/news-sources.md`).
      Commit each logical unit with `step-2:` messages (staging specific files,
      `git -C c:\GIT\Fintech\repo`), then push to `origin/main` (user authorized
      per-step push; no force-push, no git config changes, no PR, submodule
      untouched).
      Files: `docs/status.md`, `docs/decisions/0007-g2-data-determinism.md`
      (new), `docs/news-sources.md` (terms note, if not already complete).
      Verify: `git -C c:\GIT\Fintech\repo log --oneline` shows the `step-2:`
      commits; `git -C c:\GIT\Fintech\repo status` clean; full offline suite green
      one last time before push.

---

### Blocked / assumption notes (from design §11; do not stall on these)
- **A3** `.BO` default for bare Indian symbols — golden set is all `.BO`, so this
  is a non-issue for G2; proceed with the `.BO` default.
- **A6** `bsedata` announcements offline reachability unverified — ship RSS-first
  with BSE behind the default-off flag + a `# TODO(step-2)` note; do not block.
- **A8** exact golden-ticker list still "NEEDS USER CONFIRMATION" — materialize
  the proposed 10-ticker list from `tests/golden/README.md`; if the user later
  changes it, only the saved parquet set changes, not the design.
