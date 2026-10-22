# Step 2 — Data Unification: Semantic Code Review (Gate G2)

Step 2 unifies data access so the Tracker shell, the quant core, and the deep
engine all read OHLCV through one `MarketDataService`, persist bars in
`price_bars`, and derive indicators on a single unchanged `TechnicalIndicators`
path. The review confirms Gate G2: for every golden ticker the three lanes
produce byte-identical enriched frames and an identical `compute_snapshot_id`.
The approach routes every lane through one `DataFetcher` seam (quant's single
yfinance touchpoint), stores OHLCV as IEEE-754 `DOUBLE PRECISION` with
exact-float Redis serialization, and pins an EXCLUSIVE `end`/`as_of` boundary
(`ts < end`) on the cold-fetch, DB-served, and Redis-served paths so the frame
feeding the snapshot id is the same on every path.

Watch for: the shipped golden set is 6 `.NS` + 4 `.BO`, a deliberate deviation
from the all-`.BO` design text (user-approved Option A — **confirmed**, not
blocking); FEAT-005's own findings recorded the live G2 run as PENDING, but two
later commits and `step-2-integration-results.md` record the completed 10-ticker
run and the parquet fixtures are committed and the tree is clean, so the gate
evidence exists and is internally consistent (**confirmed**).

**Verdict**: APPROVED

## High-level view

The G2 seam lives in exactly one place: `DataFetcher.fetch_historical` now calls
`MarketDataService().get_ohlcv_sync(...)` and keeps its signature and OHLCV-only
`float64` tz-naive return contract, so `DataPipeline`, `TechnicalIndicators`, and
the dropna are byte-unchanged and both quant and deep inherit the shared service
from one edit. `fetch_latest` is intentionally left on direct yfinance and is off
the G2 golden path.

Determinism rests on two storage choices that are implemented as designed:
`price_bars` OHLCV columns are `Float(precision=53)` (binary64, bit-identical to
a pandas `float64` cell) and the Redis serializer casts `np.float64 -> float`
with no rounding, so cold-fetch, DB-served, and Redis-served frames carry the
same floats. The point-in-time read predicate is `ts >= start AND ts < end` in
both the async and sync repositories, matching yfinance's exclusive `end`.

The snapshot builder imports the canonical `compute_snapshot_id` from
`backend.app.schemas.snapshot` and never forks it; idempotency comes from the
content hash being the primary key plus `ON CONFLICT (snapshot_id) DO NOTHING`.

The recorded live run reports Gate G2 green for all 10 golden tickers
(element-wise frame equality + equal `compute_snapshot_id`, `as_of`=2024-01-15)
and 17 integration tests passing; the offline suite stays green with integration
skipped-with-reason when infra is absent. The golden set changed from all-`.BO`
to a 6 `.NS` + 4 `.BO` mix after six `.BO` symbols returned no yfinance data — a
user-approved selection change that does not touch ticker-resolution code.

<details>
<summary>Issues (3)</summary>

1. **Golden set deviates from design text** (NIT) — design §1 and the FEAT task
   descriptions say the golden set is all `.BO`; the shipped set is 6 `.NS` + 4
   `.BO`. This is user-approved Option A, recorded in `tests/golden/README.md`,
   `step-2-integration-results.md`, and ADR 0007; resolution code is unchanged.
   Non-blocking; the design §1 prose is now stale and could be updated to match.
2. **FEAT-005 findings contradict the completed G2 run** (NIT) — FEAT-005's
   `findings` record the live G2 result and parquet fixtures as PENDING, but two
   later commits and `step-2-integration-results.md` record the completed run and
   the 10 parquet files are committed. The FEAT-005 record is stale relative to
   the final committed state; no code impact.
3. **G2 exercised at a single `as_of`** (NIT) — the gate runs at
   `as_of`=2024-01-15 only. The determinism guarantees (exclusive end, binary64,
   exact-float cache) are path-invariant, so one trading-day anchor is adequate
   for G2; a second anchor would add confidence but is not required by the gate.

</details>

<details>
<summary>Details</summary>

### The one G2 seam in DataFetcher

