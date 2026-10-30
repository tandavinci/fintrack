# FinTrack — Project Overview

A single, plain-language picture of what FinTrack is, what works today, what's left, how it's built, how to install and use it, and what it will be able to do when finished.

> **What FinTrack is in one line:** a private, fully-local research tool that tells you *buy / hold / sell* for a BSE/NSE stock — and *why* — using only models and data running on your own machine, with no paid APIs. It is decision support, **not** financial advice. It never places orders.

---

## 1. The idea

Three separate projects were merged into one system:

| Original project | What it did | Where it lives now |
| --- | --- | --- |
| **BSE Stock Investment Tracker** | A full web app (FastAPI + PostgreSQL + Redis + Celery + React) for tracking BSE stocks, portfolio, alerts, an AI advisor/coach. | `backend/` + `frontend/` — the product shell |
| **AI Trading Agent** | Indicators, a market-regime classifier, a rule-based buy/sell strategy, and a backtest loop. | `quant/` — the fast, deterministic lane |
| **TradingAgents** (Tauric Research) | A multi-agent "debate" team that reasons about a stock. | `deep/` + `third_party/TradingAgents` — the heavyweight lane |

They are held together by three rules that make the whole thing trustworthy:

1. **One answer shape.** Every engine returns the *same* `Recommendation` object (ticker, action, confidence, horizon, rationale, evidence, and a `snapshot_id` pointing to the exact data used). No engine invents its own format.
2. **Only the gateway talks to the model.** All LLM calls go through `gateway/`, which runs against a local Ollama model. No OpenAI keys, no cloud, no per-token cost.
3. **Models explain, code computes.** Prices and indicators come from data tools, not the model. If a model's answer cites a number that isn't in the stored data snapshot, the answer is rejected.

---

## 2. What works today (done)

As of the latest push to `https://github.com/tandavinci/fintrack` (branch `main`), Steps 0, 1, and 2 of the 6-step plan are complete and verified.

### ✅ Step 0 — Foundations
- Monorepo created with the target layout; TradingAgents pinned as a read-only submodule (tag `v0.7.0`, SHA `e8f5503`).
- Planning docs, scope, and the golden-ticker set defined.

### ✅ Step 1 — Local LLM gateway (delivered in the vertical slice)
- `gateway/` with `/quick`, `/deep`, `/embed` routes.
- JSON-schema validation with one retry, call logging (model id, prompt hash, tokens, latency, validation result), a priority lock so interactive calls beat background jobs, and a **hard 8192-token context cap** tuned for a 4 GB GPU.
- **Measured: 100% valid structured output (20/20)** against the real local model — clears the G1 ≥95% bar.

### ✅ Step 2 — Data unification
- The full BSE Tracker backend shell migrated into the monorepo (it was missing before).
- A **unified data service**: yfinance with `.BO`/`.NS` resolution → Redis cache → PostgreSQL `price_bars` table. The tracker, quant, and deep lanes all read bars through this one service.
- **News ingest** (RSS, deduplicated) into a `news_items` table, with a terms-of-use note.
- **Persisted data snapshots** (`data_snapshots`) so every recommendation can be traced to the exact prices/indicators/headlines behind it.
- `infra/docker-compose.yml` brings up PostgreSQL (pgvector) + Redis; Alembic migrations create the tables.
- **Gate G2 PASSED on real data for all 10 golden tickers** — the quant, deep, and tracker lanes produce byte-identical price/indicator frames and identical snapshot ids.

### Test status (verified)
- **79 offline tests pass** (run repeatedly, deterministic).
- **17 live integration tests pass** against real Postgres + Redis + yfinance.

---

## 3. The roadmap (G0–G6 COMPLETE)

