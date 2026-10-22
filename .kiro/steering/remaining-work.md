# STEERING: FinTrack — remaining work (Steps 3-6)

This steering file governs the **rest** of the FinTrack build. Steps 0, 1, and 2 are done and pushed (see `docs/status.md` and `docs/PROJECT_OVERVIEW.md`). Read this fully before starting any new step. Work one step at a time, in order. Do not start a step until the previous gate has passed. It complements — does not replace — `docs/unification-plan.md` (the detailed step map) and the original mission steering embedded there.

## Ground rules (unchanged, still non-negotiable)

1. No paid APIs, no OpenAI/Anthropic keys. Every model call goes through `gateway/`; only the gateway talks to Ollama.
2. Never edit `third_party/TradingAgents` source — config and the `deep/` adapter only.
3. Models explain, code computes. Any number not in the stored data snapshot invalidates the answer.
4. One `Recommendation` contract (`backend/app/schemas/recommendation.py`) and one `compute_snapshot_id` (`backend/app/schemas/snapshot.py`) — import, never fork.
5. The BSE Tracker keeps running at every step. No big-bang rewrite of a working module.
6. The deep lane is never run inside a web request — it is a queued Celery job, progress over SSE.
7. Point-in-time snapshots behind every signal (already persisted to `data_snapshots`).
8. Log every model call (the gateway already does: model id, prompt hash, tokens, latency, validation result).
9. No order execution. Paper positions only.
10. New ideas go to `docs/backlog.md`; they enter the plan only if something of equal size leaves it.

## Confirmed environment (do not re-derive)

- Windows, PowerShell, Python 3.12 via the `py` launcher (`python`/`python3` on PATH are broken Store stubs). Project venv: `c:\GIT\Fintech\repo\.venv`, python `.venv\Scripts\python.exe`.
- GPU: RTX 2050, **4 GB VRAM**, 31.5 GB RAM (NOT the 8 GB machine the original mission text assumed — see ADR 0004).
- Model: **`llama3.2` (3B, 4-bit)** for both quick and deep tiers. The gateway hard-caps `num_ctx=8192`. Do NOT pull an 8B model or assume 8 GB VRAM.
- Ollama runs on the HOST (`http://localhost:11434`); containers reach it at `host.docker.internal:11434` (ADR 0002). Infra (Postgres pgvector + Redis) is `infra/docker-compose.yml`.
- Migrations: `alembic upgrade head` runs async via `backend/alembic`; the compose stack must be healthy first.

## How to run every remaining step (the working agreement)

1. **State the step and task** at the start.
2. **Delegate substantive work to a workflow** (design → plan → implement → code-review), not inline edits. Investigations/analysis may use the fast path. Small mechanical doc/config edits may be done inline.
3. **Real infra, real data.** Tests that touch the DB, Redis, yfinance, or Ollama run against the live stack and are marked `@pytest.mark.integration` (skip-with-reason when infra is absent). The offline suite (currently 79 tests) must stay green and fast, and is run 3x for stability before any push.
4. **One task, one commit,** message `step-N: short description`. After the code review APPROVES and tests pass, **push to `origin/main`** (user authorized per-step push). No force-push, no PR, no git-config changes, submodule gitlink untouched.
5. **Write/update a test first** when a task touches a boundary (gateway, adapter, schema, data service, a new Celery task, a new API route).
6. If a model returns invalid JSON or altered numbers, fix the prompt or validation — never loosen the schema.
7. Record results in `docs/status.md` (weekly template) and a go/fix/stop note in `docs/decisions/` at each gate. Keep `docs/PROJECT_OVERVIEW.md` current.

## Remaining steps and their gates

### Step 3 — Quant core productization (NEXT)
The quant engine and its `Recommendation` output already exist; this step makes it a first-class, persisted, scheduled, visible product feature.
- Persist quant `Recommendation`s to a DB table (reuse the contract; add a `recommendations` table + Alembic migration). Store the `snapshot_id` link to `data_snapshots`.
- A nightly fast-lane Celery job (queue `quant`) that runs after market close over the holdings + watchlist and writes recommendations.
- Score backtests against a Sensex/Nifty benchmark, by rating, the same way TradingAgents scores.
- React UI: regime + signal overlays with **Recharts** (not Chart.js).
**Gate G3:** the same backtest run twice gives identical results (already true for the rule-based path — prove it with a persisted-run test), and regime labels appear for all 10 golden tickers.

### Step 4 — Deep engine (real TradingAgents)
- Replace the deep adapter's marked seam with the real TradingAgents graph, wired **by config only**: `llm_provider="openai_compatible"`, `backend_url` = the gateway, `max_debate_rounds=1`, risk rounds 1, analysts `market,news` first, `checkpoint_enabled=True`, low temperature.
- Celery queue `deep` at concurrency 1, overnight window; progress streamed over SSE. Never in a web request.
- Measure one ticker at 1 debate round + 2 analysts on the 4 GB / `llama3.2` stack; set the nightly ticker count from that. If too slow even at the minimum, fall back to weekly runs on holdings only (stop/pivot rule).
- Move the memory log/lessons to PostgreSQL; keep the markdown as a backup export.
- Keep the number-check (already sign-preserving); reject reports whose figures aren't in the snapshot.
**Gate G4:** 5 tickers end to end within the measured time budget, all schema-valid, number-check failures under 5%.
**Watch-out:** on 4 GB VRAM the deep lane is the real performance risk. Measure before scaling; honor the stop/pivot rule rather than forcing it.

### Step 5 — Product unification
- Recommendation fusion view: quant and deep side by side with a conflict flag (reuse `backend/app/schemas/fusion.py` — store both, never average).
- Advisor chat reads stored recommendations + holdings; coach unchanged except it now uses the gateway.
- Wire alerts: AI alerts from quant patterns, risk alerts from portfolio volatility, news alerts from `news_items`.
- Pages: Signals & Regime, Backtest Lab, Agent Debate viewer, Model Status. **Retire Chart.js** (the AI Trading Agent's old frontend).
**Gate G5:** a full day runs unattended — data sync, nightly signals, deep runs on holdings, alerts delivered, nightly pipeline finishes before 09:00 IST (BSE opens 09:15).

### Step 6 — Evaluation and hardening
- Backtest grid on the 10 golden tickers (weekly dates) for quant, deep, and a buy-and-hold baseline.
- Paper-trade at least 4 weeks vs the benchmark.
- Compare 2-3 local models with the qualification scorecard; keep the best (within the 4 GB budget).
- PostgreSQL backups + a one-command restore; finish README + runbook.
**Gate G6:** results reviewed honestly vs the baseline; decide whether the system earns a place in real decisions.

## Measures (check at every gate)

| Measure | Target |
| --- | --- |
| Paid API cost | 0 |
| Valid structured output | ≥95% (currently 100% at G1) |
| Numbers not in the snapshot | < 5% of reports |
| Backtest reproducibility | identical on two runs |
| Nightly pipeline finish | before 09:00 IST |
| Offline test suite | stays green, run 3x before push |

## Stop and pivot rules

- Deep run too slow even at 1 round / 2 analysts → move the deep lane to weekly, holdings only.
- After 4 weeks of paper trading neither lane beats buy-and-hold → keep the system as an explanation/monitoring tool, stop treating signals as an edge.
- A TradingAgents upgrade breaks the adapter → stay on the pinned SHA (`e8f5503`) until the adapter tests pass again.
- Want to share this with others → stop and get advice on SEBI rules first.
