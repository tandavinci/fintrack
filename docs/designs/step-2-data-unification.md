# Step 2 — Data Unification: Requirements & Technical Design

Status: REVISED after design review r2 (design only; no code written). All
HIGH + MEDIUM findings from r1 AND r2 resolved; NITs addressed. Every
source-level claim verified against live code (see §12 Verification log); review
responses in §13 (r1) and §14 (r2).
Owner: solo developer.
Gate: G2 (unification-plan.md §Step 2).
Scope lock: this document locks the SCHEMA and BOUNDARIES for Step 2 before any
code is written. Technology stack and table shapes decided here are locked once
the design is approved.

Path conventions (from unification-plan.md):
- MONO = `c:\GIT\Fintech\repo` (the monorepo root; "repo root" below).
- SRC-T = `c:\GIT\Fintech\Tracker-Tool` (source BSE Tracker).
- SRC-Q source quant already migrated to `MONO/quant`.

---

## 1. Summary

Step 2 unifies data access across the three lanes (Tracker product shell,
`quant/` core, `deep/` adapter) so that for every golden ticker all three lanes
read **identical** OHLCV bars and indicators (Gate G2). It delivers:

- **FEAT A** — one unified data service wrapping yfinance with `.BO`/`.NS`
  resolution, a Redis read-cache, and a Postgres `price_bars` table, which the
  quant `DataPipeline` and the `deep` adapter both read through.
- **FEAT B** — a news/announcements ingest (RSS + BSE announcements) into one
  deduplicated `news_items` table.
- **FEAT C** — a persisted `data_snapshots` table keyed by the EXISTING
  `compute_snapshot_id`, holding the exact OHLCV+indicator tail, indicator
  summary, and headlines given to any engine, built by one
  `build_and_store_snapshot(ticker, as_of)` service.
- **INFRA** — `infra/docker-compose.yml` bringing up pgvector Postgres + Redis
  7, with Ollama staying on the host (decision 0002).
- **TEST STRATEGY** — real Postgres + Redis + real yfinance behind an
  `@pytest.mark.integration` marker, while the existing 39 offline tests stay
  green and fast with no infra.

This is a **prerequisite migration** step: the BSE Tracker backend shell is
first relocated into `MONO/backend/app/` without disturbing the three contract
files already living in `MONO/backend/app/schemas/`.

Hard constraints respected throughout (unification-plan §Cross-cutting, scope.md):
no paid APIs / no OpenAI keys; the gateway is the only path to Ollama; Ollama
stays on the host; `third_party/TradingAgents` is never edited; models explain,
code computes — the persisted snapshot is the source of truth for numbers; ONE
`Recommendation` contract and ONE `compute_snapshot_id`, imported never forked;
the Tracker keeps running (relocate working modules, do not rewrite them);
point-in-time snapshots; no worktree — work is directly in the monorepo.

---

## 2. Technology stack (LOCKED once approved)

| Concern | Choice | Why |
| --- | --- | --- |
| ORM | SQLAlchemy 2.x async (`Mapped`/`mapped_column`, `DeclarativeBase`) | matches SRC-T `app/database.py` and `app/models/*` verbatim (rule 5: relocate, do not rewrite) |
| DB driver (app) | `asyncpg` via `postgresql+asyncpg://` | SRC-T `DATABASE_URL` convention |
| DB driver (alembic/sync tooling) | `psycopg2-binary` | SRC-T dep; used by sync utilities/tests if needed |
| Migrations | Alembic, async `env.py` (unchanged from SRC-T) | SRC-T already async-migration based |
| Cache | Redis 7, `redis.asyncio` via `app/cache.py` helpers | SRC-T cache conventions |
| Price source | `yfinance==0.2.40` (pinned, already in repo) | slice pin; `.BO`/`.NS` support |
| News source | `feedparser` (RSS) + `bsedata` (BSE announcements, best-effort) | RSS is offline-testable; bsedata already a Tracker dep |
| Postgres image | `pgvector/pgvector:pg16` | scope.md pgvector requirement; swap noted in unification-plan 1.1 |
| Snapshot payload column | `JSONB` | queryable, native to Postgres; see §6.3 |
| Background jobs | Celery (queues `quant`/`deep` arrive in Steps 3/4) | SRC-T `celery_app.py`; Step 2 adds no new beat jobs |
| Timezone | `pytz`, `Asia/Kolkata` for market logic | SRC-T `celery_app.is_market_hours` |

`feedparser` is the one genuinely new runtime dependency introduced by Step 2
(it is not in either source `requirements.txt`). It is pinned in §4.2. BSE
announcements reuse the existing `bsedata` dep.

---

## 3. FEAT 0 (PREREQUISITE) — Relocate the Tracker backend shell into MONO

### 3.1 What exists today

`MONO/backend/` currently contains ONLY:

```
backend/__init__.py
backend/app/__init__.py
backend/app/schemas/__init__.py        (empty)
backend/app/schemas/recommendation.py  (THE contract)
backend/app/schemas/snapshot.py        (compute_snapshot_id, SNAPSHOT_TAIL_N=60)
backend/app/schemas/fusion.py          (fuse)
```

SRC-T `backend/app/` has the full product shell:
`{models,repositories,routers,schemas,services,tasks,utils}`, `cache.py`,
`celery_app.py`, `config.py`, `database.py`, `dependencies.py`, `main.py`,
`__init__.py`, plus `alembic/`, `alembic.ini`, `seed_data.py`, `tests/`.

### 3.2 Relocation strategy (rule 5: relocate, do not rewrite)

Copy the SRC-T `backend/` working tree into `MONO/backend/` under git, with
TWO carve-outs that MUST NOT be overwritten:

1. **`MONO/backend/app/schemas/`** — the three contract files
   (`recommendation.py`, `snapshot.py`, `fusion.py`) are the single source of
   truth (decision 0006). They are NOT present in SRC-T and must survive the
   copy. The copy of SRC-T's `schemas/` brings in the Tracker's own schema
   modules (`alert_schemas.py`, `portfolio_schemas.py`, `report_schemas.py`,
   `stock_schemas.py`, `user_schemas.py`). These coexist — different filenames,
   no collision — EXCEPT `schemas/__init__.py`, which collides (see §3.3).
2. **`MONO/backend/__init__.py` and `MONO/backend/app/__init__.py`** already
   exist (empty package markers). SRC-T's `app/__init__.py` is also effectively
   a package marker; keep whichever is non-empty, default to the existing MONO
   markers if both are empty.

Concretely, the migration copies every SRC-T `backend/app/*` subpackage and
top-level module (`cache.py`, `celery_app.py`, `config.py`, `database.py`,
`dependencies.py`, `main.py`) plus `alembic/`, `alembic.ini`, `seed_data.py`
into `MONO/backend/`, then performs the merge in §3.3. SRC-T stays untouched at
its original path as the running backup until G2 passes.

**Import-path note (decided):** SRC-T uses top-level `from app.xxx import ...`.
In MONO the package is `backend.app` (decision 0006 lists `backend` as a
setuptools package and the ONE import line is
`from backend.app.schemas.recommendation import Recommendation`). The relocated
Tracker modules therefore need their intra-package imports rewritten
`app.` → `backend.app.`. This touches EVERY `app.`-prefixed import in the
relocated shell AND in `alembic/env.py`, which (VERIFIED against SRC-T
`alembic/env.py`) imports `from app.config import settings`,
`from app.database import Base`, and `import app.models` — all three get the
`app.` → `backend.app.` rewrite, not just `import app.models`.
This is a mechanical rename, not a rewrite (rule 5 preserved: logic unchanged).

**Alembic invocation model (pinned — resolves MEDIUM-1).** VERIFIED against
SRC-T `alembic.ini`: it sets `script_location = alembic` and
`prepend_sys_path = .`, and Alembic resolves BOTH **relative to the current
working directory at invocation, not the ini file's location**. The §8.3 runbook
runs migrations FROM THE REPO ROOT (`alembic -c backend/alembic.ini upgrade
head`). For that CWD, `script_location = alembic` would resolve to
`MONO/alembic` (which does not exist — versions live at `MONO/backend/alembic`),
breaking the command. So the pinned model is:

- Run from the **repo root** (one invocation model, matching §8.3 step 3).
- Rewrite `alembic.ini` to `script_location = backend/alembic` (so the script
  directory resolves correctly from repo root).
- Keep `prepend_sys_path = .` — from repo root this puts the repo root on
  `sys.path`, which is exactly what makes `import backend.app.models` (and the
  other `backend.app.*` imports in `env.py`) resolve.

This is the single authoritative invocation; §8.3 step 3 is correct as written
against it. (The earlier "resolved relative to `MONO/backend`" guidance was
wrong for a run-from-root command and is replaced by the above.)
Chosen over the alternative of adding `backend/` to `sys.path` so that `app`
stays importable, because decision 0006 already locked the editable-install +
explicit-package model and a second import root would reintroduce the
`sys.path` hacks 0006 exists to avoid.

### 3.3 MERGE strategy for `backend/app/schemas/__init__.py`

This is the one true collision. The merged `__init__.py` must re-export BOTH:

- the three contract symbols (so `from backend.app.schemas import Recommendation`
  works and the single-home invariant holds), AND
- the Tracker's existing schema re-exports (so relocated Tracker routers/services
  that do `from app.schemas import AlertCreate` keep working after the
  `app.`→`backend.app.` rename).

VERIFIED against SRC-T (`c:\GIT\Fintech\Tracker-Tool\backend\app\schemas\__init__.py`):
the Tracker `__init__.py` re-exports ONLY from two modules — `alert_schemas`
(`AlertCreate, AlertListResponse, AlertResponse, AlertType, AlertUpdate,
TriggeredAlert`) and `report_schemas` (the `CapitalGainsEntry … UserEducationSummary`
set). The other three schema modules present on disk (`portfolio_schemas.py`,
`stock_schemas.py`, `user_schemas.py`) are NOT re-exported by SRC-T's `__init__`
today, so the merge copies exactly the alert + report blocks and appends the
contract block. The operative rule is "copy SRC-T's `__init__` verbatim, append
the contract"; if a future SRC-T revision adds more re-exports the merge picks
them up automatically, never a fixed hand-list.

Merged file shape (the contract block first, then the Tracker block verbatim
from SRC-T `schemas/__init__.py`, with one combined `__all__`):

```python
"""Pydantic schemas package.

Home of the ONE shared Recommendation contract (decision 0006) AND the
Tracker's product schemas. The contract symbols are re-exported here so the
single import home holds; the Tracker schemas are re-exported unchanged.
"""

# --- Shared contract (single source of truth; never fork) ---
from backend.app.schemas.recommendation import Action, Recommendation
from backend.app.schemas.snapshot import SNAPSHOT_TAIL_N, compute_snapshot_id
from backend.app.schemas.fusion import fuse

# --- Tracker product schemas (relocated verbatim from SRC-T) ---
from backend.app.schemas.alert_schemas import (
    AlertCreate, AlertListResponse, AlertResponse, AlertType, AlertUpdate,
    TriggeredAlert,
)
from backend.app.schemas.report_schemas import (
    CapitalGainsEntry, CapitalGainsParams, CapitalGainsReportResponse, DateRange,
    DifficultyLevel, EducationModuleListResponse, EducationModuleResponse,
    EducationProgressResponse, GainsCategory, GlossaryTerm, HistoryExportParams,
    HoldingReportEntry, InsightCompareRequest, InsightCompareResponse,
    InsightScoreResponse, InsightSignal, PortfolioReportResponse, ReportFormat,
    ReportParams, ReportResponse, ReportStatus, TopOpportunityResponse,
    UserEducationSummary,
)
# (SRC-T's __init__ re-exports ONLY alert_schemas + report_schemas today —
#  verified. Step-2 copies SRC-T's list verbatim and appends the contract
#  symbols; no Tracker export is dropped. If SRC-T later re-exports
#  portfolio/stock/user schemas, they are carried over automatically.)

__all__ = [
    # --- contract (prepended) ---
    "Action", "Recommendation", "SNAPSHOT_TAIL_N", "compute_snapshot_id", "fuse",
    # --- SRC-T __all__ copied VERBATIM below this line (29 names, SRC-T's own
    #     order & comment grouping; nothing reordered within a group) ---
    # Alert schemas
    "AlertCreate",
    "AlertListResponse",
    "AlertResponse",
    "AlertType",
    "AlertUpdate",
    "TriggeredAlert",
    # Insight schemas
    "InsightCompareRequest",
    "InsightCompareResponse",
    "InsightScoreResponse",
    "InsightSignal",
    "TopOpportunityResponse",
    # Education schemas
    "DifficultyLevel",
    "EducationModuleListResponse",
    "EducationModuleResponse",
    "EducationProgressResponse",
    "GlossaryTerm",
    "UserEducationSummary",
    # Report schemas
    "CapitalGainsEntry",
    "CapitalGainsParams",
    "CapitalGainsReportResponse",
    "DateRange",
    "GainsCategory",
    "HistoryExportParams",
    "HoldingReportEntry",
    "PortfolioReportResponse",
    "ReportFormat",
    "ReportParams",
    "ReportResponse",
    "ReportStatus",
]
```

**Merge specified mechanically (resolves MEDIUM-3/MEDIUM-A/MEDIUM-B), not as a
hand-list.** The merged `__all__` is the five contract names
(`Action`, `Recommendation`, `SNAPSHOT_TAIL_N`, `compute_snapshot_id`, `fuse`)
**prepended** to SRC-T's `__all__` **copied verbatim** — the 29 Tracker names
above are reproduced in SRC-T's exact order under SRC-T's own four comment
headers (Alert / Insight / Education / Report), VERIFIED line-for-line against
`Tracker-Tool/backend/app/schemas/__init__.py`. (Earlier drafts said "30 names"
and re-sorted the Report group; both were wrong — SRC-T's `__all__` is **29
names** [6 Alert + 5 Insight + 6 Education + 12 Report], and this block now
matches its order exactly. The merged length is therefore `29 + 5 = 34`.) NO
SRC-T entry is removed, renamed, or reordered; the contract import block is
likewise ADDITIVE and comes first; the Tracker import block is copied verbatim
and never trimmed.