**Update (2026-10-27): all gates G0–G6 are done.** Steps 3–6 (quant core, deep
engine, product unification, evaluation & hardening) have been built and merged.
Gate G6 closed **GO (with caveats)** — the quant lane modestly beat the
buy-and-hold baseline over the tested window, the system is adopted as a
decision-SUPPORT and monitoring tool (not a proven edge), and llama3.2 is kept
over gemma2:9b on the 4 GB-VRAM machine. See `docs/evaluations/g6-evaluation.md`
and `docs/decisions/0008-g6-evaluation-verdict.md`. Deferred to capable hardware:
the deep lane's multi-ticker backtest/paper book, a ≥4-week paper-trade result,
and the timed nightly-pipeline guarantee.

The original step/gate table (kept for reference):

| Step | What it adds | Gate |
| --- | --- | --- |
| **Step 3 — Quant core** | Persist quant recommendations to a table; a nightly Celery job that generates signals after market close; backtest scoring vs Sensex/Nifty; regime/signal overlays in the React UI (Recharts). | G3: identical backtests on two runs; regime labels for all golden tickers. |
| **Step 4 — Deep engine** | Wire the *real* TradingAgents multi-agent graph into the deep lane (config-only), queued overnight at concurrency 1 with live progress over SSE; number-check enforced; memory moved to PostgreSQL. | G4: 5 tickers end to end in budget, all schema-valid, <5% number-check failures. |
| **Step 5 — Product unification** | Side-by-side quant+deep fusion view with a conflict flag; advisor/coach on the local gateway; alerts wired (AI/risk/news); new UI pages (Signals & Regime, Backtest Lab, Agent Debate viewer, Model Status); retire Chart.js. | G5: a full unattended day — data sync, nightly signals, deep runs, alerts, finished before 09:00 IST. |
| **Step 6 — Evaluation & hardening** | Backtest grid (quant / deep / buy-and-hold) on golden tickers; 4+ weeks of paper trading vs benchmark; compare local models; DB backups + one-command restore. | G6: honest results vs baseline; decide if the system earns a place in real decisions. |

The detailed, task-by-task plan is in `docs/unification-plan.md`; the steering rules for finishing it are in `.kiro/steering/remaining-work.md`.

---

## 4. How it works (architecture)

```
          ┌──────────────────────────────────────────────┐
          │                  frontend/ (React)            │  UI
          └───────────────────────┬──────────────────────┘
                                   │ REST / SSE
          ┌───────────────────────┴──────────────────────┐
          │              backend/ (FastAPI)               │  API + domain services
          │   routers · services · repositories · tasks   │
          └───────┬───────────────────────┬──────────────┘
                  │                        │
         ┌────────┴────────┐      ┌────────┴────────┐
         │     quant/      │      │     deep/       │       Engines
         │ indicators,     │      │ adapter →       │
         │ regime, strategy│      │ TradingAgents   │
         │ (deterministic) │      │ (LLM debate)    │
         └────────┬────────┘      └────────┬────────┘
                  │  both return the SAME Recommendation
                  │                        │
          ┌───────┴────────────────────────┴──────┐
          │              gateway/                  │  ONLY path to the model
          │  /quick /deep /embed · schema-validated│
          └───────────────────┬────────────────────┘
                              │ host.docker.internal:11434
                   ┌──────────┴──────────┐
                   │   Ollama (llama3.2)  │  local model, on the host
                   └─────────────────────┘

   Data & memory:  PostgreSQL (pgvector) · Redis        (via infra/docker-compose.yml)
   Background:     Celery queues (data, insights, quant, deep, alerts)
```

**The two analysis lanes:**
- **Quant (fast):** pulls bars, computes indicators, classifies the market regime, runs a rule-based scorer → a `Recommendation`. Deterministic: same input, same output. No model needed for the default path.
- **Deep (heavy):** builds a prompt from a stored data snapshot, runs an LLM-driven analysis through the gateway, and **rejects any figure not in the snapshot**. The real multi-agent TradingAgents graph plugs in here at Step 4.

When the two lanes disagree, FinTrack keeps **both** and flags the conflict — it never averages them.

---

## 5. How to install

