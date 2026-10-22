# Step 2 — Data Unification: Design Review (r3)

Reviewer: design-review subagent (fresh read, no authoring context)
Document under review: `docs/designs/step-2-data-unification.md`
Governing context read: `docs/unification-plan.md` (Step 2, Gate G2, cross-cutting
invariants); contract files `backend/app/schemas/{recommendation,snapshot,fusion}.py`;
Tracker backend at `Tracker-Tool/backend/`; quant sources
(`quant/config.py`, `quant/data/{fetcher,pipeline,indicators}.py`,
`quant/recommend.py`), `deep/adapter.py`, repo `requirements.txt`, `pyproject.toml`,
and `tests/test_integration_slice.py`.

Method: every source-level claim in the design that affects correctness of the
backend-shell merge, the requirements merge, the G2 identical-bars guarantee, the
schemas, the snapshot idempotency, and the infra/test strategy was checked against
the live source. Results are in the two assumption sections at the end.

## Verdict inputs

Counting findings by severity below. The mechanical verdict (HIGH+MEDIUM > 0 ⇒
CHANGES_REQUESTED) is recorded in `step-2-design-review.json`.

---

## Findings

### 1. [MEDIUM] Gate G2 test #3 "Tracker service path" cannot produce a comparable enriched frame as specified

Where: §9.2 test #3 ("Gate G2 element-wise equality across tracker/quant/deep"),
read together with §4.2 and §4.6.

Problem: §4.2 states normatively that "The service returns RAW OHLCV only.
Indicator enrichment stays in `quant.TechnicalIndicators` ... The service never
computes indicators," and §4.6 says "The Tracker product path ... calls the async
`MarketDataService.get_ohlcv` directly." But §9.2 test #3 asserts
`pandas.testing.assert_frame_equal` **on the canonical columns** AND equal
`compute_snapshot_id` across three paths including "(c) the Tracker service path."
A raw-OHLCV frame from `get_ohlcv` has no indicator columns (SMA_50, RSI, MACD, …)
and `compute_snapshot_id` intersects the canonical set with the columns present
(verified in `backend/app/schemas/snapshot.py`: `cols = [c for c in _CANONICAL_COLUMNS
if c in tail.columns]`). So the Tracker raw frame would hash over `{Open,High,Low,
Close,Volume}` only, while quant/deep hash over the full enriched set — the ids
cannot be equal, and `assert_frame_equal` on "the canonical columns" is ambiguous
about whether it means the 5 OHLCV columns or the full indicator set. The gate as
written is not implementable against the service contract the same document locks.

