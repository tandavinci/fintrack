# 0001 — Monorepo strategy

Date: 2026-10-21
Status: accepted — MONO = c:\GIT\Fintech\repo
Gate: Step 0

## Context

Three separate git repos live under `c:\GIT\Fintech\`:
- `Tracker-Tool` (BSE Tracker, remote tandavinci/Tracker-Tool) — already has the target `backend/app/{...}` layout, docker-compose, specs.
- `Not-a-Finance-advice-` (AI Trading Agent, remote tandavinci/Not-a-Finance-advice-).
- `agents` (Polymarket Agents — unrelated, not part of the unification).

`c:\GIT\Fintech` itself is not a git repo. STEERING §4 wants a single `repo/` layout.

## Decision

Create a new git repo at `c:\GIT\Fintech\repo` (MONO) with the STEERING §4 layout.
- BSE Tracker → `MONO/backend` + `MONO/frontend`.
- AI Trading Agent → `MONO/quant` (FastAPI + CLI dropped in Step 3; Chart.js frontend retired in Step 5).
- TradingAgents → `MONO/third_party/TradingAgents` pinned submodule (read-only).

Migration is staged (copy working trees under git, keep originals as untouched backups until each gate passes), never big-bang (rule 5). Record source commit SHAs in a decision note at migration time.

## Alternatives considered

- Promote `Tracker-Tool` to the root and fold others in: rejected — entangles the Tracker's git history with the monorepo and makes the submodule layout awkward.
- `git subtree` to preserve full history: deferred (backlog B-003) — not needed for a personal tool; adds complexity.

## Consequences

- The Tracker keeps running from its original folder until `MONO/backend` is proven at each gate.
- Need to confirm with the user: exact MONO path, and whether to keep the two source repos as remotes/backups after migration.