Prerequisites: **Python 3.12** (Windows: the `py` launcher), **Git**, **[Ollama](https://ollama.com)** running locally, **Docker** (for the database/cache), and **Node 18+** (only for frontend work).

```bash
# 1. Clone with the pinned TradingAgents submodule
git clone --recurse-submodules https://github.com/tandavinci/fintrack.git
cd fintrack

# 2. Pull the local model (3B, fits a 4 GB GPU)
ollama pull llama3.2

# 3. Python environment
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install -e .

# 4. Start the data stack (PostgreSQL + Redis) and run migrations
docker compose -f infra/docker-compose.yml up -d
.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade head

# 5. Verify
.venv\Scripts\python.exe -m pytest -q                 # offline suite: 79 passed
.venv\Scripts\python.exe -m pytest -m integration -q  # live suite (needs the stack up)
```

Full step-by-step setup, usage, and troubleshooting live in **`docs/USER_GUIDE.md`**.

---

## 6. What you can do *now* (today's capabilities)

Even before Steps 3-6, with the current build you can:

- **Get a rule-based buy/hold/sell for any BSE/NSE ticker** from the quant lane, with a confidence, a plain-English rationale, and evidence that points back to the exact data used.
  ```python
  from datetime import date
  from quant.recommend import recommend
  rec = recommend("RELIANCE.NS", as_of=date.today())
  print(rec.action, rec.confidence, rec.rationale)
  ```
- **Get an LLM "deep" opinion** through the local model, with the built-in number-check guarding against made-up figures.
  ```python
  from datetime import date
  from deep.adapter import analyze
  rec = analyze("TCS.BO", as_of=date.today(), portfolio=None)
  ```
- **Fetch and cache real market data** through the unified service (Redis + PostgreSQL), and have it stored point-in-time.
- **Compute indicators and classify the market regime** (bullish / bearish / sideways / high-volatility / breakout).
- **Call the local model directly** via the gateway (`/quick`, `/deep`, `/embed`) with schema-validated output.
- **Fuse both lanes** and see a conflict flag when they disagree.
- **Run the golden-ticker tests** to confirm the data pipeline is reproducible on your machine.

What you **cannot** do yet (comes with later steps): scheduled nightly signals, the full multi-agent debate, the web UI pages, alerts delivery, and the evaluation/backtest-grid reporting.

---

## 7. What you'll be able to do when it's finished

- Wake up to **nightly recommendations** for your holdings and watchlist, generated automatically after market close and ready before the market opens.
- See **quant and deep opinions side by side**, with conflicts flagged, inside a web dashboard.
- Watch the **multi-agent debate** reason through a stock, and inspect the data snapshot behind every number.
- Get **alerts** — breakout patterns, portfolio risk, relevant news — wired to your actual holdings.
- Run a **backtest grid** comparing the quant lane, the deep lane, and plain buy-and-hold against the Sensex/Nifty, and **paper-trade** to see whether the system actually adds value — reported honestly, so you can decide if it earns a place in your decisions.
- All of it **local, private, and free to run** (no API bills), on modest hardware.

---

## 8. Important disclaimer

FinTrack is a personal research and monitoring tool. It does not execute trades, it is not financial advice, and its signals are not a proven edge. Paper positions only. If you ever plan to share it with others, review SEBI rules first.

---

## 9. Where to find things

| You want… | Look at |
| --- | --- |
| Set up and use the tool | `docs/USER_GUIDE.md` |
| The big picture (this doc) | `docs/PROJECT_OVERVIEW.md` |
| What's in / out of scope | `docs/scope.md` |
| The full step-by-step roadmap | `docs/unification-plan.md` |
| Rules for finishing the build | `.kiro/steering/remaining-work.md` |
| Progress log, week by week | `docs/status.md` |
| Why a major decision was made | `docs/decisions/0001…0007` |
| Operational commands | `docs/runbook.md` |
| News-source terms of use | `docs/news-sources.md` |
