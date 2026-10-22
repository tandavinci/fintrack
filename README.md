# FinTrack — Unified Local Trading Intelligence Platform

A personal research tool that unifies three projects into one system running **entirely on a local LLM (Ollama)** with **no paid APIs**. Decision support only — **not financial advice, no order execution, paper positions only**.

FinTrack brings together:
- **BSE Stock Investment Tracker** (FastAPI + PostgreSQL + Redis + Celery + React) — the product shell → `backend/` + `frontend/`
- **AI Trading Agent** (yfinance, indicators, regime agent, rule-based strategy, backtest loop) → `quant/`
- **TradingAgents** (LangGraph multi-agent trading team) → `deep/` adapter, pinned read-only submodule

Every engine returns the **same `Recommendation` contract**, and **only the gateway talks to Ollama**.

> New here? Start with the **[Project Overview](docs/PROJECT_OVERVIEW.md)**, then the **[User Guide](docs/USER_GUIDE.md)**.

## Current state

**Steps 0, 1, and 2 of the 6-step plan are complete and pushed.** The three projects are unified end to end, with a unified data layer on real PostgreSQL + Redis.

| Component | Status |
| --- | --- |
| `gateway/` — quick/deep/embed routes, schema validation + 1 retry, call logging, `num_ctx=8192` hard cap, threading priority lock | ✅ built, live-tested against Ollama |
| `quant/` — indicators, regime, rule-based strategy, backtest; returns `Recommendation(source="quant")` | ✅ built |
| `deep/` — `analyze(ticker, as_of, portfolio)` callable, gateway-routed, sign-preserving number-check; TradingAgents seam marked | ✅ built (adapter + seam; real graph wiring is Step 4) |
| `backend/` — full BSE Tracker shell + unified `MarketDataService` (yfinance → Redis → PostgreSQL), news ingest, persisted snapshots | ✅ built |
| `infra/` — docker-compose: PostgreSQL (pgvector) + Redis; Alembic migrations (rev 002) | ✅ built |
| Single `Recommendation` contract + one `compute_snapshot_id`, imported by every engine | ✅ |
| TradingAgents pinned submodule (`v0.7.0`, SHA `e8f5503`) | ✅ |
| Test suite | ✅ **79 offline + 17 live integration passed**; structured-output validity **20/20 = 100%**; **Gate G2 passed on real data for all 10 golden tickers** |

Measured on the target hardware (RTX 2050, 4 GB VRAM, `llama3.2` 3B, 8k context).

**Still to come:** Step 3 (quant tables + nightly signals + UI overlays), Step 4 (real TradingAgents graph + overnight deep queue), Step 5 (fusion view, alerts, UI pages), Step 6 (evaluation, paper trading, backups). See `docs/PROJECT_OVERVIEW.md` for the full picture and `docs/unification-plan.md` for the task-level roadmap.

## Layout

```
backend/            # BSE Tracker: models, schemas (incl. the Recommendation contract), services, routers, repositories, tasks
frontend/           # React 18, TypeScript, Zustand, Tailwind, Vite, Recharts
quant/              # data, indicators, regime, rule-based strategy, backtest
deep/               # adapter around TradingAgents: analyze(ticker, as_of, portfolio)
gateway/            # LLM gateway: /quick /deep /embed — the ONLY component that talks to Ollama
third_party/TradingAgents/   # pinned submodule, read-only (never edited)
infra/              # docker-compose, Ollama route, env examples
docs/               # USER_GUIDE, scope, status, backlog, decisions/, runbook, unification-plan
tests/              # contract, quant, deep, gateway (live), integration, validity-rate
```

## Quick start

Requires: Python 3.12 (Windows: use the `py` launcher), Node 18+, and [Ollama](https://ollama.com) running locally with a model pulled (`ollama pull llama3.2`).

```bash
git clone --recurse-submodules https://github.com/tandavinci/fintrack.git
cd fintrack
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m pytest -q
```

Full setup, usage, and troubleshooting: **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)**.

## Non-negotiables

No paid APIs. Never edit TradingAgents source. Models explain, code computes (any number not in the data snapshot invalidates the answer). One `Recommendation` schema everywhere. The Tracker keeps running at every step. The deep lane is a queued callable, never inside a web request. Log every model call. Paper positions only.

## License

Private — personal research tool. Not investment advice. If you ever intend to share this, review SEBI rules first.
