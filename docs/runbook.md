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

## Ollama models

```powershell
ollama list
ollama pull qwen2.5:7b-instruct-q4_K_M   # 8B-class 4-bit candidate (Step 0.5)
```

## Backups / restore (built in Step 6)

- PostgreSQL backup + one-command restore — TODO Step 6.4.

## Gate checklist pointer

See `unification-plan.md` for the per-step gate checks (G0-G6) and `status.md` for current progress.
