# Runbook

Operational notes for the unified platform. Expanded through Steps 1-6; this is the Step 0 seed.

## Local toolchain (Windows)

- Python: use the `py` launcher (3.12). `python` / `python3` on PATH are Microsoft Store stubs and will fail — do not use them for host scripts.
- Ollama runs natively on the host at `http://localhost:11434`; containers reach it at `http://host.docker.internal:11434`.
- Set host env before heavy runs: `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`.

## Fresh checkout

Run these in order — the editable install must come last so the four packages
resolve against the freshly installed dependencies:

```powershell
git clone <MONO-remote> repo
cd repo
git submodule update --init --recursive   # pulls pinned TradingAgents
py -m venv .venv                          # py launcher only; never bare python/python3
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .   # makes backend/quant/deep/gateway importable
```

## Shared-import mechanism (the ONE contract)

The monorepo keeps a single `Recommendation` contract and imports it the same
way from every engine (STEERING rule 4). This is made possible by the editable
install of the repo into the venv:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

With that in place there is exactly ONE import line used everywhere
(`quant/`, `deep/`, `gateway/`, tests, and scripts):

```python
from backend.app.schemas.recommendation import Recommendation, Action
```

`pyproject.toml` lists the four packages explicitly
(`packages = ["backend", "quant", "deep", "gateway"]`) so setuptools does not
scan `third_party/` or `frontend/`. Re-running `pip install -e .` is only
needed when that package list in `pyproject.toml` changes — ordinary edits to
source files are picked up live by the editable install. See
`decisions/0006-editable-install-import-wiring.md`.

## Bring up the stack (target, from Step 1 onward)

```powershell
cd infra
docker compose up -d        # postgres+pgvector, redis, gateway, backend, celery workers
```

## Step 2 — data stack

Brings up the pgvector Postgres + Redis 7 data stores (Ollama stays on the host,
decision 0002) and migrates the Step-2 schema. Run from the repo root with the
venv's interpreter.

1. Copy the env template (edit secrets if desired):

   ```powershell
   cp infra/.env.example infra/.env
   ```

2. Bring up the stores and wait for both healthchecks to report healthy:

   ```powershell
   docker compose -f infra/docker-compose.yml up -d
   docker compose -f infra/docker-compose.yml ps   # wait for "healthy"
   ```

3. From the repo root (editable install done, decision 0006) run the
   migrations — creates `price_bars`, `news_items`, `data_snapshots` and enables
   the `vector` extension:

   ```powershell
   .\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade head
   ```

4. Smoke (requires the stack up):

   ```powershell
   .\.venv\Scripts\python.exe -m pytest -m integration -k price_bar
   ```

5. Tear down (add `-v` to also drop the `pg_data` / `redis_data` volumes):

   ```powershell
   docker compose -f infra/docker-compose.yml down
   ```

Offline tests need no stack: `.\.venv\Scripts\python.exe -m pytest -q`.
Integration tests skip-with-reason (never fail) when the stores are unreachable.

## Ollama models

```powershell
ollama list
ollama pull qwen2.5:7b-instruct-q4_K_M   # 8B-class 4-bit candidate (Step 0.5)
```

## Backups / restore (Step 6.4)

Two PowerShell scripts in `infra/` back up and restore the Postgres database.
They run the Postgres container's OWN `pg_dump` / `pg_restore` via `docker exec`
+ `docker cp`, so **no host PostgreSQL client install is needed** and the dump is
always produced/consumed by the matching server version
(`pgvector/pgvector:pg16`). The Docker stack must be up (same as for running the
app). Connection settings come from `infra/.env` when present, else the
docker-compose defaults (user/db `fintrack`); the container is auto-detected
(compose default `fintech-infra-postgres-1`, with a label-based fallback).

Backup (writes a timestamped custom-format dump to `infra/backups/`):

```powershell
.\infra\backup-db.ps1
# or choose a location:
.\infra\backup-db.ps1 -OutDir D:\backups
```

Restore — ONE command (restores the newest `infra/backups/*.dump` by default):

```powershell
.\infra\restore-db.ps1
```

Restore is DESTRUCTIVE: it uses `pg_restore --clean --if-exists`, which DROPS and
recreates objects, overwriting current data. The script asks for a typed `yes`
confirmation first. Skip the prompt for scripted restores with `-Force`, and/or
point at a specific dump with `-InFile`:

```powershell
.\infra\restore-db.ps1 -InFile infra\backups\fintrack-20261027-120000.dump -Force
```

Notes:
- The dump includes the `vector` (pgvector) extension's table data like any
  other data; the extension itself is recreated by the Alembic migrations on a
  fresh DB, or by the dump on an existing one.
- `infra/backups/` is git-ignored (dumps can be large and are local-only).
- Verify a restore with, e.g.:
  `docker exec fintech-infra-postgres-1 psql -U fintrack -d fintrack -c "\dt"`.

## Gate checklist pointer

See `unification-plan.md` for the per-step gate checks (G0-G6) and `status.md` for current progress.
