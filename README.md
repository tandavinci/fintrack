# FinTrack — Unified Local Trading Intelligence Platform

A personal research tool that unifies three projects into one system running **entirely on a local LLM (Ollama)** with **no paid APIs**. Decision support only — **not financial advice, no order execution, paper positions only**.

FinTrack brings together:
- **BSE Stock Investment Tracker** (FastAPI + PostgreSQL + Redis + Celery + React) — the product shell → `backend/` + `frontend/`
- **AI Trading Agent** (yfinance, indicators, regime agent, rule-based strategy, backtest loop) → `quant/`
- **TradingAgents** (LangGraph multi-agent trading team) → `deep/` adapter, pinned read-only submodule

Every engine returns the **same `Recommendation` contract**, and **only the gateway talks to Ollama**.

> New here? Start with the **[User Guide](docs/USER_GUIDE.md)**.

## Current state

A working, tested **vertical slice** is in place and proves the three projects are unified end to end:

| Component | Status |
| --- | --- |
| `gateway/` — quick/deep/embed routes, schema validation + 1 retry, call logging, `num_ctx=8192` hard cap, threading priority lock | ✅ built, live-tested against Ollama |
| `quant/` — migrated indicators, regime, rule-based strategy, backtest; returns `Recommendation(source="quant")` | ✅ built |
| `deep/` — `analyze(ticker, as_of, portfolio)` callable, gateway-routed, sign-preserving number-check; TradingAgents seam marked | ✅ built (adapter + seam; real graph wiring is Step 4) |
| Single `Recommendation` contract imported by every engine | ✅ |
| TradingAgents pinned submodule (`v0.7.0`, SHA `e8f5503`) | ✅ |
| Test suite | ✅ **39 passed**, structured-output validity **20/20 = 100%** |

Measured on the target hardware (RTX 2050, 4 GB VRAM, `llama3.2` 3B, 8k context). See `docs/status.md` for the full week log and `docs/unification-plan.md` for the roadmap.

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