**What the gate actually enforces (authoritative over the count).** The CI/review
gate is expressed ONLY via the dynamic SRC-T length + a set union, so the prose
count can never desync it again:

```python
contract = {"Action", "Recommendation", "SNAPSHOT_TAIL_N", "compute_snapshot_id", "fuse"}
assert set(merged.__all__) == set(srct.__all__) | contract
assert len(merged.__all__) == len(srct.__all__) + 5   # today: 29 + 5 == 34
```

The gate checks the SET union and the dynamic `len(srct.__all__) + 5` — it does
NOT hard-code 34 (or 35). Set-equality + the exact `+5` length together catch
both a dropped Tracker name and an accidental duplicate. Ordering is NOT
significant to the gate; the verbatim block above is for human readability and to
honor "reorder-nothing," but correctness rests on the set/length assertion. If a
future SRC-T revision adds re-exports, re-run the merge rule — the imports and
`__all__` pick them up by construction and the dynamic gate re-counts
automatically. If a later Tracker schema name ever collided with a contract name
(none do today — the five contract names are unique), the contract name wins and
the collision is a hard review stop.

### 3.4 `requirements.txt` merge (keep the slice's exact pins)

The repo-root `requirements.txt` (the slice) currently pins exactly (VERIFIED
verbatim against the file):

```
fastapi==0.115.* uvicorn==0.30.* pydantic>=2,<3 pydantic-settings==2.*
httpx==0.27.* jsonschema==4.* pandas==2.2.2 numpy==1.26.4 ta==0.11.0
yfinance==0.2.40 pytest==8.* pytest-asyncio==0.23.*
```

SRC-T `backend/requirements.txt` is unpinned (VERIFIED) and lists:
`fastapi uvicorn sqlalchemy alembic asyncpg psycopg2-binary pydantic
pydantic-settings email-validator python-jose[cryptography] passlib[bcrypt]
celery redis httpx pytrends bsedata reportlab hypothesis pytest pytest-asyncio
factory-boy pytz`. The names `fastapi uvicorn pydantic pydantic-settings httpx
pytest pytest-asyncio` OVERLAP the slice and keep the slice pins unchanged;
`sqlalchemy alembic asyncpg psycopg2-binary email-validator
python-jose[cryptography] passlib[bcrypt] celery redis pytrends bsedata reportlab`
are Tracker-only runtime names; `hypothesis factory-boy` are Tracker-only test
names. (`feedparser` is in NEITHER file — it is the one net-new Step-2 dep, A9.)

**Merge rule (decided):** the slice's exact pins are authoritative for every
overlapping name. For Tracker-only names, add them pinned to a known-good
version (not an open range — content_safety: pin dependencies). The task listed
these Tracker deps to merge: `sqlalchemy, alembic, asyncpg, psycopg2-binary,
celery, redis, httpx, pytrends, bsedata, pytz`. `httpx` OVERLAPS the slice and
keeps the slice pin `httpx==0.27.*` (do NOT re-add).

The four deps NOT in the task's explicit list (`email-validator`,
`python-jose[cryptography]`, `passlib[bcrypt]`, `reportlab`) are added only if
they are actually imported by a module that the relocated shell loads in Step 2.
Because the relocation copies the WHOLE SRC-T `backend/app/` tree (auth routers,
report service, `main.py` wiring them in), these are imported at shell import
time and are therefore required (auth: `python-jose`, `passlib`;
Pydantic `EmailStr`: `email-validator`; PDF reports: `reportlab`). **Implementer
gate (resolves NIT-2):** before adding each of the four, confirm with a grep that
it is imported by a module reachable from the relocated `backend.app.main` import
graph (e.g. `python-jose`/`passlib` in `app/utils` or `app/dependencies`,
`reportlab` in the report service, `email-validator` via a Pydantic `EmailStr`
field). Any of the four NOT reachable from the Step-2 import graph is DEFERRED to
the step that wires its router, not added speculatively. The import graph is the
authority, not this list. Test-only Tracker deps (`hypothesis`, `factory-boy`)
are added pinned in the test group.

Resulting `requirements.txt` (pins to be resolved against the venv at
implementation time; shown with representative compatible pins):

```
# --- slice pins (AUTHORITATIVE; unchanged) ---
# INVARIANT: no overlapping name below changes a slice pin. The Tracker-shell
# versions further down are REPRESENTATIVE and are resolved against MONO/.venv
# at build time; only the slice pins in this top block are fixed by this design.
fastapi==0.115.*
uvicorn==0.30.*
pydantic>=2,<3
pydantic-settings==2.*
httpx==0.27.*
jsonschema==4.*
pandas==2.2.2
numpy==1.26.4
ta==0.11.0
yfinance==0.2.40
pytest==8.*
pytest-asyncio==0.23.*
# --- Tracker shell deps (Step 2; pinned, no overlap with the slice) ---
sqlalchemy==2.0.*
alembic==1.13.*
asyncpg==0.29.*
psycopg2-binary==2.9.*
celery==5.3.*
redis==5.0.*
pytrends==4.9.*      # name verified against SRC-T requirements.txt
bsedata==1.0.*
pytz==2024.*
email-validator==2.*
python-jose[cryptography]==3.3.*
passlib[bcrypt]==1.7.*
reportlab==4.*
feedparser==6.0.*    # NEW in Step 2 (RSS ingest, FEAT B)
# --- test-only ---
hypothesis==6.*
factory-boy==3.3.*
```

The exact patch pins are confirmed at implementation time by installing into
`MONO/.venv` and running the full suite; the invariant the reviewer enforces is:
**no overlapping name changes the slice's pin**, and every Tracker import
resolves. `pydantic>=2,<3` already satisfies SRC-T (it used unpinned
`pydantic`); SQLAlchemy 2.x is required for the `Mapped[...]` style the SRC-T
models already use.

### 3.5 `pyproject.toml` `[tool.setuptools]` changes

Current:

```toml
[tool.setuptools]
packages = ["backend", "quant", "deep", "gateway"]
```

The explicit list stops setuptools from scanning `third_party/` and `frontend/`
(decision 0006). With `packages = ["backend", ...]`, setuptools includes
`backend` and, by default `find`-style inclusion, its subpackages are NOT
auto-added when a hand-written list is used. To keep
`from backend.app.schemas.recommendation import Recommendation` AND the new
`from backend.app.models.price_bar import PriceBar`,
`from backend.app.services.data_service import ...` working, the package list
must enumerate the new subpackages OR switch to `find`:

**Decision:** switch to automatic discovery scoped to the four roots, which is
the lowest-maintenance option and avoids listing every new subpackage by hand:

```toml
[tool.setuptools.packages.find]
where = ["."]
include = ["backend*", "quant*", "deep*", "gateway*"]
exclude = ["third_party*", "frontend*", "tests*"]
```

`include` globs (`backend*`) pull in `backend`, `backend.app`,
`backend.app.schemas`, `backend.app.models`, `backend.app.services`, etc.
automatically; `exclude` keeps the read-only submodule and the JS frontend out.
This is chosen over hand-listing (`packages = ["backend", "backend.app",
"backend.app.models", ...]`) because the Tracker shell has ~8 subpackages and
Step 2 adds more; an explicit list would need editing on every new package and
is exactly the kind of drift decision 0006 warns about. `pip install -e .` is
re-run once after this change (decision 0006 notes re-install is only needed
when the package list changes).

Every Tracker subpackage must contain an `__init__.py` (SRC-T already has them);
the relocation preserves them so discovery finds the packages.

---

## 4. FEAT A — The single unified data service

### 4.1 Module location & name (Tracker service conventions)

Following SRC-T `app/services/*_service.py` + `app/repositories/*_repository.py`
+ `app/models/*.py` layering:

| Layer | New file | Responsibility |
| --- | --- | --- |
| Model | `backend/app/models/price_bar.py` | `PriceBar` ORM model (§5) |
| Repository | `backend/app/repositories/price_bar_repository.py` | async DB upsert + point-in-time read of `price_bars` |
| Service | `backend/app/services/data_service.py` | the public `MarketDataService`: read path cache→DB→yfinance, `.BO`/`.NS` resolution, upsert |
| Cache helpers | extend `backend/app/cache.py` | new key prefixes + TTLs for OHLCV ranges |
| Ticker resolution | `backend/app/services/ticker_resolution.py` | mirror of quant `Config.resolve_ticker` (§4.3) |

`MarketDataService` is the ONE public surface. It is an async class (the Tracker
app is async end-to-end); a thin **synchronous facade** is provided for the
in-process quant/deep callers (§4.6), because `quant/` and `deep/` are plain
synchronous callables (the deep adapter deliberately imports no async web stack).

### 4.2 Public interface

```python
# backend/app/services/data_service.py  (signatures; async core)
class MarketDataService:
    async def get_ohlcv(
        self,
        ticker: str,              # human ticker, e.g. "RELIANCE" or "RELIANCE.BO"
        interval: str,            # "1d", "30m", "5m", ... (quant Config intervals)
        start: date | None = None,
        end: date | None = None,  # EXCLUSIVE upper bound = as_of (point-in-time, §4.4c)
        lookback_days: int | None = None,
        asset_class: str = "stock",
    ) -> pd.DataFrame: ...
        # Return shape/columns/index/dtype are defined NORMATIVELY by the
        # §4.6 post-processing block (the single source for the return
        # contract): a tz-naive DatetimeIndex named "Datetime", columns exactly
        # ["Open","High","Low","Close","Volume"] as float64, IDENTICAL in
        # dtype/shape/order to quant DataFetcher output (G2). Float values are
        # the canonical frame per §4.4b (byte-faithful on every path).
```

