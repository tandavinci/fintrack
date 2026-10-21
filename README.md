# Unified Local Trading Intelligence Platform

A personal research tool that unifies three projects into one system running entirely on a local LLM (Ollama), with no paid APIs. Decision support only, not financial advice. No order execution — paper positions only.

See `docs/scope.md` for what's in and out, `docs/unification-plan.md` for the step-by-step execution map, and `docs/status.md` for current progress.

## Layout (STEERING §4)

```
backend/            # BSE Tracker: models, schemas, services, routers, repositories, tasks
frontend/           # React 18, TypeScript, Zustand, Tailwind, Vite, Recharts
quant/              # from AI Trading Agent: data, indicators, regime, strategy, backtest
deep/               # adapter around TradingAgents: analyze(ticker, date, portfolio)
gateway/            # LLM gateway: quick, deep, embed routes (only component that talks to Ollama)
third_party/TradingAgents/   # pinned submodule, read-only
infra/              # docker-compose, Ollama route, env examples
docs/               # scope, status, backlog, decisions/, runbook, unification-plan
tests/golden/       # 10 saved BSE tickers for repeatable tests
```

## Non-negotiables (STEERING §3)

No paid APIs. Never edit TradingAgents source. Models explain, code computes. One `Recommendation` schema everywhere. The Tracker keeps running at every step. The deep lane is queued, never in a web request. Log every model call. Paper only.

## Status

Step 0 (Foundations) — skeleton + planning docs in place; baselines, submodule, and golden set pending user confirmation. See `docs/status.md`.