`quant/data/fetcher.py` is the crux. `fetch_historical` no longer calls
`yf.Ticker(...).history(...)`; its body converts the ISO `start`/`end` strings to
`date` and delegates to `MarketDataService().get_ohlcv_sync(ticker=self.display_ticker,
interval=..., start=..., end=..., lookback_days=..., asset_class=self.asset_class)`.
The signature `(start_date, end_date, lookback_days, interval)` and the documented
return contract (OHLCV-only `float64`, tz-naive `Datetime`-named index) are
preserved, so `DataPipeline.get_enriched_data`, `TechnicalIndicators.add_all`, and
the `dropna(subset=["SMA_50","RSI"])` are untouched. Because `deep/adapter.py`
builds its frame through the same `DataPipeline`, routing the one fetcher feeds
every lane — which is exactly what makes the three lanes read identical bars.

`fetch_latest` is left calling yfinance directly. The design justifies this: it
is period-based with no `start`/`end`/`lookback_days`, and it is reached only via
`get_latest_enriched`, not the `get_enriched_data(lookback_days=200, interval="1d")`
path both `quant.recommend` and `deep.analyze` use. It is off the G2 golden path,
so leaving it unrouted does not weaken the gate.

```
quant.recommend ─┐
                 ├─ DataPipeline.get_enriched_data ─ DataFetcher.fetch_historical ─┐
deep.analyze ────┘                                                                 │
                                                          MarketDataService.get_ohlcv_sync
                                                                    │
Tracker ── MarketDataService.get_ohlcv ─────────────────────────────┘
                                                   Redis → Postgres(price_bars) → yfinance
```

### Byte-faithful float storage is the determinism cornerstone

`compute_snapshot_id` renders OHLCV via `float(value)`, so two lanes reading the
same `(ticker, as_of)` at different times (one cold, one from a warm cache the
other missed) must see bit-identical floats or they compute different ids.

The implementation honors the design's Option A. `backend/app/models/price_bar.py`
uses `Float(precision=53)` for open/high/low/close — Postgres `DOUBLE PRECISION`,
the same IEEE-754 binary64 as a pandas `float64` cell — so a write/read round-trip
is bit-identical rather than the lossy `Numeric(18,4)` the earlier draft used. The
Redis path in `cache.py` casts each `np.float64` to a Python `float` before
`json.dumps` (both binary64) with no `round()`, so the deserialized frame matches
the cached one. Volume stays `BigInteger`, which is byte-faithful only because
every configured source returns integral volume; the design flags fractional
volume (split/dividend-adjusted feeds) as the one case that would need the column
widened. Not a Step-2 concern, but worth remembering if a source changes.

### Exclusive end boundary on every path

`price_bar_repository.py` implements the point-in-time read as
`ts >= start AND ts < end` in both `PriceBarRepository` (async) and
`SyncPriceBarRepository` (sync), and `count_bars` uses the same half-open window
for the coverage decision. This matches yfinance's exclusive `end` so a
DB-served or Redis-served frame carries exactly the rows a cold fetch would, and
no bar dated `as_of` leaks in (no lookahead). The news repository deliberately
uses the opposite `published_at <= as_of` (inclusive) — a headline published on
`as_of` is legitimately known, whereas a bar dated `as_of` is an unclosed
session. The asymmetry is intentional and documented; it should not be
"reconciled" by a later step.

### Snapshot builder imports the canonical id, never forks

`backend/app/services/snapshot_service.py` imports `compute_snapshot_id` and
`SNAPSHOT_TAIL_N` from `backend.app.schemas.snapshot` and calls the imported
function on the enriched frame — no reimplementation, no alternate column logic.
The `data_snapshots` primary key is that 16-char id, and the insert is
`pg_insert(...).on_conflict_do_nothing(index_elements=["snapshot_id"])`, so a
second build with the same inputs returns the stored payload and leaves exactly
one row. The enriched frame is built through the same `DataPipeline(...)
.get_enriched_data(end_date=as_of.isoformat(), interval="1d", lookback_days=200,
use_cache=False)` call quant/deep use, so the persisted snapshot references the
same numbers the recommendation carries.

### The G2 gate test and the recorded result

`tests/test_gate_g2.py` builds the enriched frame three independent ways — quant
`DataPipeline`, the deep adapter's pipeline build, and the Tracker
`MarketDataService` raw bars run through the one `TechnicalIndicators.add_all`
plus the shared dropna — and asserts `pandas.testing.assert_frame_equal` on the
canonical columns with no tolerances, then asserts
`compute_snapshot_id` is equal across all three lanes. It iterates only over
golden tickers that have a committed parquet fixture, so the asserted set matches
what was materialized.