The exact column set, index name, and dtypes are specified once in §4.6 ("OHLCV
post-processing"); §4.2 and §4.5 reference that block rather than restating it,
so there is a single normative source for the service return shape (NIT-5).

**Window boundary & normalization (NORMATIVE — single source for HIGH-A/HIGH-B).**
`start`/`end`/`lookback_days` are all optional on the public signature, but the
G2 live path never passes a literal `start` (see §4.4c). To guarantee that every
caller reaching the service by any seam, with any of the `(start, end,
lookback_days)` combinations, produces the SAME cache key, the SAME DB predicate,
and the SAME yfinance fetch shape, `get_ohlcv` runs ONE normalization step at the
very top — specified once in §4.4c and consumed unchanged by the Redis key
(§4.5), the DB read (§4.4/§5.3), and the yfinance leg (§4.6). `end` is an
**EXCLUSIVE** upper bound on every path (matching yfinance's exclusive `end`);
that convention is pinned in §4.4c and referenced, never redefined, elsewhere.

A synchronous facade `get_ohlcv_sync(...)` (same signature) wraps the async
method for quant/deep (§4.6).

The service returns RAW OHLCV only. Indicator enrichment stays in
`quant.TechnicalIndicators` (unchanged — Gate G2 requires one indicator code
path). The service never computes indicators.

### 4.3 `.BO`/`.NS` resolution (reuse/mirror `Config.resolve_ticker`)

quant `Config.resolve_ticker(ticker, asset_class)` handles forex (`=X`),
commodity (`=F`), and stock (returns as-is). It does NOT add `.BO`/`.NS` — for
Indian equities the caller passes the suffix explicitly (golden set is all
`.BO`). Step 2 needs explicit BSE/NSE handling.

**Decision:** add BSE/NSE resolution in `backend/app/services/ticker_resolution.py`
as a thin wrapper that DELEGATES to `quant.config.Config.resolve_ticker` for the
forex/commodity/stock cases and layers Indian-equity rules on top:

```python
def resolve_market_ticker(ticker: str, asset_class: str = "stock") -> str:
    t = ticker.upper().strip()
    if asset_class == "stock" and not t.endswith((".BO", ".NS")):
        # Bare Indian symbol -> default to BSE (.BO); golden set is .BO.
        # A symbol already carrying .BO/.NS passes through unchanged.
        # US tickers (optional, scope.md) are passed bare to yfinance.
        # Resolution does NOT guess .BO for known-US tickers; see A3.
        ...
    return Config.resolve_ticker(t, asset_class)
```

Chosen over copying `resolve_ticker` into `backend/` (would fork logic — forbidden
spirit of rule 3/decision 0006) and over editing quant's `Config` to add `.BO`
logic (would entangle the quant engine with BSE product rules). The wrapper
imports quant's `Config` (allowed — `quant` is an installed package). The exact
default-suffix policy (`.BO` vs explicit-required) is flagged in §11 A3 because
it affects which bars land in the DB.

**Invariant ownership:** the resolved yfinance symbol (`RELIANCE.BO`) is the
identity used for the cache key, the `price_bars.ticker` column, and the DB
storage key. Resolution happens at the service boundary; downstream storage
layers receive the already-resolved symbol.

**Double-resolution is idempotent (VERIFIED against `quant/data/fetcher.py`).**
`DataFetcher.__init__` ALREADY calls `Config.resolve_ticker(raw_ticker,
asset_class)` and stores the result as `self.ticker`; when the §4.6 seam
re-points `fetch_historical` to `MarketDataService.get_ohlcv_sync`, the service
will run `resolve_market_ticker` on a symbol that may already be resolved. This
is safe because (a) `Config.resolve_ticker` on an already-suffixed forex/
commodity symbol (`EURUSD=X`, `GC=F`) returns it unchanged, and (b)
`resolve_market_ticker` passes a symbol already ending in `.BO`/`.NS` through
untouched. So `resolve(resolve(x)) == resolve(x)` for every supported case and
the service never double-suffixes. The seam passes `DataFetcher.display_ticker`
(the human symbol) — NOT the pre-resolved `self.ticker` — into the service so
resolution policy stays owned in ONE place; see §4.6.

**Snapshot-id ticker arg caveat (IMPORTANT, G2):** `compute_snapshot_id(ticker,
as_of, df)` is called by `quant/recommend.py` and `deep/adapter.py` with the
ticker string THE CALLER passed to `recommend()`/`analyze()` (verified: both
pass their own `ticker` arg, not a resolved symbol). The snapshot id therefore
keys on the caller's ticker string, while `price_bars.ticker` and the cache key
use the resolved symbol. These are DIFFERENT strings when the caller passes a
bare symbol. For the golden set this is a non-issue because the golden tickers
are already `.BO` (so caller string == resolved symbol), but the invariant to
state plainly is: **G2 identity (identical `compute_snapshot_id` across lanes)
holds because all three lanes pass the SAME caller ticker string into
`compute_snapshot_id` and read the SAME enriched df — it does NOT depend on the
snapshot-id ticker matching the DB/cache resolved symbol.** Step 2 does NOT
change `compute_snapshot_id`'s inputs in either lane (rule: never fork the
canonical id). If a future step wants the snapshot id to key on the resolved
symbol, that is a contract change gated separately, not a Step-2 change.

### 4.4 Read path: service → Redis → Postgres → yfinance

```
get_ohlcv(ticker, interval, start, end, lookback_days)
   resolve ticker  ->  resolved symbol
   0. NORMALIZE the window (§4.4c): (start, end) become concrete dates;
      `end` is EXCLUSIVE; the key/DB/yfinance legs below all consume these.
   1. Redis GET  ohlcv:{symbol}:{interval}:{start_norm}:{end_norm}   (short-circuit)
        hit  -> deserialize to the EXACT float frame that was cached, return
   2. Postgres SELECT from price_bars
        WHERE ticker=symbol AND interval=interval
          AND ts >= start_norm AND ts < end_norm        -- EXCLUSIVE end (§4.4c)
        if the stored range is authoritative for this request
        (§4.4a) -> build DataFrame, warm Redis, return
   3. yfinance fetch (DataFetcher-equivalent path, §4.6)
        -> UPSERT rows into price_bars (point-in-time, §5.3)
        -> warm Redis
        -> return
```

#### 4.4c Window normalization & the exclusive-`end` convention (resolves HIGH-A + HIGH-B)

The G2 live path is specific and VERIFIED: both `quant/recommend.py` and
`deep/adapter.py` call `DataPipeline(...).get_enriched_data(end_date=as_of.isoformat(),
interval="1d", lookback_days=200, use_cache=False)`, and quant `DataFetcher.fetch_historical`
then derives `start_date = end_date - timedelta(days=lookback_days)` INTERNALLY
because `start_date is None` (VERIFIED in `quant/data/fetcher.py`). So on the G2
path `start` is always `None`, `end`=`as_of`, and `lookback_days=200` drives the
window. Every concrete mechanism in §4.4/§4.4a/§4.5/§5.3, however, needs a
concrete `start` and a pinned `end` semantics. The service therefore runs ONE
normalization at the top of `get_ohlcv`, and the Redis key, the DB predicate, and
the yfinance leg all consume its outputs — never the literal `None`:

```python
# backend/app/services/data_service.py — normalize BEFORE keying/caching/fetching
from quant.config import Config

if end is None:
    end = date.today()
if Config.is_intraday(interval) and lookback_days and lookback_days > Config.INTRADAY_MAX_LOOKBACK_DAYS:
    lookback_days = Config.INTRADAY_MAX_LOOKBACK_DAYS   # mirror DataFetcher clamp (60)
if start is None:
    start = end - timedelta(days=(lookback_days or Config.DEFAULT_LOOKBACK_DAYS))
# From here on start/end are CONCRETE dates. end is an EXCLUSIVE upper bound.
```

This mirrors `DataFetcher.fetch_historical` EXACTLY (same clamp order: clamp
`lookback_days` first, then default `end`, then derive `start`; same
`Config.DEFAULT_LOOKBACK_DAYS=120` fallback; same `INTRADAY_MAX_LOOKBACK_DAYS=60`
clamp — all VERIFIED against `fetcher.py` / `config.py`), so the derived `start`
is bit-identical to what the quant fetcher would have computed.

**Exclusive `end` is the ONE boundary convention (HIGH-A, chosen Option 1).**
VERIFIED in `quant/data/fetcher.py`: the daily leg calls
`stock.history(start=start_date, end=end_date, interval=interval)`, and yfinance
treats `end` as an **exclusive** upper bound — the bar dated exactly `end`/`as_of`
is NOT returned. The service must reproduce `DataFetcher` byte-for-byte (§4.6),
so `end` is exclusive on the cold-fetch leg by construction. To keep the
DB-served and Redis-served frames carrying the SAME rows (and therefore the SAME
`SNAPSHOT_TAIL_N` tail and the SAME `compute_snapshot_id`), the DB predicate and
the cache semantics are ALSO exclusive on `end`:

- DB read predicate: `ts >= start AND ts < end` (NOT `BETWEEN`, NOT `ts <= end`).
- Point-in-time read at `as_of`: `ts < as_of` (NOT `ts <= as_of`); see §5.3.
- The cache key uses the normalized `start`/`end`, so the stored frame already
  excludes any `ts == end` bar — a DB-served or Redis-served read returns the
  identical row set a cold yfinance fetch would.

Chosen over Option 2 (pass `end = as_of + 1 day` to yfinance and keep inclusive
`ts <= end` everywhere): Option 2 would change the bars `DataFetcher` returns
today and therefore change EVERY existing `snapshot_id` — forbidden by "relocate,
do not rewrite / never perturb the canonical id." Option 1 leaves the live fetch
shape and every existing snapshot id untouched and only makes the storage layers
agree with yfinance's pre-existing exclusive-`end` behavior. The daily golden set
(all `.BO`, `as_of` frequently a weekend/holiday with no bar at `as_of`) hides
the bug today; §9.2 test #2a is tightened to use a trading-day `as_of` so the
boundary is actually exercised.

**Key/coverage/fetch all consume the normalized window.** The Redis key is
`ohlcv:{symbol}:{interval}:{start_norm}:{end_norm}` (§4.5), the DB coverage
window (§4.4a) is `[start_norm, end_norm)`, and the yfinance leg (§4.6)
reproduces `DataFetcher`'s period-vs-start/end branch using the SAME normalized
values. Consequence (VERIFIED requirement): two callers at the same
`(as_of, lookback_days, interval)` reaching the service via different seams
derive the identical normalized `(start, end)` and therefore collide on ONE cache
key and read the SAME DB range — which is exactly what §9.2 test #2 ("second
fetch served from cache") and the new no-infra key-parity unit test (§9.4) assert.

#### 4.4a Coverage decision (bounded; historical reads are DB-authoritative)

"The stored range is authoritative" is decided in two regimes, so that a
point-in-time historical read never triggers a spurious refetch that could
restate already-stored bars (MEDIUM-4):

- **Historical window** — if `end < today - FRESH_WINDOW_DAYS` (constant,
  default `FRESH_WINDOW_DAYS = 2` trading days) AND at least one row exists in
  `[start, end)` (half-open, exclusive `end` per §4.4c) for `(symbol, interval)`,
  the DB is treated as AUTHORITATIVE:
  the service builds the frame from the stored rows and does NOT refetch. Past
  windows are immutable, so a single stored row is enough to declare the range
  served; the coverage ratio is NOT applied here. This keeps a historical
  `snapshot_id` byte-stable across runs — a refetch at a past `as_of` would
  re-pull *current* yfinance data and, under the §5.3 overwrite-on-upsert rule,
  could mutate stored bars and change a historical `snapshot_id`; bounding the
  heuristic to the recent window prevents that.
- **Recent window** — if `end >= today - FRESH_WINDOW_DAYS`, the service uses
  the **expected-row-count** ratio (not a holiday calendar, to stay
  offline-deterministic): it compares the stored row count in `[start, end)`
  (half-open, exclusive `end` per §4.4c) against the business-day count times
  `DB_COVERAGE_MIN_RATIO` (default `0.90`,
  a tunable constant). Below the floor → treat as a miss and refetch. In the
  recent window a refetch is both desired (yfinance restates recent bars) and
  harmless to historical determinism, because historical `as_of` values never
  reach this branch. A worst-case mis-judgement here is an idempotent refetch of
  recent bars only (§11 A4).

`FRESH_WINDOW_DAYS` and `DB_COVERAGE_MIN_RATIO` are documented constants on the
service. Both regimes are unit-testable with a stubbed repository (no infra):
feed a fake row count + an `end` date and assert refetch vs served.

#### 4.4b The returned OHLCV frame is ONE canonical float frame on every path (G2, resolves HIGH-1)

G2 requires that cold-fetch, DB-served, and Redis-served reads for the same
`(symbol, interval, start, end)` produce a frame that feeds `compute_snapshot_id`
IDENTICALLY — otherwise two lanes reading at different times (one fresh, one from
a warm cache the other missed) could compute different `snapshot_id`s for the
same `(ticker, as_of)`, which G2 forbids. `compute_snapshot_id` renders OHLCV
cells via `float(value)` at 6 dp (VERIFIED, V1), so the frame's float values must
be identical, not merely "close," on every path.

**Decision (chosen: byte-faithful full-precision storage — review Option A):**
the OHLCV values the service returns are ALWAYS the yfinance/pandas `float64`
values, and the DB and Redis layers are byte-faithful caches of exactly those
floats — never a reduced-precision round-trip.

- **Storage precision:** `price_bars` OHLCV columns are
  `DOUBLE PRECISION` (SQLAlchemy `Float(precision=53)` / `sa.Float`), NOT
  `Numeric(18,4)`. Postgres `double precision` is IEEE-754 binary64, the same
  representation as a pandas `float64` cell, so a value written and re-read is
  bit-identical. `volume` stays `BigInteger`. (This supersedes the earlier
  `Numeric(18,4)` choice; see §5.1.)
- **DB-served frames:** when §4.4a serves from the DB, the service builds the
  frame from the stored `double precision` values with `dtype=float64`, so the
  resulting cells equal the originally-fetched floats bit-for-bit.
- **Redis-served frames:** serialized as the exact `float64` repr (§4.5), so a
  cache hit yields the same floats.
- **Cold-fetch frames:** the yfinance/pandas `float64` frame as-is.

**`volume` byte-faithfulness (NIT-2).** `DataFetcher` carries `Volume` as part of
the `float64` OHLCV frame, but `compute_snapshot_id` renders it via `float(value)`
at 6 dp like every other canonical column (VERIFIED, V1). In practice yfinance
daily/intraday volume is INTEGRAL, so storing it as `BigInteger` and reading it
back yields e.g. `1000000` → `float64 1000000.0`, which renders IDENTICALLY to
the fetched `1000000.0` — the integer column is byte-faithful for the hash
precisely because volume is integral. **Guard note:** if a future source ever
returns FRACTIONAL volume (some split/dividend-adjusted feeds do), the
`BigInteger` column would truncate the fractional part and perturb the hash; at
that point volume must widen to `DOUBLE PRECISION` like the OHLC columns. This is
flagged in §11 A5 and is not a Step-2 concern because every configured source
(yfinance `.BO`/`.NS`) returns integral volume.

Therefore the frame fed to `compute_snapshot_id` is the SAME float frame on cold,
DB, and Redis paths, and A5's "snapshot computed from the float frame" claim holds
for EVERY path, not just the cold fetch. The §9.2 integration suite adds an
explicit assertion (test #2a): `compute_snapshot_id` of the cold-fetch frame ==
the DB-served frame == the Redis-served frame for the same `(ticker, as_of)`.

Rejected alternative (review Option B — keep `Numeric(18,4)` and round every lane
to 4 dp before hashing): rejected because it changes the snapshot *numbers* for
all existing callers (4 dp instead of the current full-precision 6-dp render) and
would require touching the canonical `compute_snapshot_id` inputs — forbidden
(never fork / never alter the canonical id). Option A keeps the canonical render
untouched and achieves exactness by making storage lossless.

### 4.5 Redis cache key shape & TTL policy

Extend `backend/app/cache.py` with Step-2 constants (mirrors the existing
`PREFIX_*` + `*_TTL` style):

```python
PREFIX_OHLCV = "ohlcv:"          # ohlcv:{symbol}:{interval}:{start}:{end}
OHLCV_INTRADAY_TTL = 300         # 5 min  — intraday bars churn intra-session
OHLCV_DAILY_TTL = 86400          # 24 h   — daily bars are stable once closed
```

Key shape: `ohlcv:{resolved_symbol}:{interval}:{start_norm_iso}:{end_norm_iso}`
(the NORMALIZED window from §4.4c — never the literal `None`). The value is the
serialized OHLCV frame as **JSON records with an ISO timestamp column and OHLCV
as JSON numbers that are the EXACT IEEE-754 doubles of the frame's `float64`
cells**.

**Exact serialization mechanism (NIT-1).** The frame cells are numpy `float64`
scalars, which `json.dumps` CANNOT serialize directly (`TypeError: Object of type
float64 is not JSON serializable`). The serializer therefore casts each cell
`np.float64 -> Python float` first — via `df.astype(object)` / `.to_dict()` /
`.tolist()`, or an equivalent per-cell `float(x)` — which is LOSSLESS because
both `np.float64` and Python `float` are IEEE-754 binary64. `json.dumps` then
emits the shortest round-tripping decimal and `json.loads` reconstructs the
identical double (`float(repr(x)) == x`), so the deserialized frame is
bit-identical to the frame that was cached. No `round()` / reduced-precision
step is ever reached; a lossy round would break §4.4b and is explicitly
forbidden here. This is decided over pickle (unsafe) and over Parquet-in-Redis
(extra dependency, no precision benefit here). The byte-faithful requirement is
the G2 cornerstone: see §4.4b. TTL:

- intraday intervals (`Config.is_intraday(interval)` → `1m..1h`): `OHLCV_INTRADAY_TTL`.
- daily (`1d`): `OHLCV_DAILY_TTL`.

Point-in-time requests whose `end < today` are historical and immutable; the
service MAY set a long TTL for them, but the default policy above is sufficient
and simplest. The DB is the durable store; Redis is only a hot-read accelerator,
so a cache miss is never a correctness problem.

### 4.6 How quant `DataPipeline` and `deep` read THROUGH the service (Gate G2)

This is the crux of G2: all three lanes must see identical bars.

`quant.DataPipeline` today calls `self.fetcher.fetch_historical(...)` directly
(quant `DataFetcher` → yfinance). The change:

- `DataFetcher.fetch_historical` is re-pointed to call the unified service's
  **synchronous facade** `MarketDataService.get_ohlcv_sync(...)` instead of
  calling `yf.Ticker(...).history(...)` directly. `fetch_historical` keeps its
  EXACT signature `(start_date, end_date, lookback_days, interval)` and its EXACT
  return contract (OHLCV-only `float64`, tz-naive `Datetime`-named index), so
  `DataPipeline.get_enriched_data` and `TechnicalIndicators.add_all` are
  **unchanged**. This is the minimal seam that routes quant through the shared
  service without touching indicator code.
- **`fetch_latest` is NOT re-pointed in Step 2.** VERIFIED against
  `quant/data/fetcher.py`: `fetch_latest(self, period=None, interval=None)` is
  PERIOD-based (`stock.history(period=..., interval=...)`) with distinct defaults
  (`"60d"` intraday / `"3mo"` daily) and has no `start`/`end`/`lookback_days`,
  so it cannot route through `get_ohlcv(ticker, interval, start, end,
  lookback_days, asset_class)` without a `period`→(start,end) translation that
  does not exist. Crucially, `fetch_latest` is OFF the G2 path: it is reached
  only via `DataPipeline.get_latest_enriched`, whereas both `quant.recommend` and
  `deep.analyze` use `get_enriched_data(..., lookback_days=200, interval="1d")`
  → `fetch_historical` (VERIFIED, V5/V6). Step 2 therefore leaves `fetch_latest`
  calling yfinance directly, unchanged. Routing `fetch_latest` through the
  service (adding a `period: str | None` param to `get_ohlcv` with the explicit
  `"60d"`/`"3mo"` mapping) is deferred to a later step and is NOT a Step-2
  deliverable; this keeps the "EXACT signature" claim true for the one method the
  seam actually touches. Because `fetch_latest` is off the G2 golden path, this
  does not weaken G2.
  **Ticker passed to the service:** `DataFetcher` passes `self.display_ticker`
  (the human symbol it captured in `__init__`) plus its `asset_class`, and lets
  the service own resolution via `resolve_market_ticker`. Because resolution is
  idempotent (§4.3), passing the already-resolved `self.ticker` instead would
  yield the same symbol; the design standardizes on `display_ticker` so there is
  exactly ONE resolution authority (the service) and `DataFetcher.__init__`'s
  own `Config.resolve_ticker` call becomes a harmless pre-pass. `DataFetcher`'s
  public attributes (`self.ticker`, `self.display_ticker`) are unchanged — only
  the body of `fetch_historical` is re-pointed (`fetch_latest` untouched, see
  above), preserving rule 5.
- `deep/adapter.py` already builds its snapshot via the SAME
  `quant.DataPipeline` (see its docstring and `analyze()`), so once
  `DataFetcher` reads through the service, deep automatically reads the same bars.
  No change to `deep/adapter.py`'s fetch path is required for G2.
- The Tracker product path (e.g. a future price-history view) calls the async
  `MarketDataService.get_ohlcv` directly.

**Decision — where the seam lives:** re-point `quant.data.fetcher.DataFetcher`
rather than re-pointing `DataPipeline`. Chosen because `DataFetcher` is the
single yfinance touchpoint in quant; re-pointing it means pipeline, backtests,
and deep all inherit the shared service with one edit, and the indicator /
dropna logic in `DataPipeline` stays byte-for-byte identical (G2 depends on that
identity). The alternative (new fetch call inside `DataPipeline`) would leave
`DataFetcher` as a second, divergent yfinance path — exactly the duplication G2
forbids.

**Preserving the daily `lookback_days>=200` behavior:** both `quant.recommend`
and `deep.analyze` already call `DataPipeline(...).get_enriched_data(
end_date=as_of.isoformat(), interval="1d", lookback_days=200, use_cache=False)`
(VERIFIED verbatim in `quant/recommend.py` and `deep/adapter.py`). The dropna
columns are NOT hard-coded: `DataPipeline.get_enriched_data` computes
`long_sma_col = f"SMA_{Config.get_indicator_periods(interval)['sma_long']}"` and
does `dropna(subset=[long_sma_col, "RSI"])` (verified). For `interval="1d"`,
`get_indicator_periods("1d")['sma_long'] == 50`, so the daily path drops on
`["SMA_50", "RSI"]`. The service MUST honor a 200-calendar-day daily fetch so
that after `TechnicalIndicators.add_all` + that dropna at least
`SNAPSHOT_TAIL_N=60` warmed bars survive (SMA_50 needs 50 warmup rows; ~200
calendar days ≈ ~135 trading days, leaving ≳60 after dropna). The service does
NOT clamp daily lookback (only intraday is clamped to 60, matching quant
`Config.INTRADAY_MAX_LOOKBACK_DAYS=60`, verified). A unit/integration assertion
(§9) pins "≥60 warmed daily bars survive for every golden ticker" so a
regression here fails loudly.

**Intraday fetch-shape parity (VERIFIED, must be preserved by the service).**
`DataFetcher.fetch_historical` does NOT call `yf...history(start=,end=)` for
intraday; it uses a period-based call `stock.history(period=f"{lookback_days}d",
interval=interval)` for intraday intervals and `start/end` only for daily
(verified). The unified service's yfinance leg MUST reproduce this same
period-vs-start/end branching — consuming the §4.4c NORMALIZED window — so the
bars returned are byte-identical to today's `DataFetcher` output (G2 depends on
the fetch call shape, not just the symbol): the intraday leg calls
`history(period=f"{lookback_days}d", interval=interval)` with the clamped
`lookback_days`, and the daily leg calls `history(start=start_norm,
end=end_norm, interval=interval)` where `end_norm` is the EXCLUSIVE upper bound
(§4.4c/HIGH-A). Because the normalization reproduces `DataFetcher`'s internal
`start = end - lookback_days` derivation and its 60-day intraday clamp exactly,
the service's yfinance call is indistinguishable from the one quant issues today.
The service's OHLCV post-processing MUST also reproduce `DataFetcher` exactly:
keep only `["Open","High","Low","Close","Volume"]`, `tz_localize(None)` if the
index is tz-aware, and set `df.index.name = "Datetime"` (all verified in
`fetcher.py`).

**G2 identity guarantee:** because (a) resolution is centralized, (b) the one
`DataFetcher` seam feeds every lane, and (c) indicators + dropna are a single
unchanged code path, the enriched frame — and therefore
`compute_snapshot_id(ticker, as_of, df)` — is identical across tracker/quant/deep
for the same `(ticker, interval, as_of)`. The G2 test (§9) asserts element-wise
frame equality and snapshot-id equality across the three entry points. Note the
snapshot-id ticker-arg caveat in §4.3: the id is identical across lanes because
all lanes pass the SAME caller ticker string and read the SAME enriched df; the
guarantee does not require the snapshot-id ticker to equal the DB/cache resolved
symbol (for the all-`.BO` golden set they coincide anyway).

### 4.7 FEAT A error handling

| Operation | Failure condition | Recoverable? | Caller receives | Logged |
| --- | --- | --- | --- | --- |
| Redis GET/SET | Redis down / timeout | Recoverable — skip cache, fall through to DB/yfinance | normal result (cache bypassed) | WARNING once per call (non-critical, mirrors SRC-T cache-failure handling) |
| Postgres SELECT | DB down | Fatal for that call | raises `DataServiceError` (wraps SQLAlchemy error) | ERROR |
| Postgres UPSERT | unique-constraint race | Recoverable — ON CONFLICT DO UPDATE (§5.3) | success | DEBUG |
| yfinance fetch | empty frame | Fatal for that call | raises `ValueError` (same message style as quant `DataFetcher`) | WARNING |
| yfinance fetch | network error / rate limit | Fatal for that call; DB/cache may still serve partial on a later call | raises `DataServiceError` | WARNING |
| ticker resolution | unknown/empty ticker | Fatal | raises `ValueError` | — |

`DataServiceError` is a new exception in `data_service.py`. The quant/deep
`ValueError("...requires daily bars/non-empty snapshot")` guards already present
stay as the last line of defense. No failure is silently swallowed except a
Redis read/write, which is explicitly non-critical (DB is the source of truth).

### 4.8 FEAT A input validation

| Input | Rule | On failure |
| --- | --- | --- |
| `ticker` | required, non-empty str after strip | `ValueError` |
| `interval` | required, must be in `Config.INTRADAY_INTERVALS ∪ {"1d"}` | `ValueError` |
| `start`/`end` | optional dates; normalized per §4.4c (`end` defaults to today, `start` derived from `lookback_days`); if both set, require `start < end`; `end` is the EXCLUSIVE point-in-time upper bound | `ValueError` if `start >= end` |
| `lookback_days` | optional positive int; intraday clamped to 60 (Config), daily uncapped | clamp (intraday) / `ValueError` if ≤0 |
| `asset_class` | one of `Config.ASSET_CLASSES` | `ValueError` |

---

## 5. PriceBar model & migration

### 5.1 `PriceBar` ORM model (`backend/app/models/price_bar.py`)

Mirrors SRC-T model style (`Base`, `Mapped`, `mapped_column`, `__table_args__`
with an `Index`). SRC-T `PriceHistory` uses `Numeric(12,2)` prices + `Integer`
volume; Step 2 deliberately departs from that for OHLCV precision (see the first
decision below and §4.4b): prices are stored as `DOUBLE PRECISION` so a
written-then-read value is bit-identical to the fetched `float64`, keeping G2
exact. Volume widens to `BigInteger`.

```python
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, Float, String, UniqueConstraint, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.database import Base

class PriceBar(Base):
    __tablename__ = "price_bars"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)      # resolved symbol, e.g. RELIANCE.BO
    interval: Mapped[str] = mapped_column(String(8), nullable=False)     # 1d, 30m, 5m ...
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)  # bar timestamp (tz-naive, matches quant)
    # OHLCV stored as IEEE-754 binary64 (Postgres DOUBLE PRECISION) so a
    # write->read round-trip is bit-identical to the pandas float64 cell (§4.4b, G2).
    open: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    high: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    low: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    close: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="yfinance")
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("ticker", "interval", "ts", name="uq_price_bars_tkr_interval_ts"),
        Index("idx_price_bars_lookup", "ticker", "interval", "ts"),
    )
```

Decisions:
- **`ts` as a single tz-naive `DateTime`** (not separate `date`/`ts`): intraday
  bars need a time component, daily bars store midnight; quant's frames are
  tz-naive `DatetimeIndex`, so one column keeps a 1:1 mapping and the unique key
  works for both intervals. The task text allows "ts/date"; a single `ts`
  column covers both and avoids a nullable-column branch.
- **`DOUBLE PRECISION` (binary64) prices**, chosen over SRC-T's `Numeric(12,2)`
  and over a wider `Numeric(18,4)`: `compute_snapshot_id` renders OHLCV via
  `float(value)` at 6 dp (V1), so G2 requires the DB-served frame to carry the
  SAME float as the fresh yfinance frame. A `Numeric(18,4)` column truncates to
  4 dp and a DB-served frame would render `...xxxx00` while a cold-fetch frame
  renders the true `...xxxx37`, so two lanes reading at different times could
  compute different `snapshot_id`s — exactly what HIGH-1 flagged. `double
  precision` is the same IEEE-754 binary64 as a pandas `float64` cell, so
  write→read is bit-identical and the snapshot is stable across cold/DB/Redis
  paths (§4.4b). A5 is updated accordingly: the snapshot is computed from a float
  frame that is now byte-faithful on EVERY path, not only the cold fetch.
- **`BigInteger` volume** — BSE large-cap daily volumes exceed 32-bit int.
- **unique `(ticker, interval, ts)`** — the point-in-time identity; one row per
  bar per interval.

### 5.2 Alembic migration

New revision under `backend/alembic/versions/` using the SRC-T async env and the
`%(year)…_%(rev)s_%(slug)s` filename template. `down_revision` chains onto the
existing initial revision `"001"`:

```python
revision = "002"
down_revision = "001"

def upgrade() -> None:
    op.create_table(
        "price_bars",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(32), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=False), nullable=False),
        # DOUBLE PRECISION (binary64) so OHLCV round-trips bit-identically to the
        # pandas float64 frame — see §4.4b / §5.1 (G2 determinism).
        sa.Column("open", sa.Float(precision=53), nullable=False),
        sa.Column("high", sa.Float(precision=53), nullable=False),
        sa.Column("low", sa.Float(precision=53), nullable=False),
        sa.Column("close", sa.Float(precision=53), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("source", sa.String(16), nullable=False, server_default=sa.text("'yfinance'")),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", "interval", "ts", name="uq_price_bars_tkr_interval_ts"),
    )
    op.create_index("idx_price_bars_lookup", "price_bars", ["ticker", "interval", "ts"])

def downgrade() -> None:
    op.drop_index("idx_price_bars_lookup", table_name="price_bars")
    op.drop_table("price_bars")
```

`news_items` (§6) and `data_snapshots` (§7) are added in the SAME revision 002
(one Step-2 schema migration) OR as sequential 002/003/004 — **decision: one
revision 002 "step-2 data tables"** creating all three tables plus the pgvector
extension enable, so a single `alembic upgrade head` provisions Step 2. The
pgvector `CREATE EXTENSION IF NOT EXISTS vector` runs first in `upgrade()`
(mirrors SRC-T's `CREATE EXTENSION pg_trgm`), even though no vector column is
used yet — it provisions for Step 4 RAG and satisfies scope.md "pgvector".

### 5.3 Upsert / point-in-time semantics (STEERING rule 7)

- **Upsert:** inserts use Postgres `INSERT ... ON CONFLICT (ticker, interval, ts)
  DO UPDATE SET open=…, high=…, low=…, close=…, volume=…, source=…,
  fetched_at=now()` (SQLAlchemy `pg_insert(...).on_conflict_do_update`). A
  re-fetch of the same bar overwrites OHLCV with the latest yfinance values and
  bumps `fetched_at`, but NEVER creates a duplicate row — the unique key is the
  bar identity.
- **Point-in-time read:** a snapshot at `as_of` reads
  `ts < as_of` only (strict upper bound, §4.4c/HIGH-A), so a backtest or a
  snapshot build sees exactly the bars yfinance's exclusive-`end` fetch would
  return up to — but not including — `as_of`; no lookahead (rule 7) and no
  one-bar divergence between the cold-fetch and DB-served frames. The `end`
  parameter is an EXCLUSIVE upper bound mapped to `ts < end`. (News uses an
  INCLUSIVE `published_at <= as_of`; that asymmetry is intentional, see §6.4.)
- **Why overwrite rather than version rows:** yfinance restates recent bars
  (split/dividend adjustment). For a personal research tool the latest adjusted
  value is the correct one; keeping historical restatements would bloat the
  table and is explicitly out of scope. `fetched_at` records provenance. This is
  flagged in §11 A5 because it means a snapshot_id for a very recent `as_of`
  could change if yfinance later restates those bars — acceptable for
  as-of-historical snapshots, noted for recent ones.

---

## 6. FEAT B — News/announcements ingest

### 6.1 Sources (configurable) & terms of use

Two source families, both configurable via settings (no hard-coded secret, no
paid feed):

1. **RSS feeds** — a configurable list `NEWS_RSS_FEEDS: list[str]` in
   `backend/app/config.py` (default: a small set of India-market business RSS
   feeds, e.g. a major business daily's markets feed). Parsed with `feedparser`.
2. **BSE announcements** — via `bsedata` (already a Tracker dep, used by
   `price_updater`). If `bsedata`'s announcements endpoint is not reliably
   reachable offline, FEAT B ships **RSS-only** with a documented TODO (see
   below). The design does not assume bsedata announcements are offline-friendly.

**Decision:** implement RSS ingest as the primary, always-on path, and add BSE
announcements as a best-effort secondary source guarded by a feature flag
`NEWS_BSE_ANNOUNCEMENTS_ENABLED: bool = False` (default off). Rationale: RSS via
`feedparser` is deterministic and testable; `bsedata` announcements reliability
offline is unverified (A6). When the flag is off, a `# TODO(step-2): verify
bsedata announcements endpoint reachability & ToS, then enable` is recorded in
code and in `docs/`. This keeps the integration test suite green without
depending on an unverified external endpoint.

**Terms-of-use obligation (hard requirement):** the implementation MUST document,
in `docs/news-sources.md`, for EACH configured source: the source name, feed
URL, its terms-of-use / robots stance, attribution requirement, and the
allowed-use note ("personal research, non-redistributed" — matching scope.md's
no-redistribution stop-and-pivot rule). A source whose ToU forbids automated
access or caching is NOT added. This doc is a deliverable of FEAT B, not
optional.

### 6.2 `NewsItem` model (`backend/app/models/news_item.py`)

```python
class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)  # resolved symbol or NULL (market-wide)
    source: Mapped[str] = mapped_column(String(64), nullable=False)        # feed/source id
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    dedup_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("dedup_hash", name="uq_news_items_dedup"),
        Index("idx_news_ticker_published", "ticker", "published_at"),
    )
```

### 6.3 Dedup hash definition

`dedup_hash = sha256( f"{source}|{url or headline}|{published_at_iso}" ).hexdigest()`

Precisely: the hash input is `source`, then `url` if non-empty else `headline`,
then the ISO rendering of `published_at` (or the empty string if the feed omits
it), joined by `|`. Chosen so that (a) the same item re-seen in a later poll
hashes identically → `ON CONFLICT (dedup_hash) DO NOTHING` makes re-runs add no
duplicates (idempotent ingest), and (b) two genuinely different items with the
same headline but different URLs do not collide. Using `url` as the primary
identity (falling back to headline) matches how RSS items are uniquely addressed.
Full sha256 hex (64 chars) stored in a `String(64)` unique column.

**Known over-dedup edge (accepted, NIT-3):** the opposite direction — a feed that
REUSES one landing URL across multiple updates AND omits `published_at` — makes
two genuinely different items hash identically, so the second is silently dropped
(`ON CONFLICT DO NOTHING`). This is accepted for a personal research tool:
feeds in the configured India-market set carry per-article URLs and publish
timestamps, so the collision is unlikely in practice. If a future feed exhibits
this pattern, the mitigation is to fold a content digest (e.g. a hash of the
summary/body) into `dedup_hash`; deferred until a real feed needs it.

### 6.4 Recent-headlines-for-ticker method

```python
# backend/app/repositories/news_repository.py
async def recent_for_ticker(
    ticker: str | None, limit: int = 10, as_of: datetime | None = None
) -> list[NewsItem]:
    # WHERE (ticker = :resolved OR ticker IS NULL)  -- ticker-specific + market-wide
    #   AND (:as_of IS NULL OR published_at <= :as_of)   -- point-in-time (rule 7)
    # ORDER BY published_at DESC NULLS LAST
    # LIMIT :limit
```

The `as_of` filter keeps news point-in-time for snapshot builds (rule 7): a
snapshot at a past date must only include headlines published on or before that
date. Market-wide items (`ticker IS NULL`) are included for every ticker.

**Boundary asymmetry with bar reads is INTENTIONAL (NIT-3).** News uses an
INCLUSIVE `published_at <= as_of`, whereas price-bar reads use an EXCLUSIVE
`ts < as_of` (§4.4c/§5.3/HIGH-A). This asymmetry is deliberate and must NOT be
"reconciled" by a later step: a headline published ON the `as_of` date is
legitimately KNOWN at `as_of` (news is a point event that has already occurred),
while a price bar DATED `as_of` represents the session that is not yet closed at
`as_of` and would be lookahead if included. Different domains, different correct
boundary; both are point-in-time-safe for their data type.

### 6.5 FEAT B error handling & validation

| Operation | Failure | Recoverable? | Caller receives | Logged |
| --- | --- | --- | --- | --- |
| RSS fetch (per feed) | network error / malformed feed | Recoverable — skip that feed, continue others | partial ingest result `{added, skipped, failed_feeds}` | WARNING per failed feed |
| feed entry parse | missing title/url | Recoverable — skip entry | counted in `skipped` | DEBUG |
| `published_at` parse | unparseable date | Recoverable — store NULL | entry stored with NULL date | DEBUG |
| DB insert | dedup conflict | Expected — `ON CONFLICT DO NOTHING` | counted in `skipped` | — |
| bsedata announcements (flagged) | endpoint unreachable | Recoverable — skip source | `failed_feeds` += source | WARNING |

Validation: `headline` required non-empty (trimmed); `url` required for RSS
(bsedata items may carry an announcement URL or a synthesized one); `source`
required; `ticker` optional (NULL = market-wide); `published_at` optional. An
entry missing a required field is skipped, never inserted with placeholders.

A failed feed is NEVER fatal to the whole ingest — one bad feed cannot block the
others. Ingest returns a summary dict (not an exception) unless EVERY feed fails,
in which case it raises `NewsIngestError` so a caller/task can alert.

---

## 7. FEAT C — The persisted DataSnapshot

### 7.1 `DataSnapshot` model (`backend/app/models/data_snapshot.py`)

```python
from sqlalchemy.dialects.postgresql import JSONB

class DataSnapshot(Base):
    __tablename__ = "data_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(16), primary_key=True)  # = compute_snapshot_id(...)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("idx_snapshots_ticker_asof", "ticker", "as_of"),
    )
```

- **PK is the EXISTING `compute_snapshot_id` value** (16-char sha256 prefix,
  `String(16)`), NOT an autoincrement id. This makes the snapshot content-addressed:
  identical inputs → identical id → one row (idempotency, §7.3).
- **`payload` is `JSONB`** holding: the OHLCV + indicator tail (last
  `SNAPSHOT_TAIL_N=60` enriched rows as records), the indicator summary
  (`TechnicalIndicators.summarize(df)`), and the recent headlines
  (`news.recent_for_ticker`). Exact payload schema in §7.2.

### 7.2 `build_and_store_snapshot` service (`backend/app/services/snapshot_service.py`)

```python
def build_and_store_snapshot(ticker: str, as_of: date) -> tuple[str, dict]:
    # 1. enriched daily frame via the SAME path quant/deep use:
    #       DataPipeline(ticker, interval="1d").get_enriched_data(
    #           end_date=as_of.isoformat(), interval="1d",
    #           lookback_days=200, use_cache=False)
    #    (DataFetcher now reads THROUGH MarketDataService — FEAT A §4.6)
    # 2. snapshot_id = compute_snapshot_id(ticker, as_of, df)   # EXISTING fn, imported
    # 3. idempotency: if a row with snapshot_id exists, return (snapshot_id, row.payload)
    #    WITHOUT recomputing/duplicating.
    # 4. else build payload and INSERT ... ON CONFLICT (snapshot_id) DO NOTHING
    # 5. return (snapshot_id, payload)
```

The `snapshot_id` is computed by importing the EXISTING
`backend.app.schemas.snapshot.compute_snapshot_id` — it is NOT reimplemented,
NOT forked, NOT wrapped with different column logic (the canonical columns live
in that module). This is the single hard invariant of FEAT C.

**Payload schema (JSONB):**

```jsonc
{
  "ticker": "RELIANCE.BO",
  "as_of": "2024-01-15",
  "interval": "1d",
  "snapshot_tail_n": 60,
  "bars": [ { "ts": "...", "Open": .., "High": .., "Low": .., "Close": .., "Volume": .. }, ... ],  // last 60 enriched rows incl. indicator cols
  "indicator_summary": { ...TechnicalIndicators.summarize(df)... },
  "headlines": [ { "source": .., "headline": .., "url": .., "published_at": .. }, ... ]  // news.recent_for_ticker(ticker, as_of=as_of)
}
```

The `bars` tail carries the canonical indicator columns so the payload is
self-describing and a reader can reconstruct what each engine saw. The snapshot
is the **source of truth for numbers** (rule 3): the number-check in
`deep/adapter.py` validates model figures against this data.

Sync vs async: `build_and_store_snapshot` is **synchronous** (it calls the quant
`DataPipeline` which is sync, and the sync DB facade), matching how
`quant.recommend` and `deep.analyze` run (offline/Celery callables, never inside
a web request). The DB write uses a synchronous SQLAlchemy session helper added
alongside the async engine (psycopg2 driver), OR an `asyncio.run`-bridged call
following SRC-T `price_updater._run_async` precedent. **Decision:** add a small
synchronous session factory (`sync_engine` via `psycopg2`) in
`backend/app/database.py` for the sync facade and the snapshot/price-bar sync
writes, rather than bridging async with `_run_async` everywhere — a dedicated
sync engine is cleaner for the sync-callable lanes and avoids event-loop
juggling in library code. The async engine remains the Tracker app's path. Both
engines point at the same DB URL (sync uses `postgresql+psycopg2://`, async uses
`postgresql+asyncpg://`).

**This is NOT a new pattern — it RELOCATES an existing SRC-T one (rule 5).**
SRC-T `backend/app/tasks/insight_calculator.py` already defines
`_get_sync_database_url()` (which swaps `postgresql+asyncpg` →
`postgresql+psycopg2`) and `_create_sync_session()` (a `create_engine` +
`sessionmaker(expire_on_commit=False)` factory with `pool_pre_ping=True`) for
its Celery/sync execution context, alongside the async `create_async_engine` +
`async_sessionmaker` in `backend/app/database.py`. Step 2 lifts that same URL-swap
+ sync-factory shape into `database.py` as a shared helper so the data-service
sync facade, `build_and_store_snapshot`, and the price-bar sync writes reuse it
instead of each rolling their own. SRC-T also already uses the `_run_async`
bridge (`price_updater.py`, `trend_fetcher.py`), so BOTH sync strategies exist in
SRC-T today; the design picks the dedicated-sync-engine strategy for library-level
callables because it avoids nesting event loops when a caller is itself async.

### 7.3 Idempotency (same inputs → same id → no duplicate row)

Because the PK IS the content hash and inserts use
`ON CONFLICT (snapshot_id) DO NOTHING`, calling `build_and_store_snapshot` twice
with the same `(ticker, as_of)` over the same bars:
1. computes the identical `snapshot_id`,
2. the second call finds the existing row and returns its payload (step 3) — or,
   if two calls race, the conflict clause makes the second insert a no-op.

Result: exactly one row, and the returned `(snapshot_id, payload)` round-trips
(the payload read back from JSONB equals the payload written, modulo JSON
numeric normalization — floats are stored as JSON numbers; see A5). The §9
integration test asserts "same id, one row, payload round-trips."

### 7.4 How `quant/recommend.py` and `deep/adapter.py` reference the persisted snapshot

Today both compute `snapshot_id` inline via `compute_snapshot_id(ticker, as_of, df)`
and do NOT persist anything. Step 2 change (behavior-identical offline):

- Both lanes continue to compute `snapshot_id` exactly as now (same function,
  same df) — so the id they put on the `Recommendation` is unchanged.
- A new, optional persistence seam: before returning, each lane may call
  `snapshot_service.build_and_store_snapshot(ticker, as_of)` (or a lighter
  `persist_if_absent(snapshot_id, ticker, as_of, df, headlines)`), which stores
  the snapshot the recommendation references. The `Recommendation.snapshot_id`
  now points at a PERSISTED row.
- **Offline-identical guarantee:** persistence is injected so it is a **no-op
  when no DB is configured** (the offline tests run without infra). The chosen
  mechanism: the lanes gain a **backward-compatible signature extension** — a
  trailing optional `snapshot_sink` callable defaulting to `None`. Adding a
  trailing optional parameter changes the signature but is backward-compatible:
  the existing positional/keyword call sites in `test_integration_slice.py`
  (`analyze("TEST", AS_OF)`, `recommend("TEST", AS_OF, df=enriched)`, VERIFIED)
  keep working unchanged, so the offline suite stays green. Chosen over making
  the lanes import the DB session directly (would couple the pure quant/deep
  callables to Postgres and break the offline suite).

**Who passes a real sink in Step 2 (resolves MEDIUM-2):** NOBODY in Step 2. The
lanes gain the optional `snapshot_sink=None` param, but **no Step-2 code path
passes a non-None sink** — the Celery/production jobs that would wire the real
`build_and_store_snapshot` into the lanes are owned by Steps 3/4. Consequently
Step 2's ONLY writer of `data_snapshots` is `build_and_store_snapshot` itself,
invoked directly (exercised by §9.2 integration test #5). The lane-level sink is
a forward-compatible seam placed in Step 2 so Steps 3/4 can wire it without
re-touching `recommend.py`/`adapter.py`; it is explicitly NOT a Step-2 persister.
This keeps the offline suite byte-identical (default `None` → no-op) and sets up
Step 3/4 wiring. (Note: §4.6 adds a SEPARATE seam inside `DataFetcher`; the
`snapshot_sink` here is the new seam in `recommend.py`/`adapter.py`
specifically — the two are distinct and neither is "the one new seam" globally.)

Decision rationale: the task requires the lanes "reference the PERSISTED
snapshot while keeping behavior identical offline." The optional-sink pattern
satisfies both: the computed id is unchanged (behavior identical), and when a
sink IS wired (Step 3/4) the referenced snapshot is durable.

### 7.5 FEAT C error handling & validation

| Operation | Failure | Recoverable? | Caller receives | Logged |
| --- | --- | --- | --- | --- |
| enriched fetch | empty/short frame | Fatal | `ValueError` (reuses quant guard) | WARNING |
| `compute_snapshot_id` | — (pure fn, total) | n/a | — | — |
| snapshot INSERT | PK conflict | Expected (idempotency) | returns existing payload | DEBUG |
| snapshot INSERT | DB down | Fatal for persist; recommendation still returns if sink errors are swallowed by the sink wrapper | sink logs ERROR; lane still returns rec with computed id | ERROR (in sink) |
| news fetch for payload | news source down | Recoverable — payload `headlines: []` | snapshot built w/o headlines | WARNING |

Validation: `ticker` non-empty, `as_of` a `date`, frame non-empty with ≥1 row
after dropna; payload must contain the four top-level keys. A persist failure in
the sink does NOT block the recommendation (the computed id is already valid),
but is logged ERROR so a nightly run surfaces it.

---

## 8. INFRA — compose stack, env, runbook

### 8.1 `infra/docker-compose.yml`

Brings up pgvector Postgres + Redis 7, reusing SRC-T compose env conventions
(`POSTGRES_USER/PASSWORD/DB`, healthchecks). Ollama stays on the HOST (decision
0002) — NOT a service. `infra/` exists today but is EMPTY (verified); this step
populates it with `docker-compose.yml` and `.env.example`.

```yaml
name: fintech-infra                         # explicit project name — see volume-collision note

services:
  postgres:
    image: pgvector/pgvector:pg16          # swap from postgres:16-alpine (unification-plan 1.1)
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-fintrack}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-fintrack_secret}
      POSTGRES_DB: ${POSTGRES_DB:-fintrack}
    ports: ["5432:5432"]
    volumes: ["pg_data:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-fintrack}"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    ports: ["6379:6379"]
    volumes: ["redis_data:/data"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  pg_data:
  redis_data:
```

Decisions:
- **Only Postgres + Redis in Step 2.** The backend/celery/gateway services are
  added in later steps (gateway runs server-free in-process today; the Tracker
  app isn't containerized in MONO yet). Step 2 only needs the two data stores to
  be reachable for integration tests and local dev. The compose file is
  additive — later steps append `backend`, `celery-*`, and the
  `host.docker.internal:11434` + `extra_hosts` gateway wiring from decision 0002.
- **pgvector extension** is enabled by the Alembic migration (§5.2
  `CREATE EXTENSION IF NOT EXISTS vector`), NOT by a compose init script, so the
  extension is version-controlled with the schema and a fresh DB provisions it
  on `alembic upgrade head`. Chosen over a `docker-entrypoint-initdb.d` SQL file
  because migrations are the single schema source and already enable `pg_trgm`
  the same way.
- **Volumes** named `pg_data`/`redis_data`. Docker Compose namespaces volumes by
  PROJECT NAME, so non-collision with SRC-T's volumes is guaranteed only if this
  stack runs under a distinct project name. The compose file therefore sets
  `name: fintech-infra` explicitly (resolves NIT-4) rather than relying on the
  incidental default project name (the `infra/` directory name); the actual
  volumes become `fintech-infra_pg_data` / `fintech-infra_redis_data`, which
  cannot collide with SRC-T's compose project. (Equivalent alternative:
  `docker compose -p fintech-infra ...`; the in-file `name:` is chosen so the
  guarantee does not depend on remembering a flag.)

### 8.2 `infra/.env.example`

```dotenv
# Postgres (compose + app)
POSTGRES_USER=fintrack
POSTGRES_PASSWORD=fintrack_secret
POSTGRES_DB=fintrack

# App DB URLs (async for the Tracker app, sync for the quant/deep facade)
DATABASE_URL=postgresql+asyncpg://fintrack:fintrack_secret@localhost:5432/fintrack
DATABASE_URL_SYNC=postgresql+psycopg2://fintrack:fintrack_secret@localhost:5432/fintrack

# Redis (cache + Celery broker)
REDIS_URL=redis://localhost:6379/0

# Gateway / Ollama stays on the HOST (decision 0002)
OLLAMA_BASE_URL=http://localhost:11434
GATEWAY_MODEL=llama3.2
GATEWAY_NUM_CTX=8192

# News ingest (FEAT B)
# PLACEHOLDER — not a resolvable feed. Replace with ToU-cleared feeds per
# docs/news-sources.md BEFORE running ingest (see §6.1 + NIT-4 note below).
NEWS_RSS_FEEDS=https://example-business-daily.invalid/markets.rss
NEWS_BSE_ANNOUNCEMENTS_ENABLED=false
```

Host-side tests use `localhost`; when the backend is containerized later it uses
the compose service names (`postgres`, `redis`) and `host.docker.internal` for
Ollama (decision 0002). The `.env.example` is copied to `infra/.env` locally;
`.env` is gitignored.

**Placeholder feed clarification (NIT-4).** The shipped `NEWS_RSS_FEEDS` value is
an intentionally non-resolvable PLACEHOLDER (`.invalid` TLD), NOT a feed meant to
be polled. It MUST be replaced with the real, ToU-cleared feed list documented in
`docs/news-sources.md` (§6.1 makes that doc a FEAT-B deliverable) before any
ingest run. This reconciles §6.1's "default: a small set of India-market business
RSS feeds" — that default is the vetted `docs/news-sources.md` list, which the
operator pastes into `infra/.env`, not the placeholder. The §9.2 integration
tests #4/#6 do NOT depend on this URL: they ingest from a **locally-served
recorded fixture feed** (a saved RSS file served over `http://localhost`), so the
suite neither reaches the public internet nor fails with "all feeds unreachable"
on a bare checkout. With the placeholder in place and no override, an ingest run
logs a single WARNING per failed feed and returns `{added:0, ...}` (§6.5) rather
than crashing.

### 8.3 `docs/runbook.md` bring-up steps (to append)

Add a "Step 2 — data stack" section:

1. `cp infra/.env.example infra/.env` (edit secrets if desired).
2. `docker compose -f infra/docker-compose.yml up -d` → wait for both
   healthchecks green (`docker compose ps`).
3. From repo root with the venv active and editable install done (decision 0006):
   `alembic -c backend/alembic.ini upgrade head` → creates `price_bars`,
   `news_items`, `data_snapshots` and enables `vector`.
4. Smoke: `py -m pytest -m integration tests/ -k price_bar` (requires the stack).
5. Tear down: `docker compose -f infra/docker-compose.yml down` (add `-v` to
   drop volumes).

The runbook already documents the editable-install order (0006); Step 2 appends
the compose + migrate steps before the integration tests.

---

## 9. TEST STRATEGY

### 9.1 Marker & split

- Register `integration` in `pyproject.toml`
  `[tool.pytest.ini_options] markers = ["integration: requires Postgres+Redis+network"]`.
- The existing **39 offline tests stay green and fast with NO infra**: they run
  by default; integration tests are collected only when the stack is up. Each
  integration test guards with a fixture that **skips with a reason** when
  Postgres/Redis/yfinance is unreachable (mirrors `test_gateway_live._ollama_up`
  + `pytest.skip`). So `py -m pytest` on a bare machine = 39 passed, integration
  skipped; `py -m pytest -m integration` with the stack up = real coverage.
- Default run excludes nothing (offline tests have no marker); CI/dev selects
  integration explicitly with `-m integration`. The offline suite MUST NOT gain
  a DB dependency — FEAT A/C seams are injected (sync facade / optional sink) so
  offline tests patch the fetch path exactly as `test_integration_slice.py` does
  today.

### 9.2 Integration coverage (real Postgres + Redis + real yfinance)

1. **Real golden-ticker fetch lands rows in `price_bars`** — call
   `MarketDataService.get_ohlcv("RELIANCE.BO","1d", lookback_days=200)`; assert
   rows appear in `price_bars` with the resolved ticker, correct interval, and
   ≥60 bars.
2. **Second fetch served from cache/DB (yfinance NOT re-hit)** — monkeypatch /
   spy the yfinance call to assert zero network hits on the second
   `get_ohlcv(...)` for the same range; the result equals the first.
2a. **Cold == DB == Redis snapshot_id (HIGH-1 / §4.4b, the precision gate) with a
   TRADING-DAY `as_of` (HIGH-A, exercises the boundary)** — for a golden ticker:
   (i) cold-fetch the frame (empty DB + flushed Redis) and record
   `compute_snapshot_id(ticker, as_of, enriched)`; (ii) flush Redis and force a
   DB-served read of the same range, recompute the id; (iii) warm Redis and force
   a Redis-served read, recompute the id. Assert all three `snapshot_id`s are
   EQUAL. **`as_of` MUST be a trading day** (e.g. a known BSE session date), NOT
   a weekend/holiday, so a bar dated exactly `as_of` COULD exist in the DB and
   the exclusive-`end` predicate (`ts < as_of`, §4.4c) is actually exercised — a
   holiday-dated `as_of` would never hit the boundary and the test could pass
   trivially. The assertion proves (a) `DOUBLE PRECISION` storage + exact
   float-repr Redis serialization make the VALUES byte-faithful on every path,
   AND (b) the exclusive-`end` DB/cache predicate produces the SAME row set (same
   tail, same id) as the cold yfinance fetch — the two halves of the G2
   cross-path guarantee.
3. **Gate G2 element-wise equality across tracker/quant/deep** — for every
   golden ticker, obtain the enriched daily frame through (a) quant
   `DataPipeline`, (b) the deep adapter's pipeline build, (c) the Tracker service
   path, and assert `pandas.testing.assert_frame_equal` on the canonical columns
   AND equal `compute_snapshot_id`. This is the gate.
4. **News ingest dedup + re-run adds no duplicates** — ingest a feed (real or a
   recorded fixture feed served locally), count rows; re-run ingest; assert row
   count unchanged (dedup_hash `ON CONFLICT DO NOTHING`).
5. **Snapshot idempotency** — `build_and_store_snapshot(t, as_of)` twice; assert
   same `snapshot_id`, exactly one `data_snapshots` row, and payload round-trips
   (`payload == db_row.payload`).
5a. **Historical read is DB-authoritative, no refetch (MEDIUM-4 / §4.4a)** — seed
   `price_bars` with a complete historical window (`end < today -
   FRESH_WINDOW_DAYS`), spy the yfinance call, then `get_ohlcv(...)` for that
   window; assert ZERO yfinance hits and that the returned frame (and thus the
   historical `snapshot_id`) matches the seeded bars — proving a past-dated read
   never triggers a restatement refetch. A companion unit test (no infra) feeds a
   stubbed repo with a recent-window `end` and a sub-threshold row count and
   asserts the coverage ratio DOES trigger a refetch.
6. **Skip-with-reason for flaky external sources** — the yfinance/news fixtures
   `pytest.skip("yfinance unreachable")` / `skip("no RSS feed reachable")` so
   the suite stays green when an external source is down (real-data tests must
   not flake the pipeline).

### 9.3 Golden-set materialization under `tests/golden/`

`tests/golden/README.md` PROPOSES 10 `.BO` tickers (5 large, 3 mid, 2 volatile)
and states "price bars saved as CSV/Parquet here once the data service exists
(Step 2)." Step 2 MATERIALIZES them:

- The integration run fetches each golden ticker's 200-day daily bars through
  `MarketDataService` and SAVES them to `tests/golden/<TICKER>_1d.parquet`
  (point-in-time, rule 7). Parquet chosen over CSV to preserve dtypes and the
  tz-naive `DatetimeIndex` losslessly (CSV would re-introduce string-parse
  ambiguity that could perturb `compute_snapshot_id`).
- A FAST offline loader test (no infra) loads each saved parquet, asserts row
  counts + date ranges (satisfying Gate G0's "golden data loads in a test"), and
  feeds them as the `df=` argument to `quant.recommend` / as the patched
  pipeline return for `deep.analyze` — so the offline G2-style equality can run
  WITHOUT network, and the integration test re-verifies against live yfinance.
- **Confirm vs README:** the README's exact 10-ticker list is still marked
  "NEEDS USER CONFIRMATION" and "4 blocked decisions" in status.md includes the
  golden list. §11 A8 flags this: materialization uses the proposed list but the
  exact tickers are a user confirmation item; if the user changes the list, only
  the saved parquet set changes, not the design.

### 9.4 Testability notes

- **Unit-testable (no infra):** ticker resolution; dedup-hash function; payload
  builder (given a frame + headlines); idempotency logic (given a fake
  session/repo); the two-regime coverage decision (`FRESH_WINDOW_DAYS` historical
  short-circuit + `DB_COVERAGE_MIN_RATIO` recent-window ratio, §4.4a); the
  sync-facade wrapper (given a stubbed async core); and the **§4.4c window
  normalization + key parity (HIGH-B)** — assert that
  `get_ohlcv(symbol, "1d", end=D, lookback_days=200)` and the explicit-`start`
  equivalent `get_ohlcv(symbol, "1d", start=D - 200d, end=D)` derive the SAME
  normalized `(start, end)`, build the SAME Redis key
  `ohlcv:{symbol}:1d:{start}:{end}`, and (with the yfinance leg spied/stubbed)
  issue the SAME daily `history(start=, end=, interval="1d")` call shape with the
  EXCLUSIVE `end`; plus an intraday case asserting the 60-day clamp and the
  `history(period="60d", interval=...)` shape. This pins the HIGH-B normalization
  without any infra. These run in the offline suite.
- **Integration-only:** real UPSERT/ON CONFLICT behavior, Redis round-trip,
  real yfinance landing, pgvector extension present, G2 across real bars.
- A design smell check: if the G2 equality test needed per-lane tolerances it
  would signal the fetch path had diverged — the design keeps ONE `DataFetcher`
  seam + ONE indicator path precisely so the assertion can be exact, not
  tolerance-based.

---

## 10. Hard-constraint compliance checklist

| Constraint | How this design satisfies it |
| --- | --- |
| No paid APIs / no OpenAI keys | yfinance + RSS + bsedata only; no key anywhere; `.env.example` has none |
| Gateway is the only path to Ollama; Ollama on host | Step 2 touches no LLM path; compose has no Ollama service (decision 0002) |
| Never edit `third_party/TradingAgents` | Step 2 does not touch it; setuptools `exclude` keeps it unscanned |
| Models explain, code computes; snapshots are source of truth | `data_snapshots.payload` is the numeric source; deep number-check validates against it |
| ONE Recommendation contract | §3.3 merge re-exports the single contract; no second definition |
| ONE `compute_snapshot_id` | §7.2 imports the EXISTING function; never forks the canonical columns |
| Tracker keeps running (relocate, not rewrite) | §3.2 copies the shell verbatim (mechanical `app.`→`backend.app.` only); SRC-T untouched until G2 |
| Point-in-time snapshots | bar reads `ts < as_of` (EXCLUSIVE, §4.4c/HIGH-A, matches yfinance); news `published_at <= as_of` (INCLUSIVE, intentional asymmetry §6.4); parquet goldens are point-in-time |
| No worktree | all work directly in `MONO` |

---

## 11. Ambiguities & unverified assumptions (for the design reviewer)

- **A1 — single `ts` column vs `date`+`ts`.** The task says "ts/date". This
  design uses ONE tz-naive `ts` column for both daily and intraday. If the
  reviewer wants a separate `date` for daily partitioning, the unique key and
  reads change. Assumed: single `ts` is sufficient.
- **A2 — sync engine addition (RESOLVED: existing SRC-T pattern, not new).**
  This design adds a synchronous SQLAlchemy engine (`psycopg2`) for the sync
  quant/deep callers. Verification shows SRC-T ALREADY uses this exact shape:
  `tasks/insight_calculator.py` has `_get_sync_database_url()` (asyncpg→psycopg2
  swap) + `_create_sync_session()` (`create_engine` + `sessionmaker`). SRC-T also
  has the `_run_async` bridge (`price_updater.py`, `trend_fetcher.py`). So Step 2
  relocates/shares an existing sync-session pattern (rule 5) rather than inventing
  one; the only choice is where it lives (`database.py` as a shared helper vs
  duplicated per task). No reviewer action needed beyond confirming the shared
  location.
- **A3 — `.BO` default for bare Indian symbols.** Resolution defaults a bare
  stock symbol to `.BO`. US tickers (optional per scope.md) would be mis-suffixed
  if passed bare. Assumed: callers pass `.BO`/`.NS` explicitly (golden set does);
  bare symbols default to `.BO`. Needs confirmation if US tickers become
  first-class.
- **A4 — DB coverage heuristic (RESOLVED — bounded, §4.4a).** The coverage
  decision is now two-regime: a **historical** window (`end < today -
  FRESH_WINDOW_DAYS`) with ≥1 stored row is DB-authoritative (no refetch), so a
  past-dated read never restates bars or perturbs a historical `snapshot_id`; the
  **recent** window uses the expected-row-count ratio (`DB_COVERAGE_MIN_RATIO =
  0.90`) and its only worst case is an idempotent refetch of recent bars. This
  removes the "harmless" over-claim the review flagged (MEDIUM-4).
- **A5 — float round-trip & snapshot stability (RESOLVED — §4.4b, §5.1).**
  `price_bars` OHLCV is stored as `DOUBLE PRECISION` (binary64), so a DB- or
  Redis-served frame is BIT-IDENTICAL to the cold-fetch pandas `float64` frame.
  `compute_snapshot_id` therefore sees the same floats on every path, and
  cold/DB/Redis `snapshot_id`s are equal (asserted by §9.2 test #2a). The one
  residual non-determinism is the §5.3 overwrite-on-upsert combined with
  yfinance restating recent bars: a `snapshot_id` for a very recent `as_of` can
  change if those bars are later restated. This is now confined to the recent
  window (A4 keeps historical reads DB-authoritative). Assumed acceptable for a
  personal tool; historical as-of snapshots are stable. **Volume caveat (NIT-2):**
  `volume` is stored as `BigInteger`, which is byte-faithful for the hash ONLY
  because yfinance volume is integral in practice (§4.4b). A hypothetical source
  returning FRACTIONAL volume would truncate in the int column and perturb the
  snapshot; the documented mitigation is to widen `volume` to `DOUBLE PRECISION`
  if such a source is ever added. Not a Step-2 concern (yfinance `.BO`/`.NS`
  volume is integral).
- **A12 — bar-read boundary is EXCLUSIVE on `end`/`as_of` (RESOLVED — §4.4c,
  HIGH-A).** The service pins `end`/`as_of` as an EXCLUSIVE upper bound on EVERY
  path (cold yfinance, DB, Redis) — `ts < end` — to match yfinance's exclusive
  `end` and keep cold/DB/Redis frames carrying the identical row set (and thus
  the identical `snapshot_id`). News deliberately stays INCLUSIVE
  (`published_at <= as_of`, §6.4) — the asymmetry is intentional, not an
  oversight. Chosen over normalizing everything to inclusive (which would have
  changed every existing `snapshot_id`).
- **A13 — `start=None` / `lookback_days` normalization (RESOLVED — §4.4c,
  HIGH-B).** The G2 live path always passes `start=None, lookback_days=200`, so
  the service normalizes `(start, end, lookback_days)` to concrete dates ONCE at
  the top of `get_ohlcv` (mirroring `DataFetcher`'s internal derivation and
  60-day intraday clamp exactly), and the Redis key, DB predicate, and yfinance
  leg all consume the normalized window — guaranteeing two callers at the same
  `(as_of, lookback_days, interval)` collide on one cache key. A no-infra
  key-parity unit test (§9.4) pins this.
- **A6 — bsedata announcements offline reliability UNVERIFIED.** FEAT B ships
  RSS-first with BSE announcements behind a default-off flag and a documented
  TODO, because bsedata's announcements endpoint reachability offline is not
  verified. If the reviewer requires BSE announcements in Step 2, this needs a
  reachability + ToU check first.
- **A7 — requirements pin versions (names VERIFIED).** The dep NAMES in §3.4 are
  verified verbatim against both `requirements.txt` files (slice + SRC-T), incl.
  `pytrends`. Only the patch-level VERSIONS in §3.4 (e.g. `sqlalchemy==2.0.*`,
  `celery==5.3.*`) are representative and get resolved against `MONO/.venv` at
  implementation time; the LOCKED invariant is "no overlapping name changes the
  slice's pin, and every Tracker import resolves."
- **A8 — golden ticker list still needs user confirmation.** status.md lists the
  exact 10-ticker golden set as an open decision. Materialization (§9.3) uses the
  proposed list; changing it changes only the saved parquet set, not the design.
- **A9 — `feedparser` is a NEW dependency.** It is not in either source
  `requirements.txt`. Added pinned in §3.4. If the reviewer prefers reusing an
  existing HTTP+XML path instead, FEAT B's parser swaps behind the same ingest
  interface.
- **A10 — one Alembic revision 002 for all three tables + pgvector.** Chosen for
  a single `upgrade head`. If the reviewer wants one table per revision for
  finer rollback, split into 002/003/004 with chained `down_revision`.
- **A11 — Tracker intra-package import rewrite scope.** The `app.`→`backend.app.`
  rewrite touches every relocated Tracker module's imports and `alembic/env.py`.
  This is mechanical and preserves logic (rule 5), but it is a broad textual
  change; the reviewer should confirm the editable-install import model
  (decision 0006) is the intended single import root rather than adding
  `backend/` to `sys.path`.

---

## 12. Verification log (what this design was checked against)

Every claim below was confirmed by reading the live source in this pre-review
pass; the design text above is written to match it.

| # | Claim in the design | Verified against | Result |
| --- | --- | --- | --- |
| V1 | `compute_snapshot_id` = 16-char sha256 prefix, `SNAPSHOT_TAIL_N=60`, cells rendered `float(...)` at 6-dp, canonical cols incl. SMA_50/RSI | `backend/app/schemas/snapshot.py` | Matches; A5 (compute from float frame) holds — cells are `float(value)` |
| V2 | `Config.resolve_ticker` handles forex/commodity/stock, returns stock as-is, no `.BO`/`.NS`; `INTRADAY_INTERVALS`, `INTRADAY_MAX_LOOKBACK_DAYS=60` | `quant/config.py` | Matches exactly |
| V3 | `DataFetcher` is the single yfinance touchpoint; resolves in `__init__`; OHLCV-only tz-naive `Datetime`-named frame; `fetch_historical` is period-based intraday vs start/end daily; `fetch_latest(period, interval)` is PERIOD-only (`60d`/`3mo` defaults), no start/end/lookback | `quant/data/fetcher.py` | Matches; drove the "idempotent double-resolution" + "fetch-shape parity" notes (§4.3, §4.6) and the HIGH-2 decision to seam ONLY `fetch_historical` and leave `fetch_latest` on yfinance |
| V15 | SRC-T `alembic.ini` has `script_location = alembic` + `prepend_sys_path = .` (both resolved relative to the invocation CWD, not the ini location); `env.py` imports `from app.config`, `from app.database`, `import app.models` | `Tracker-Tool/backend/alembic.ini`, `alembic/env.py` | Matches; drove MEDIUM-1 fix — run from repo root, rewrite `script_location = backend/alembic`, keep `prepend_sys_path = .`, rewrite all three `app.`→`backend.app.` imports in `env.py` |
| V16 | SRC-T `schemas/__init__.py` `__all__` is exactly **29** names (6 Alert + 5 Insight + 6 Education + 12 Report) in Alert/Insight/Education/Report groups | `Tracker-Tool/backend/app/schemas/__init__.py` | Matches (re-counted r2 — earlier "30" was WRONG, see MEDIUM-A); §3.3 lists the verbatim union (merged length `29 + 5 = 34`) + the dynamic `set`/`len(srct)+5` CI gate (MEDIUM-3) |
| V4 | Daily dropna is `[SMA_{sma_long}, "RSI"]` with `sma_long=50` for `1d`; `get_enriched_data(..., use_cache=False)` seam point | `quant/data/pipeline.py`, `quant/config.py` INDICATOR_PERIODS | Matches; §4.6 updated to state dropna is computed, not hard-coded |
| V5 | `recommend(ticker, as_of, df=None)` imports `backend.app.schemas.snapshot.compute_snapshot_id`; live path uses `lookback_days=200, interval="1d"` | `quant/recommend.py` | Matches; confirms import-root model (§3.5) and the snapshot-id ticker-arg caveat (§4.3) |
| V6 | `analyze(ticker, as_of, portfolio=None)` builds via same `DataPipeline` call, imports + uses the ONE `compute_snapshot_id`, calls `TechnicalIndicators.summarize` (static) | `deep/adapter.py` | Matches; `summarize` is a `@staticmethod` |
| V7 | `summarize(df)` returns a nested dict of plain float/int (JSONB-safe) with keys price/moving_averages/momentum/volatility/volume | `quant/data/indicators.py` | Matches §7.2 `indicator_summary` payload key |
| V8 | SRC-T has an existing sync-engine pattern (not new): `_get_sync_database_url` asyncpg→psycopg2 swap + `_create_sync_session` (`create_engine`+`sessionmaker`), and `_run_async` bridge | `Tracker-Tool/backend/app/tasks/insight_calculator.py`, `price_updater.py`, `trend_fetcher.py` | Matches; A2 downgraded from "new pattern" to "relocate existing" |
| V9 | SRC-T alembic initial revision is `001` with `down_revision=None`, enables `pg_trgm` via `op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")` | `Tracker-Tool/backend/alembic/versions/20250101_0001_001_initial_models.py` | Matches; Step-2 `002` chains onto `001` and mirrors the extension-enable for `vector` |
| V10 | SRC-T `schemas/__init__.py` re-exports ONLY `alert_schemas` + `report_schemas`; `portfolio/stock/user_schemas.py` exist on disk but are not re-exported | `Tracker-Tool/backend/app/schemas/__init__.py` + dir listing | Matches; §3.3 tightened, over-hedge removed |
| V11 | Slice `requirements.txt` pins and SRC-T dep names (incl. `pytrends`, `feedparser` absent from both) | `requirements.txt`, `Tracker-Tool/backend/requirements.txt` | Matches verbatim; A7 names locked, A9 (feedparser net-new) confirmed |
| V12 | `pyproject.toml` `[tool.setuptools] packages = ["backend","quant","deep","gateway"]`; no pytest `markers` yet; `asyncio_mode="auto"` present | `pyproject.toml` | Matches; §3.5 switch to `packages.find` and §9.1 marker registration are genuine additions |
| V13 | Exactly 39 offline tests collected; `test_gateway_live._ollama_up` + `pytest.skip` skip pattern; `test_integration_slice` patches the pipeline via `monkeypatch` and has a share-one-snapshot-id test | `pytest --collect-only`, `tests/test_gateway_live.py`, `tests/test_integration_slice.py` | Matches; "39 offline", the skip-with-reason, and the injected-seam offline-patch claims (§9) are all real |
| V14 | `infra/` directory state | repo tree | Exists but EMPTY — §8.1 corrected from "does not yet exist" to "exists but empty" |

The only items that remain genuinely UNVERIFIABLE in this pass and stay flagged
for the reviewer/user are: A3 (`.BO` default policy — product decision), A6
(`bsedata` announcements offline reachability + ToU — external endpoint), and A8
(exact golden-ticker list — pending user confirmation). Everything else above is
grounded in the current source.

---

## 13. Responses to design review r1

Review: `docs/designs/step-2-design-review.md` /
`step-2-design-review.json` (verdict CHANGES_REQUESTED — 2 HIGH, 4 MEDIUM, 5 NIT).
Every HIGH and MEDIUM is ADDRESSED; all NITs addressed. Responses below.

- **HIGH-1 (G2 determinism / float round-trip) — ADDRESSED.** Chose review
  Option A: OHLCV is stored as `DOUBLE PRECISION` (binary64) and Redis is
  serialized as the exact `float64` repr, so DB- and Redis-served frames are
  bit-identical to the cold-fetch pandas `float64` frame. New §4.4b states the
  single-canonical-float-frame invariant; §4.5 pins the byte-faithful Redis
  encoding; §5.1 + §5.2 switch the model/migration columns from `Numeric(18,4)`
  to `Float(precision=53)`; A5 updated; §9.2 adds test #2a asserting
  cold == DB == Redis `snapshot_id`. Option B was rejected in-text because it
  would alter the canonical `compute_snapshot_id` render (forbidden: never fork
  the canonical id).

- **HIGH-2 (`fetch_latest` seam) — ADDRESSED.** Scoped the Step-2 seam to
  `fetch_historical` ONLY (verified it is the sole G2 path). §4.6 now states
  `fetch_latest` is left calling yfinance directly (period-based, off the G2
  path), and the "EXACT signature" claim applies only to `fetch_historical`.
  Routing `fetch_latest` via a `period`→(start,end) translation is explicitly
  deferred. V3 updated with the verified `fetch_latest` signature.

- **MEDIUM-1 (alembic path resolution) — ADDRESSED.** Pinned ONE invocation
  model (run from repo root), rewrote the §3.2 guidance to
  `script_location = backend/alembic` + `prepend_sys_path = .`, and called out
  that all three `env.py` imports (`from app.config`, `from app.database`,
  `import app.models`) get the `app.`→`backend.app.` rewrite. §8.3 step 3 is
  consistent. New V15 records the verified `alembic.ini`.

- **MEDIUM-2 (`snapshot_sink` seam) — ADDRESSED.** §7.4 now states plainly that
  in Step 2 NO code passes a non-None sink (Celery wiring is Step 3/4), so the
  only Step-2 writer of `data_snapshots` is `build_and_store_snapshot` via
  integration test #5; the lane sink is a forward-compatible seam. Reworded
  "keep current signatures" → "backward-compatible signature extension," and
  disambiguated it from the separate §4.6 `DataFetcher` seam.

- **MEDIUM-3 (`schemas/__init__.py` `__all__`) — ADDRESSED.** §3.3 now lists the
  merged `__all__` as SRC-T's verbatim **29-name** list (verified, V16; the
  earlier "30" was a miscount, corrected under r2 MEDIUM-A) with the five
  contract names prepended → merged length `29 + 5 = 34`, and specifies the
  mechanical rule plus a CI gate expressed ONLY via the dynamic
  `set(merged) == set(srct) | {contract}` AND `len(merged) == len(srct) + 5`
  (no hard-coded count).

- **MEDIUM-4 (coverage heuristic vs point-in-time) — ADDRESSED.** New §4.4a
  bounds the ratio to the RECENT window and makes historical reads
  (`end < today - FRESH_WINDOW_DAYS`, ≥1 row) DB-authoritative with no refetch,
  so a past-dated read can't restate bars or change a historical `snapshot_id`.
  A4 updated; §9.2 adds test #5a (historical no-refetch) + a companion unit test.

- **NIT-1 — ADDRESSED.** Inline INVARIANT comment added at the top of the §3.4
  requirements block.
- **NIT-2 — ADDRESSED.** §3.4 now gates each of the four extra deps on a
  verified import from the Step-2 `backend.app.main` import graph; unreachable
  ones are deferred.
- **NIT-3 — ADDRESSED.** §6.3 now documents the over-dedup edge (reused URL +
  missing `published_at`) and its deferred mitigation.
- **NIT-4 — ADDRESSED.** Compose file sets `name: fintech-infra`; §8.1 explains
  the project-name namespacing so non-collision is guaranteed.
- **NIT-5 — ADDRESSED.** §4.2 now points at the §4.6 post-processing block as the
  single normative source for the column/index/dtype contract.

Unverified items the review re-confirmed as open are product/external decisions,
not design gaps, and remain flagged: A3 (`.BO` default), A6 (bsedata ToU/
reachability, shipped default-off), A8 (golden ticker list, user confirmation).
The "39 offline tests" count (V13) stands as verified by the original pass via
`pytest --collect-only`.

---

## 14. Responses to design review r2

Review: `docs/designs/step-2-design-review.md` / `step-2-design-review.json`
(verdict CHANGES_REQUESTED — 2 HIGH, 2 MEDIUM, 4 NIT). The r2 pass confirmed all
r1 fixes genuinely landed and raised two NEW HIGH findings on window-boundary
semantics that the r1 precision fix did not cover, plus two MEDIUM documentation
defects in the `__all__` merge and four NITs. Every HIGH and MEDIUM is ADDRESSED;
all NITs addressed.

- **HIGH-A (exclusive-`end` fetch vs inclusive DB predicate → one-bar divergence,
  breaks G2) — ADDRESSED (chose Option 1).** VERIFIED in `quant/data/fetcher.py`
  that the daily leg's `history(start=, end=)` treats `end` as EXCLUSIVE. Pinned
  ONE boundary convention: `end`/`as_of` is EXCLUSIVE on every path. New §4.4c
  states it normatively; the §4.4 read-path diagram now reads
  `ts >= start AND ts < end`; §4.2 signature comment, §4.4a coverage window
  (`[start, end)`), §4.8 validation (`start < end`), §5.3 point-in-time read
  (`ts < as_of`), and the §10 checklist are all reconciled to exclusive. Option 2
  (shift yfinance `end`+1d, keep inclusive) was rejected in-text because it would
  change every existing `snapshot_id`. §9.2 test #2a now mandates a TRADING-DAY
  `as_of` so the boundary is actually exercised. New A12 records the decision.

- **HIGH-B (`start=None` + `lookback_days` key/coverage/fetch unspecified on the
  only G2 path) — ADDRESSED.** VERIFIED both live lanes call with `start=None,
  end=as_of, lookback_days=200`. New §4.4c adds ONE normalization step at the top
  of `get_ohlcv` (mirroring `DataFetcher`'s internal `start = end - lookback_days`
  derivation, the `DEFAULT_LOOKBACK_DAYS=120` fallback, and the 60-day intraday
  clamp exactly), and states that the Redis key (§4.5), the DB predicate
  (§4.4/§4.4a), and the yfinance leg (§4.6) ALL consume the NORMALIZED window,
  never the literal `None`. §4.5 key shape updated to `{start_norm}:{end_norm}`.
  §9.4 adds a no-infra key-parity + fetch-shape unit test (end+lookback call ≡
  explicit-start call). New A13 records the decision.

- **MEDIUM-A ("30 names" is wrong; it is 29) — ADDRESSED.** Re-counted against
  `Tracker-Tool/backend/app/schemas/__init__.py`: SRC-T `__all__` is 29 (6+5+6+12).
  Changed every "30" to "29" in §3.3, V16, and the §13 MEDIUM-3 response (merged
  length `29 + 5 = 34`). The CI gate stays expressed ONLY via dynamic
  `len(srct.__all__) + 5` and the set union — no hard-coded 34/35 — with an
  explicit note that the gate, not the prose, is authoritative so the count
  can't desync again.

- **MEDIUM-B (merged `__all__` regrouped/reordered, contradicting "verbatim")
  — ADDRESSED.** Made the merged `__all__` block TRULY verbatim: the 29 Tracker
  names now appear in SRC-T's exact order under SRC-T's own four comment headers
  (one name per line, matching SRC-T's layout), with the five contract names
  prepended. The surrounding claim is also softened to state plainly that
  ordering is NOT significant to the gate (which checks set + length) and the
  verbatim block is for readability — so the implementer is never told both
  "verbatim" and "regrouped."

- **NIT-1 (numpy→float cast before `json.dumps`) — ADDRESSED.** §4.5 now names
  the exact mechanism: cells are cast `np.float64 -> Python float` (lossless,
  both IEEE-754 binary64) before `json.dumps`, so no `TypeError` and no lossy
  `round()` is ever reached.

- **NIT-2 (volume integrality for `BigInteger` hash stability) — ADDRESSED.**
  §4.4b now states volume is integral in practice so the `BigInteger` column is
  byte-faithful under `compute_snapshot_id`'s float render, with a guard note
  (widen to `DOUBLE PRECISION` if a fractional-volume feed is ever added). A5
  carries the same caveat.

- **NIT-3 (news-vs-bar boundary asymmetry) — ADDRESSED.** §6.4 now states the
  asymmetry (news inclusive `<= as_of`, bars exclusive `< as_of`) is INTENTIONAL
  and must not be "fixed" to match, with the domain rationale. §5.3 cross-refs it.

- **NIT-4 (placeholder RSS URL conflicts with §6.1 / tests) — ADDRESSED.** §8.2
  now marks `NEWS_RSS_FEEDS` as an intentional non-resolvable placeholder
  (`.invalid` TLD) to be replaced per `docs/news-sources.md` before ingest, and
  states §9.2 tests #4/#6 use a locally-served recorded fixture feed, not this
  URL; reconciled with §6.1's "vetted default list" wording.

All r2 HIGH + MEDIUM findings are resolved by pinning ONE exclusive-`end`
boundary convention and ONE window-normalization step on the G2 path and by
fixing the `__all__` count/verbatim documentation. The open items remain the same
product/external decisions (A3, A6, A8); no new source-contradicting assumption
was introduced.

---

## CARRIED-FORWARD REQUIREMENTS (design loop ended at iteration 3 — orchestrator decision)

The design loop was ended with these findings from `step-2-design-review.json` (final iteration) still open: **3 MEDIUM + 3 NIT, 0 HIGH**. They are NOT dismissed — they are mandatory build requirements, and the CODE REVIEW must verify each against running code + the live integration tests. They were moved to the build phase because the two HIGH Gate-G2 determinism blockers are now RESOLVED (exclusive-end boundary + single window-normalization, verified against live source), and each remaining MEDIUM has a concrete, fully-specified fix already written — there is no open design decision left, only implementation. This matches how the earlier vertical-slice design loop was closed.

**REQ-S2-1 (was MEDIUM-1) — G2 three-lane test must compare the ENRICHED frame.**
The unified `MarketDataService` returns RAW OHLCV only (it never computes indicators). `compute_snapshot_id` intersects the canonical indicator columns present on the frame, so a raw Tracker frame would hash over OHLCV-only while quant/deep hash the full enriched set — the ids cannot match by construction. Implement the Gate G2 test as:
  - Precondition assertion: the raw OHLCV bars the three lanes receive from the service are element-wise equal.
  - G2 identity assertion: enrich the Tracker lane's raw bars via the ONE shared `quant` path (`TechnicalIndicators.add_all` + the same daily `dropna`) BEFORE comparing, then assert enriched-column equality and equal `compute_snapshot_id` across tracker/quant/deep.
  (Equivalently, state explicitly that snapshot-id identity is a quant/deep guarantee and the Tracker lane's guarantee is raw-bar equality — pick the enriched-comparison form unless there's a reason not to.)

**REQ-S2-2 (was MEDIUM-2) — the persisted snapshot must hash the SAME frame the lane hashed.**
`build_and_store_snapshot` must NOT independently refetch with `use_cache=False` and hash a possibly-restated frame. Add an optional `df` parameter; the `snapshot_sink` passes the already-enriched frame so `compute_snapshot_id` runs over identical bytes and the persisted `snapshot_id` equals the lane's id. For the standalone test #5 (no lane in the loop), pin a HISTORICAL `as_of` so the DB-authoritative rule removes recent-window restatement non-determinism, and assert the stored id equals the lane's id over the same frame. Idempotent: same inputs → same id → exactly one row.

**REQ-S2-3 (was MEDIUM-3) — pin the exact snapshot_sink signatures; side-effect only.**
Use these exact signatures (do not improvise):
  - `recommend(ticker, as_of, df=None, snapshot_sink=None)`
  - `analyze(ticker, as_of, portfolio=None, snapshot_sink=None)`
  with `snapshot_sink: Callable[[str, date, pd.DataFrame], None] | None = None` appended LAST. It is invoked AFTER `snapshot_id` is computed and BEFORE return, receives the same enriched `df`, its return value is ignored, and any exception it raises is swallowed-and-logged so a persist failure NEVER changes the returned `Recommendation`. In Step 2 the default `None` is always used by the offline suite — the 39 offline tests MUST stay green (run them 3x to confirm).

**REQ-S2-4 (was NIT-4) — alembic runs async against the live DB.**
State in the runbook/§3.2: `alembic upgrade head` runs in async online mode via the unchanged `env.py` reading `settings.database_url` (asyncpg); the compose stack must be healthy first; the sync URL is used only by the data-service sync facade, never by alembic.

**REQ-S2-5 (was NIT-5) — the four extra Tracker deps are required, not conditional.**
Because Step 2 relocates the whole backend shell (auth + report routers are wired in `main.py`), `email-validator`, `python-jose[cryptography]`, `passlib[bcrypt]`, and `reportlab` are imported at shell-import time and MUST be listed in `requirements.txt`. Keep any import-grep as a confirmation step, not a gate that could drop them.

(NIT-6 needs no action: the 200-calendar-day lookback leaving ~85+ surviving daily bars > 60 preserves existing live-lane behavior; if a specific golden ticker yields <60 surviving bars, raise `lookback_days` for that fetch — never change `SNAPSHOT_TAIL_N`.)