Why it matters: G2 is THE gate of Step 2. Its acceptance test must be unambiguous
and consistent with the service's return contract, or the implementer will either
weaken the gate or silently make the Tracker path compute indicators (violating
§4.2's "service never computes indicators").

Concrete fix: pin exactly what the three lanes compare. Recommended wording:
"The three-lane G2 comparison is performed on the ENRICHED frame produced by the
ONE shared path: quant `DataPipeline.get_enriched_data` (quant lane), the deep
adapter's identical `DataPipeline` build (deep lane), and — for the Tracker lane —
`TechnicalIndicators.add_all(MarketDataService.get_ohlcv(...))` followed by the
same `dropna`, i.e. the Tracker lane enriches the service's raw bars through the
SAME `quant` indicator code path. The raw-OHLCV equality (service output) is
asserted separately as the precondition; the enriched-frame + `compute_snapshot_id`
equality is asserted on top of it." Alternatively, drop "(c) the Tracker service
path" from the enriched/snapshot-id assertion and assert only that the Tracker
lane's RAW bars equal the raw bars underlying quant/deep — but then state plainly
that G2's snapshot-id identity is a two-lane (quant/deep) guarantee plus a raw-bar
equality for the Tracker lane. Either way, name the enrichment step for the Tracker
lane explicitly.

### 2. [MEDIUM] `build_and_store_snapshot` can compute a snapshot_id that does not match the lane's id, undermining the idempotency/round-trip guarantee

Where: §7.2 (`build_and_store_snapshot(ticker, as_of)` fetches its OWN enriched
frame via `DataPipeline(...).get_enriched_data(..., lookback_days=200,
use_cache=False)` then calls `compute_snapshot_id(ticker, as_of, df)`), read with
§7.3 (idempotency) and §7.4 (the lanes compute the id over THEIR df).

Problem: the design asserts (§7.3, §9.2 test #5) that calling
`build_and_store_snapshot` twice yields "the same `snapshot_id`" and that the
persisted row is the one the `Recommendation` references. But the snapshot_id is a
content hash of the last 60 enriched rows rendered at 6 dp (verified in
`snapshot.py`). `build_and_store_snapshot` fetches with `use_cache=False`, so its
frame comes from a fresh service read; the quant/deep lane that produced the
`Recommendation` fetched at a (possibly) different wall-clock time. Per the design's
OWN §4.4a "recent window" rule and §5.3 overwrite-on-upsert, a recent-`as_of` fetch
can be a yfinance restatement, so two independent `use_cache=False` fetches of a
recent window can legitimately return different bars ⇒ different `snapshot_id`. The
design acknowledges this risk narrowly in A5 ("a `snapshot_id` for a very recent
`as_of` can change") but never reconciles it with the flat claim in §7.3/§7.4 that
the persisted snapshot is THE one the recommendation references. For a recent `as_of`
the stored id can fail to match the id already on the `Recommendation`.

Why it matters: FEAT C's contract is "the `Recommendation.snapshot_id` now points
at a PERSISTED row." If the persister independently refetches, that invariant holds
only for historical `as_of` (DB-authoritative, no refetch) and is not guaranteed for
recent `as_of`. An implementer following §7.2 literally will not detect the mismatch.

Concrete fix: make the persister content-addressed on the SAME frame the lane
hashed, not a re-fetch. Preferred: give `build_and_store_snapshot` an optional
`df` parameter (mirroring `recommend(ticker, as_of, df=None)`) and have the lane's
`snapshot_sink` pass the ALREADY-ENRICHED frame it hashed, so
`compute_snapshot_id` runs over identical bytes and the stored id equals the
recommendation's id by construction:
```python
def build_and_store_snapshot(ticker, as_of, df=None):
    if df is None:
        df = DataPipeline(ticker, interval="1d").get_enriched_data(
            end_date=as_of.isoformat(), interval="1d", lookback_days=200, use_cache=False)
    snapshot_id = compute_snapshot_id(ticker, as_of, df)   # same frame the lane used
    ...
```
State explicitly that when invoked standalone (§9.2 test #5, no lane) the fetch
path is used and the test must pin a HISTORICAL `as_of` (so the DB-authoritative
rule removes restatement non-determinism), matching how test #2a already mandates a
trading-day `as_of`. Add an assertion to test #5 that the stored id equals the id
the lane would compute over the same frame.

### 3. [MEDIUM] `snapshot_sink` seam is specified as a signature change to the two contract-facing lanes without pinning the exact signature, leaving an ambiguity the offline suite depends on

Where: §7.4 ("the lanes gain ... a trailing optional `snapshot_sink` callable
defaulting to `None`").

Problem: the design says the extension is backward-compatible and cites the
verified offline call sites `recommend("TEST", AS_OF, df=enriched)` and
`analyze("TEST", AS_OF)`. Verified in `tests/test_integration_slice.py` those calls
exist. But the current signatures are `recommend(ticker, as_of, df=None)` and
`analyze(ticker, as_of, portfolio=None)` (verified). The design never writes the
resulting signatures, and the placement matters: for `recommend` the sink must go
AFTER `df` (`recommend(ticker, as_of, df=None, snapshot_sink=None)`), and for
`analyze` AFTER `portfolio` (`analyze(ticker, as_of, portfolio=None,
snapshot_sink=None)`). If an implementer inserts the sink before an existing
keyword-defaulted parameter, the positional call `recommend("TEST", AS_OF,
df=enriched)` still works (it is keyword) but any positional third-arg caller would
break, and the "shape of the Recommendation is unchanged" claim needs the sink to
be invoked only as a side effect (never altering the returned object). None of this
is pinned.

Why it matters: the offline suite staying green (a hard Step-2 requirement: "the
existing 39 offline tests stay green") rests on the exact signature and on the sink
being a pure side-effect with a `None` default that is never invoked in Step 2.
"Backward-compatible signature extension" is not specific enough to implement
safely.

Concrete fix: write the two exact signatures in §7.4 and state the invocation
contract: "`snapshot_sink: Callable[[str, date, pd.DataFrame], None] | None = None`,
appended as the LAST parameter of each lane; when non-None it is called AFTER the
`snapshot_id` is computed and BEFORE the `Recommendation` is returned, receiving
the same enriched `df` the id was computed over; its return value is ignored and
any exception it raises is swallowed-and-logged by the caller-supplied wrapper, so
a persist failure never changes the returned `Recommendation` (ties to §7.5). In
Step 2 the default `None` is always used; no Step-2 code passes a sink."

### 4. [NIT] Alembic `env.py` sets `sqlalchemy.url` to the ASYNC URL; the runbook's `alembic upgrade head` therefore requires the async driver and DB reachable, which should be stated

Where: §8.3 step 3 (`alembic -c backend/alembic.ini upgrade head`) and §3.2
(alembic invocation model), against verified `Tracker-Tool/backend/alembic/env.py`
(`config.set_main_option("sqlalchemy.url", settings.database_url)` → the async
`postgresql+asyncpg://` URL, online mode via `asyncio.run(run_async_migrations())`).

Problem: the design's §8.2 `.env.example` defines both `DATABASE_URL`
(asyncpg) and `DATABASE_URL_SYNC` (psycopg2). The relocated `env.py` uses the async
one. The runbook is correct, but the design never states that `alembic upgrade head`
runs through asyncpg (so the compose Postgres must be up and `asyncpg` installed —
both are, per §3.4 and §8.3 step 2), nor that `settings.database_url` must resolve
to the async URL at migration time. A reader could wrongly wire the sync URL into
alembic.

Concrete fix: add a sentence to §3.2/§8.3: "Migrations run in async online mode via
the relocated `env.py` (unchanged), which reads `settings.database_url` (the asyncpg
URL); the compose stack must be healthy first (step 2). The sync URL is used only by
the data-service sync facade, never by alembic."

### 5. [NIT] `requirements.txt` merge omits `email-validator`/`python-jose`/`passlib`/`reportlab` from the "representative" block while also listing them — reconcile the two blocks

Where: §3.4. The prose says the four extra deps are "added only if ... imported by a
module that the relocated shell loads," gated by an implementer grep. The shown
`Resulting requirements.txt` block then lists all four unconditionally
(`email-validator==2.*`, `python-jose[cryptography]==3.3.*`, `passlib[bcrypt]==1.7.*`,
`reportlab==4.*`).

Problem: a reader can't tell whether the four are in or conditional. Since the
relocation copies the WHOLE `backend/app/` tree including `main.py` wiring the auth
and report routers, they will be imported at shell import time, so in practice all
four are required — but the design presents them as both "conditional" and "listed."

Concrete fix: state that because Step 2 relocates the whole shell (auth + report
routers wired in `main.py`), the four are required and listed; keep the grep as a
confirmation step, not a gate that could drop them. One sentence removes the
apparent contradiction.

### 6. [NIT] Golden-set `assert ≥60 bars` / 200-calendar-day survival is asserted but the arithmetic margin is thin and source-dependent

Where: §4.6 ("~200 calendar days ≈ ~135 trading days, leaving ≳60 after dropna")
and §9.2 test #1 / the "≥60 warmed daily bars" assertion.

Problem (verified against `pipeline.py` + `config.py`): daily dropna is on
`["SMA_50","RSI"]`; `SMA_50` needs 50 warmup rows and `RSI` needs 14, so ~50 rows
are consumed. 200 calendar days ≈ 135–138 trading days on BSE (≈252/365 × 200 ≈
137, minus Indian market holidays which are more numerous than US). 137 − 50 ≈ 87
surviving, comfortably > 60 — so the claim holds, but the margin tightens for
volatile periods with long holiday clusters, and the number is not a constant the
design controls. This is a NIT, not a gap, because the live lanes already use
`lookback_days=200` (verified in `recommend.py`/`adapter.py`) so the design merely
preserves existing behavior.

Concrete fix: none required for correctness; optionally note that if a golden
ticker ever yields < 60 surviving daily bars the fix is to raise `lookback_days`
for that fetch, not to change the snapshot tail N.

---

## Verified Assumptions

Each item below was a claim in the design that I confirmed against the live source.

- V-A **Contract files survive the merge / exist today.** `backend/app/schemas/`
  contains `recommendation.py` (defines `Action`, `Recommendation`), `snapshot.py`
  (`SNAPSHOT_TAIL_N=60`, `compute_snapshot_id`, 16-char sha256 prefix, canonical
  columns incl. `SMA_20`/`SMA_50`/`RSI`, cells rendered `f"{float(value):.6f}"`,
  NaN→`"nan"`), `fusion.py` (`fuse`). `schemas/__init__.py` is EMPTY. All match
  §3.1. (Read all four files.)
- V-B **`compute_snapshot_id` renders OHLCV AND volume via `float(...)` at 6 dp,
  intersecting canonical columns present on the frame.** Confirms §4.4b (byte-faithful
  float frame requirement), the NIT-2 volume-integrality reasoning, and the HIGH-1
  precision rationale for `DOUBLE PRECISION`.
- V-C **Exclusive-`end` on the daily fetch.** `quant/data/fetcher.py` daily leg:
  `stock.history(start=start_date, end=end_date, interval=interval)` — yfinance `end`
  is exclusive. Confirms §4.4c / HIGH-A (Option 1 is the correct reproduction).
- V-D **`DataFetcher` internals the §4.4c normalization must mirror.** `lookback_days`
  defaults to `Config.DEFAULT_LOOKBACK_DAYS` (=120, verified in `config.py`); intraday
  clamp to `Config.INTRADAY_MAX_LOOKBACK_DAYS` (=60); `end_date` defaults to today;
  `start_date = end_date - timedelta(days=lookback_days)`; intraday uses
  `history(period=f"{lookback_days}d", interval=...)`, daily uses start/end; OHLCV
  reduced to `["Open","High","Low","Close","Volume"]`; `tz_localize(None)` if tz-aware;
  `index.name = "Datetime"`. All match §4.4c/§4.6.
- V-E **`fetch_latest` is period-based and off the G2 path.** Signature
  `fetch_latest(self, period=None, interval=None)` with `"60d"`/`"3mo"` defaults,
  reached only via `DataPipeline.get_latest_enriched`. Confirms the HIGH-2 decision to
  seam only `fetch_historical`.
- V-F **Daily dropna is computed, not hard-coded.** `pipeline.get_enriched_data`:
  `long_sma_col = f"SMA_{Config.get_indicator_periods(interval)['sma_long']}"`;
  `get_indicator_periods("1d")['sma_long'] == 50`; dropna subset
  `[long_sma_col, "RSI"]` with a `["RSI"]` fallback. Matches §4.6.
- V-G **Both live lanes call the identical pipeline and pass their own ticker to
  `compute_snapshot_id`.** `quant/recommend.py` and `deep/adapter.py` both call
  `DataPipeline(ticker=ticker, interval="1d").get_enriched_data(end_date=as_of.isoformat(),
  interval="1d", lookback_days=200, use_cache=False)` and
  `compute_snapshot_id(ticker, as_of, df)` with the caller's `ticker`. Confirms the §4.3
  snapshot-id ticker-arg caveat and the G2 identity reasoning.
- V-H **`Config.resolve_ticker` handles forex/commodity/stock, returns stock as-is, no
  `.BO`/`.NS`; idempotent on already-suffixed symbols.** Confirms §4.3 (wrapper
  delegating to `Config`, `.BO` default) and the double-resolution idempotency note.
- V-I **`TechnicalIndicators.summarize` is a `@staticmethod` returning a nested
  float/int dict (price/moving_averages/momentum/volatility/volume).** Matches §7.2
  payload `indicator_summary` and V7.
- V-J **`add_all` produces every canonical column** (SMA_20, SMA_50, EMA_12/26, RSI,
  MACD/MACD_Signal/MACD_Histogram, BB_Upper/Middle/Lower/Width, ATR, Volume_SMA,
  Volume_Ratio). Confirms the snapshot canonical-column intersection is non-trivial on
  enriched frames.
- V-K **Offline test call sites.** `tests/test_integration_slice.py` calls
  `recommend("TEST", AS_OF, df=enriched)` and `analyze("TEST", AS_OF)`, patches
  `quant.data.pipeline.DataPipeline.get_enriched_data` via monkeypatch, and stubs the
  gateway. Confirms §7.4/§9.1's "offline suite stays green via injected seams."
- V-L **SRC-T `schemas/__init__.py` `__all__` is exactly 29 names** (6 Alert + 5
  Insight + 6 Education + 12 Report) and re-exports ONLY from `alert_schemas` and
  `report_schemas`; Insight/Education/Report classes all live in `report_schemas.py`
  (`InsightScoreResponse`, `DifficultyLevel`, `EducationModuleResponse`, `GlossaryTerm`
  verified there). Confirms §3.3's merge, the "29+5=34" count, and the dynamic set/len
  CI gate. The "r1 said 30" correction is accurate.
- V-M **`requirements.txt` files.** Repo slice pins verbatim
  (`pandas==2.2.2`, `numpy==1.26.4`, `ta==0.11.0`, `yfinance==0.2.40`,
  `httpx==0.27.*`, `pydantic>=2,<3`, `pytest==8.*`, `pytest-asyncio==0.23.*`, etc.);
  SRC-T `backend/requirements.txt` is unpinned and contains `pytrends`, `bsedata`,
  `sqlalchemy`, `alembic`, `asyncpg`, `psycopg2-binary`, `celery`, `redis`, `httpx`,
  `email-validator`, `python-jose[cryptography]`, `passlib[bcrypt]`, `reportlab`,
  `hypothesis`, `factory-boy`, `pytz`. `feedparser` absent from BOTH. Confirms §3.4
  overlap analysis, the net-new `feedparser`, and A7/A9/A11.
- V-N **`pyproject.toml`** has `packages = ["backend","quant","deep","gateway"]`,
  `asyncio_mode = "auto"`, `requires-python = ">=3.12"`, and NO pytest `markers`.
  Confirms §3.5 (switch to `packages.find`) and §9.1 (register the `integration`
  marker) are genuine additions, and the Python 3.12 target for the dep merge.
- V-O **SRC-T alembic.** `alembic.ini` has `script_location = alembic`,
  `prepend_sys_path = .`, and the `%%(year)…_%%(rev)s_%%(slug)s` file template;
  `env.py` imports `from app.config import settings`, `from app.database import Base`,
  `import app.models`, sets url from `settings.database_url`, runs async online. Initial
  revision `001` has `down_revision=None` and `CREATE EXTENSION IF NOT EXISTS pg_trgm`.
  Confirms §3.2 (all three `app.`→`backend.app.` rewrites + `script_location =
  backend/alembic`), V15, and §5.2 (chain `002` onto `001`, mirror the extension-enable
  for `vector`).
- V-P **SRC-T sync-engine pattern is pre-existing (not new).**
  `tasks/insight_calculator.py` defines `_get_sync_database_url()` (asyncpg→psycopg2
  swap) and `_create_sync_session()` (`create_engine` + `sessionmaker(expire_on_commit
  =False)`, `pool_pre_ping=True`); `price_updater.py` and `trend_fetcher.py` define the
  `_run_async` bridge. Confirms A2/V8 — the design relocates an existing pattern (rule 5),
  not inventing one.
- V-Q **SRC-T `database.py`** uses `create_async_engine` + `async_sessionmaker` +
  `DeclarativeBase` `Base`. Confirms §2 ORM/driver choices and §7.2's "add a sync factory
  alongside the async engine."
- V-R **SRC-T `PriceHistory` uses `Numeric(12,2)` prices + `Integer` volume.** Confirms
  §5.1's stated departure to `DOUBLE PRECISION` + `BigInteger` and the rationale (the
  Tracker's own column type would 4-dp-truncate and break the 6-dp snapshot render).
- V-S **`infra/` exists but is empty** (no compose file present). Matches §8.1 ("exists
  but EMPTY", V14).
- V-T **`gateway/client.py` exists** (deep adapter imports `generate_sync` from it);
  consistent with §4.6/§7 leaving the gateway untouched in Step 2.

## Unverified / Wrong Assumptions

- U-1 **A3 — `.BO` default for bare Indian symbols (product decision, UNVERIFIED by
  design, correctly flagged).** The design defaults a bare stock symbol to `.BO`. The
  golden set is all `.BO`, so for Step 2 this is inert, but a bare known-US ticker would
  be mis-suffixed. The design explicitly flags this as needing confirmation if US tickers
  become first-class. Not a blocker for the golden-set-only Step-2 scope; left as a
  tracked product decision.
- U-2 **A6 — `bsedata` announcements offline reachability + ToU (external, UNVERIFIED).**
  The design ships RSS-first with BSE announcements behind a default-off flag and a
  documented TODO, which is the correct way to not depend on an unverified external
  endpoint. Cannot be verified from source (requires a live endpoint + ToU check); the
  design's default-off posture makes it non-blocking for Step 2.
- U-3 **A8 — exact 10-ticker golden list (pending user confirmation).** `status.md` is
  cited as listing this as an open decision; I did not independently open `status.md` in
  this pass, but the design's handling (materialize the proposed list; changing it changes
  only the saved parquet set, not the design) is sound and non-blocking.
- U-4 **Representative Tracker-dep PATCH versions (`sqlalchemy==2.0.*`, `celery==5.3.*`,
  etc.) resolving cleanly on Python 3.12 (NOT verified — resolved at install time by
  design).** The design is explicit that only the SLICE pins are authoritative and the
  Tracker pins are representative, to be confirmed against `MONO/.venv`. I verified the
  dep NAMES and the slice pins (V-M) and the 3.12 target (V-N); I did NOT run a resolver,
  so cross-dependency compatibility on 3.12 (e.g. `psycopg2-binary==2.9.*`,
  `reportlab==4.*`, `bsedata==1.0.*` wheels on 3.12) is unverified. The design's stated
  invariant ("no overlapping name changes a slice pin; every Tracker import resolves") is
  the right guard, but the review cannot certify the representative pins install together.
  This is a build-time verification the implementer must actually perform; it is called
  out here as a known unverified item, not scored as a finding because the design does not
  claim the representative pins are final.

---

## Summary

The design is unusually thorough and its source-level claims hold up: every
verifiable assertion about the contract files, the quant/deep fetch path, the
exclusive-`end` boundary, the SRC-T schema `__all__` (29 names), the alembic
invocation, the pre-existing sync-engine pattern, and the requirements overlap was
confirmed against live code. The three MEDIUM findings are about internal
consistency of the G2 acceptance test (#1), the snapshot-id/persistence
reconciliation for recent `as_of` (#2), and the under-specified `snapshot_sink`
signature the offline suite depends on (#3) — each is a concrete, bounded fix, not
a redesign. With those three resolved the design is implementation-ready.
