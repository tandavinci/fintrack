# Scope — Unified Local Trading Intelligence Platform

Status: Step 0 (Foundations). Owner: solo developer. Last updated: 2026-10-21.

Personal research tool. Decision support only, not financial advice. No order execution, paper positions only.

## In scope

- One system unifying three projects, running entirely on a local LLM (Ollama) with no paid APIs.
- Primary market: BSE/NSE tickers (`.BO`, `.NS`). US tickers optional.
- Single-user, local, self-hosted.
- Backend: FastAPI + PostgreSQL (with pgvector) + Redis + Celery (from the BSE Tracker shell).
- `quant/`: data slicing, indicators, regime agent, rule-based strategy, backtest engine (from the AI Trading Agent).
- `deep/`: an adapter around TradingAgents (pinned, read-only), queued and overnight only.
- `gateway/`: the only component that talks to Ollama; routes `quick`, `deep`, `embed`.
- One `Recommendation` schema shared by every engine.

## Out of scope (explicit non-goals)

- No live trading / no order execution. Paper positions only.
- No paid APIs, no OpenAI/Anthropic keys, no paid data feeds.
- No multi-user, no auth beyond the existing single-user shell.
- No mobile app.
- No editing of TradingAgents source — config and adapter only.
- No sharing/distribution without first getting advice on SEBI rules (stop-and-pivot rule).

## Confirmed environment (verified 2026-10-21)

| Fact | Value | How verified |
| --- | --- | --- |
| OS | Windows (win32), PowerShell | session context |
| Git | 2.51.0.windows | `git --version` |
| Python | 3.12.0 via `py` launcher | `py -0p` — `python`/`python3` on PATH are Microsoft Store stubs and do NOT work |
| Node | v24.15.0 | `node --version` |
| Docker | 29.6.2 | `docker --version` |
| Ollama | 0.40.6; `llama3.2:latest` (3B, ~2 GB) pulled | `ollama --version`, `ollama list` |
| GPU | **NVIDIA RTX 2050, 4 GB VRAM** (+ Intel iGPU) | `nvidia-smi` |
| RAM | **31.5 GB** | `Win32_ComputerSystem` |
| CPU | **Intel Core Ultra 5 135H** (laptop) | `Win32_Processor` |

> NOTE: STEERING §2 lists different "fixed facts" (Ryzen 5800X, RTX 4060 Ti 8 GB, 48 GB RAM). The real machine is weaker. The plan below is adapted to the measured hardware (decision 0004). All model/context choices use the measured numbers, not the steering spec.

### Measured model performance (2026-10-21, `llama3.2` 3B 4-bit)

| Context | tokens/sec | GPU/CPU split | VRAM used | Note |
| --- | --- | --- | --- | --- |
| 131072 (Ollama default) | 9.4 | 10% / 90% | spilled to RAM (18 GB) | too large — do NOT use |
| 8192 | 27 | 67% / 33% | ~2.3 GB | fits, fast — **use 8k cap** |

Conclusion: cap `num_ctx` at 8192. An 8B model (~5 GB 4-bit) does not fit 4 GB VRAM and would run mostly on CPU; stay on a 3B-class model for interactive (quick) and deep tiers on this machine.

### Environment decisions / open items

- **Docker route on Windows:** run Ollama natively on the host (already installed) and have containers reach it at `host.docker.internal:11434`. This avoids GPU passthrough into containers. Confirm vs WSL2 at Step 1. (See decisions/0002.)
- **Model gap:** steering wants an 8B-class 4-bit model; only `llama3.2` (~3B) is present. Pull an 8B-class 4-bit model (e.g. `qwen2.5:7b-instruct-q4_K_M` or `llama3.1:8b-instruct-q4_K_M`) in Step 0 task 3 and measure tokens/sec + VRAM at 8k and 16k context before Step 1. 8 GB VRAM is tight for 8B at 16k; measure before committing.
- **Python on PATH:** use `py` (3.12) for all local Python commands. Inside Docker the image controls the interpreter, so this only affects host-side scripts and the golden-set tests.

## The three source projects (verified)

1. **BSE Stock Investment Tracker** — `c:\GIT\Fintech\Tracker-Tool` (git `main`, remote `tandavinci/Tracker-Tool`).
   The product shell. Already has `backend/app/{models,schemas,services,routers,repositories,tasks,utils}`, `alembic/`, `docker-compose.yml` (postgres:16, redis:7, backend, celery worker+beat), `.kiro/specs/`, React/TS/Zustand/Tailwind/Vite/Recharts frontend. Uses OpenAI GPT-4 today (to be replaced by the gateway in Step 1).
2. **AI Trading Agent** — `c:\GIT\Fintech\Not-a-Finance-advice-` (git `main`, remote `tandavinci/Not-a-Finance-advice-`).
   Becomes `quant/`. Has `backend/{data,agents,backtesting}`, a FastAPI layer + `run.py` CLI (both removed in Step 3), and a Chart.js frontend (retired in Step 5). yfinance + rule-based strategy + optional GPT-4o-mini.
3. **TradingAgents** — NOT present in the workspace. `c:\GIT\Fintech\agents` is **Polymarket Agents** (remote `Polymarket/agents`), a different project. TradingAgents (`TauricResearch/TradingAgents`, LangGraph multi-agent team) must be added as a pinned submodule under `third_party/TradingAgents/` in Step 0.
