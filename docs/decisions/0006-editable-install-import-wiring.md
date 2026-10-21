# 0006 — Editable-install import wiring for the shared contract

Date: 2026-10-21
Status: accepted
Gate: Step 5 / FEAT-005

## Context

Every engine in the monorepo (`quant/`, `deep/`, `gateway/`) must import the
ONE `Recommendation` contract — and the contract-adjacent `snapshot.py` /
`fusion.py` helpers — from a single location, `backend/app/schemas/`
(STEERING rule 4: exactly one schema imported everywhere). The packages live in
sibling top-level directories under the repo root, so they are not importable
from one another by relative path alone. We needed a mechanism that makes
`backend`, `quant`, `deep`, and `gateway` resolvable as first-class packages
from any working directory, including the pytest run and in-process callers.

## Decision

Install the repo editable into the project venv:

```powershell
c:\GIT\Fintech\repo\.venv\Scripts\python.exe -m pip install -e .
```

`pyproject.toml` lists the four packages explicitly
(`packages = ["backend", "quant", "deep", "gateway"]`) so setuptools does not
scan `third_party/` (the read-only submodule) or `frontend/`. With the editable
install in place there is exactly ONE import line used everywhere:

```python
from backend.app.schemas.recommendation import Recommendation, Action
```

Re-running `pip install -e .` is only necessary when the `pyproject.toml`
package list changes — ordinary edits to source files are picked up live by the
editable install.

## Consequences

- No `sys.path` hacks, no per-test path shims; imports are identical in tests,
  scripts, and the FastAPI app.
- The contract has a single home; a second definition of `Recommendation`
  cannot silently appear because every lane imports the same module.
- Fresh checkouts must run the editable install once (documented in
  `runbook.md`): submodule init -> venv -> `pip install -r requirements.txt`
  -> `pip install -e .`.
