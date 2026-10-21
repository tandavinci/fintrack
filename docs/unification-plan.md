# Unification Plan — from three repos to one local system

This is the execution map from STEERING.md, grounded in the verified state of the three repos (see `scope.md`). It expands each step into concrete, ordered actions with the exact source and target paths, and the gate check for each. Work one step at a time; do not start a step until the previous gate passes. One task = one commit, message `step-N: short description`.

Legend for paths:
- SRC-T = `c:\GIT\Fintech\Tracker-Tool`
- SRC-Q = `c:\GIT\Fintech\Not-a-Finance-advice-`
- MONO  = `c:\GIT\Fintech\repo` (the unified monorepo root)

## Monorepo strategy (decided — see decisions/0001)

- MONO is a new git repo. Target layout per STEERING §4.
- BSE Tracker becomes `MONO/backend` + `MONO/frontend` (it already has the exact backend layout the target wants).
- AI Trading Agent becomes `MONO/quant` (its `backend/{data,agents,backtesting}` moved in, FastAPI + `run.py` CLI dropped in Step 3, Chart.js frontend retired in Step 5).
- TradingAgents is a pinned git submodule at `MONO/third_party/TradingAgents` — read-only.
- Migration is staged, never big-bang (rule 5). The Tracker keeps running at every step. Code is brought in by copying working trees into MONO under git, preserving the originals as untouched backups until each gate passes. History-preserving `git subtree` is an option but not required for a personal tool; default to a clean copy + a decision note recording the source commit SHAs.

## Step 0 — Foundations (weeks 1-2)   [IN PROGRESS]

0.1 Create MONO layout. **(done — skeleton dirs created)**
0.2 `git init` MONO; add `.gitignore`; first commit of skeleton + docs.
0.3 Add TradingAgents as pinned submodule:
    `git -C MONO submodule add https://github.com/TauricResearch/TradingAgents third_party/TradingAgents`
    then pin: `git -C MONO/third_party/TradingAgents checkout <release-tag-or-SHA>` and commit the gitlink. Record the pinned SHA in decisions/0003.
0.4 Baselines — run each project alone, record startup + one analysis + one backtest in `status.md`:
    - Tracker: `cd SRC-T; docker compose up` (postgres+redis+backend), hit `/docs`, run one advisor query. Record OpenAI dependency.
    - AI Trading Agent: `cd SRC-Q; py -m pip install -r backend/requirements.txt; py run.py backtest --ticker RELIANCE.BO --lookback 365 --horizon 10` and `py run.py predict --ticker TCS.NS`.
    - TradingAgents: follow its README to run one analysis via the gateway stub (or OpenAI-compat mock) — defer the real run to Step 4 if it needs the gateway; record that it starts.
0.5 Ollama model: pull an 8B-class 4-bit model, record tokens/sec + VRAM at 8k and 16k context. Candidate: `qwen2.5:7b-instruct-q4_K_M`.
0.6 Write `scope.md` (done) — in/out, no live trading, no multi-user, no mobile, no paid data.
0.7 Golden set: pick 10 BSE tickers (large caps, mid caps, 2 volatile), save price data to `MONO/tests/golden/`, and write a test that loads them. Proposed list in `tests/golden/README` — needs user confirmation of exact tickers.

**Gate G0 check:** all three baselines recorded in status.md; golden data loads in a test. Paid-API cost = 0 recorded.

## Step 1 — Local LLM gateway (weeks 3-4)

1.1 `infra/docker-compose.yml`: add Ollama (host route `host.docker.internal:11434` on Windows, or a GPU-enabled service on Linux/WSL2). Merge the Tracker's existing compose services here; swap postgres:16-alpine → a pgvector-enabled image (e.g. `pgvector/pgvector:pg16`) ahead of Step 2.
1.2 Build `gateway/` (FastAPI): routes `quick`, `deep`, `embed`. JSON-schema validation, one retry on invalid output, call logging (model id, prompt hash, tokens, latency, validation result — rule 8), and a priority lock so interactive chat preempts queued deep jobs.
1.3 Replace every OpenAI call in `backend/` (advisor, coach, RAG) with gateway calls. Embeddings run locally on CPU into pgvector. Grep targets in SRC-T: search `openai`, `OPENAI_API_KEY`, `gpt-4` across `backend/app/services` and `backend/app/utils`.
1.4 Model qualification harness: JSON validity over 100 runs, tool calling, 16k context, numbers preserved from a verified snapshot. Print a scorecard into `docs/`.
1.5 One model for both quick and deep tiers.