`step-2-integration-results.md` records the live run against real Postgres +
Redis + yfinance: all 10 golden tickers show `frames_equal=yes`,
`G2_equal=yes`, 270 `price_bars` rows → 85 enriched rows each, with distinct
per-ticker snapshot ids, `as_of`=2024-01-15. The full integration suite is 17
passed / 0 failed, covering the gate, cold==DB==Redis snapshot-id equality on a
trading-day `as_of`, cache-hit-no-network, historical DB-authoritative reads,
idempotent news ingest, and idempotent snapshot build. This is the recorded
coder evidence the review relies on per the no-rerun instruction.

### Golden set change and evidence consistency

The design §1 and FEAT task text describe an all-`.BO` golden set. During the
real-data integration run six `.BO` symbols (RELIANCE, HDFCBANK, INFY, ICICIBANK,
CUMMINSIND, PERSISTENT) returned no yfinance data across retries while their
`.NS` equivalents and four live `.BO` symbols returned full 200-day windows. The
user chose Option A — switch the six dead symbols to `.NS`, keep four `.BO` — so
the set now spans both exchanges. This is recorded in `tests/golden/README.md`
(marked CONFIRMED), `step-2-integration-results.md`, and ADR 0007, and
`resolve_market_ticker` / `Config.resolve_ticker` are unchanged (selection only).
The 10 parquet fixtures are committed (`git ls-files` confirms) and the tree is
clean.

FEAT-005's `findings` were written before the live run and record G2 and the
parquet fixtures as PENDING (Docker unreachable in that environment). Two later
commits — `step-2: materialize 10 golden parquet fixtures ...` and `step-2:
record full 10-ticker G2 equality results (17 integration tests green)` — plus
the committed fixtures and the integration-results doc show the live run was
subsequently completed on a capable machine. The FEAT-005 record is stale against
the final committed state; the gate evidence itself is present and consistent.

### Scope and bundled changes

The diff is well-scoped to Step 2: the backend shell relocation (mechanical
`app.` → `backend.app.` rename, contract files byte-unchanged), the unified
service + repositories + models, Alembic 002 creating all three tables plus the
pgvector extension, the infra compose stack, the news ingest with its ToU doc,
the forward-compatible lane `snapshot_sink=None` seams (no Step-2 caller passes a
non-None sink), and the test suite. No unrelated changes are bundled in. The
`snapshot_sink` seam is a no-op in Step 2 and keeps the offline suite
byte-identical while setting up Step 3/4 wiring.

</details>

<details>
<summary>File map</summary>

- `quant/data/fetcher.py` — `fetch_historical` re-pointed to the unified service (the G2 seam); `fetch_latest` unchanged.
- `backend/app/services/data_service.py` — `MarketDataService` read path, window normalization, two-regime coverage, byte-faithful frames.
- `backend/app/services/snapshot_service.py` — `build_and_store_snapshot`, imports canonical `compute_snapshot_id`, idempotent insert.
- `backend/app/services/news_service.py`, `ticker_resolution.py` — RSS ingest + dedup hash; `.BO`/`.NS` resolution delegating to quant `Config`.
- `backend/app/repositories/price_bar_repository.py`, `news_repository.py` — async + sync upsert/read; exclusive-end bar predicate, inclusive news predicate.
- `backend/app/models/{price_bar,news_item,data_snapshot}.py` — `DOUBLE PRECISION` OHLCV, JSONB payload, content-hash PK.
- `backend/app/cache.py`, `database.py`, `config.py` — OHLCV cache keys + exact-float serialization; shared sync engine; new settings.
- `backend/alembic/versions/...002...` — one revision: pgvector extension + three tables.
- `infra/docker-compose.yml`, `infra/.env.example` — pgvector Postgres + Redis 7 (`name: fintech-infra`).
- `tests/test_gate_g2.py`, `test_data_service_integration.py`, `test_snapshot_integration.py`, `test_news_ingest_integration.py`, `test_golden_materialize.py`, `test_golden_loader.py`, `integration_helpers.py` — integration suite + G2 gate + golden loader.
- `tests/golden/*_1d.parquet` (10 files) — committed golden fixtures (6 `.NS` + 4 `.BO`).
- `docs/news-sources.md`, `docs/decisions/0007-g2-data-determinism.md`, `docs/status.md` — ToU doc, determinism ADR, status.

Full diff: `git diff 862304e..HEAD` (Step 2 commits `aaa1b03`..`1f5c1bc`).

</details>