**Gate G1:** advisor + coach answer 20 saved questions on the local model, ≥95% valid structured output, no OPENAI key set. Set the deep-analysis time budget from measured speed.

## Step 2 — Data unification (weeks 5-6)

2.1 One data service wrapping yfinance with `.BO`/`.NS` resolution, Redis cache, and a PostgreSQL price-bar table. Source: SRC-Q `backend/data/fetcher.py` + `indicators.py` + `pipeline.py`, reconciled with the Tracker's existing price fetching.
2.2 News/announcements ingest from RSS + BSE announcements into one deduplicated table. Check each source's terms of use first.
2.3 Data snapshot object: exact prices, indicators, headlines given to any engine, stored with an id (`snapshot_id` in the Recommendation contract).

**Gate G2:** for every golden ticker, tracker + quant core + deep engine read identical prices and indicators.

## Step 3 — Quant core (weeks 7-9)

3.1 Move data slicing, indicators, regime agent, rule-based strategy, backtest engine into `quant/`. Remove the FastAPI layer and `run.py` CLI from the moved code.
3.2 Add the `Recommendation` model + tables + a nightly fast-lane Celery job (queue `quant`) after market close.
3.3 Score backtests vs Sensex/Nifty benchmark, by rating, the same way TradingAgents scores.
3.4 Show regime + signal overlays in the React UI with Recharts (not Chart.js).

**Gate G3:** same backtest run twice = identical results; regime labels appear for all golden tickers.

## Step 4 — Deep engine (weeks 10-13)

4.1 `deep/analyze(ticker, date, portfolio)` builds `PortfolioContext` from holdings, returns a `Recommendation`.
4.2 Configure TradingAgents by config ONLY: `llm_provider="openai_compatible"`, `backend_url`=gateway, `max_debate_rounds=1`, risk rounds 1, analysts `market,news` first, `checkpoint_enabled=True`, low temperature.
4.3 Celery queue `deep` concurrency 1, overnight window; progress streamed over SSE. Deep lane NEVER runs inside a web request (rule 6).
4.4 Measure one ticker @ 1 debate round, 2 analysts; set nightly ticker count from that. Nightly scope: holdings + ~5 watchlist top picks.
4.5 Move memory log + lessons from markdown to PostgreSQL; keep the markdown as a backup export.
4.6 Number-check: reject any report whose figures are not in the data snapshot (rule 3).
4.7 Only after baseline works, try a larger/MoE model that spills into RAM, for overnight runs.

**Gate G4:** 5 tickers end to end within the time budget, all schema-valid, number-check failures < 5%.

## Step 5 — Product unification (weeks 14-15)

5.1 Recommendation fusion view: quant + deep side by side with a conflict flag (store both on disagreement, never average — §5).
5.2 Advisor chat reads stored recommendations + holdings; coach unchanged except the gateway.
5.3 Wire alerts: AI alerts from quant patterns, risk alerts from portfolio volatility, news alerts from the ingest table. (Tracker already has an alerts model.)
5.4 Pages: Signals & Regime, Backtest Lab, Agent Debate viewer, Model Status. Retire Chart.js.

**Gate G5:** a full day runs unattended: data sync, nightly signals, deep runs on holdings, alerts delivered, nightly pipeline finishes before 09:00 IST.

## Step 6 — Evaluation & hardening (weeks 16-18, then ongoing)

6.1 Backtest grid on golden tickers, weekly dates, for quant / deep / buy-and-hold.
6.2 Paper-trade ≥4 weeks vs benchmark.
6.3 Compare 2-3 local models with the scorecard; keep the best.
6.4 PostgreSQL backups, one-command restore, README + `docs/runbook.md`.

**Gate G6:** results reviewed honestly vs baseline; decide whether the system earns a place in decisions.

## Cross-cutting invariants (apply to every step)

- Every engine returns the single `Recommendation` schema (§5). No engine-specific shapes.
- Models explain, code computes. Any number not in the stored snapshot invalidates the answer.
- The gateway is the only path to Ollama. One model loaded, one request at a time.
- TradingAgents source is never edited.
- Log every model call. No paid APIs, ever.
- New ideas go to `docs/backlog.md`; they only enter the plan if something of equal size leaves it.
