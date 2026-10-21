# FinTrack User Guide

A practical, start-to-finish guide to setting up and using the Unified Local Trading Intelligence Platform. This is a **personal research / decision-support tool**. It does **not** place orders, and nothing here is financial advice.

---

## 1. What FinTrack is

FinTrack answers one question for a BSE/NSE stock: **buy, hold, or sell — and why**, using only models and data running on your own machine. It never calls a paid API.

It has two analysis "lanes" that both return the exact same `Recommendation` shape:

- **Quant lane** (`quant/`) — fast, rule-based, deterministic. Computes indicators (SMA, EMA, RSI, MACD, Bollinger, ATR, volume), classifies the market regime, and produces a BUY/HOLD/SELL with a confidence and plain-English reasoning. No LLM needed for the default path.
- **Deep lane** (`deep/`) — an LLM-driven analysis routed through a local model, with a built-in **number-check** that rejects any figure the model "cites" that isn't actually in the data. The heavyweight multi-agent engine (TradingAgents) plugs in here.

When the two lanes disagree, FinTrack **keeps both and flags the conflict** — it never averages them.

Everything that talks to the language model goes through one place: the **gateway** (`gateway/`). That is the only component allowed to reach Ollama.

---

## 2. Before you start (prerequisites)

| Requirement | Notes |
| --- | --- |
| **Python 3.12** | On Windows use the `py` launcher. `python`/`python3` on the PATH may be broken Microsoft Store stubs — prefer `py`. |
| **Git** | With submodule support (any recent git). |
| **Ollama** | Install from https://ollama.com and make sure it runs at `http://localhost:11434`. |
| **A local model** | `ollama pull llama3.2` (3B, ~2 GB — fits a 4 GB GPU). |
| **Node 18+** | Only needed when you work on the `frontend/`. |
| **Docker** (optional) | For running PostgreSQL + Redis + the stack via `infra/` in later steps. |

### Hardware reality

The platform is tuned for a modest machine (measured on an RTX 2050 with **4 GB VRAM**). Two things follow from that and are baked in:

- The model is **`llama3.2` (3B, 4-bit)** for both the quick and deep tiers. An 8B model does not fit 4 GB of VRAM.
- The gateway **hard-caps the context window at 8192 tokens**. At Ollama's default (very large) context, the model spills to the CPU and slows to a crawl; at 8k it stays mostly on the GPU.

If you move to a bigger GPU later, see `docs/decisions/0004-hardware-adapted-model-strategy.md` before changing the model.

---

## 3. Install

```bash
# 1. Clone with the pinned TradingAgents submodule
git clone --recurse-submodules https://github.com/tandavinci/fintrack.git
cd fintrack

# If you already cloned without --recurse-submodules:
git submodule update --init --recursive

# 2. Create a virtual environment (Windows / py launcher)
py -m venv .venv

# 3. Install dependencies into the venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 4. Install the repo itself (editable) so every engine shares ONE Recommendation contract
.venv\Scripts\python.exe -m pip install -e .
```

> **Why the editable install?** `quant/` and `deep/` both import the single contract as
> `from backend.app.schemas.recommendation import Recommendation`. The `pip install -e .`
> puts the repo root on the path so there is exactly one definition, imported everywhere.
> (See `docs/decisions/0006`.)

On macOS/Linux the venv python is `.venv/bin/python` instead of `.venv\Scripts\python.exe`.

---

## 4. Verify it works

Start Ollama and confirm the model is present:

```bash
ollama list          # should show llama3.2
```

Run the full test suite:

```bash
.venv\Scripts\python.exe -m pytest -q
```

Expected: **39 passed**. The suite includes:
- the `Recommendation` contract (validation + JSON round-trip),
- quant indicators, regime classification, and reproducibility,
- the deep adapter and its number-check (including a signed-number regression),
- a **live gateway test against your real Ollama** (skips cleanly if Ollama is down),
- an integration test proving both lanes share one data snapshot and the conflict flag works,
- a **validity-rate harness** asserting ≥95% valid structured output (measured 20/20 = 100%).

If the live gateway tests **skip**, Ollama isn't reachable — start it and re-run.

---

## 5. Using the engines

### 5.1 The gateway (the only path to the model)

Run it as a service:

```bash
.venv\Scripts\python.exe -m uvicorn gateway.main:app --port 8001
```

Routes:
- `POST /quick` — interactive, low-latency prompts (advisor/coach style).
- `POST /deep` — the tier the deep engine uses.
- `POST /embed` — embeddings (local; falls back to a deterministic hash embedding offline).

Every call is logged to `gateway/logs/calls.jsonl` with the model id, a prompt hash, token counts, latency, and whether the output validated. Each request is capped at `num_ctx=8192`.

You can also call the gateway **in-process** (no server) from Python:

```python
from gateway.client import generate_sync
text = generate_sync(route="quick", prompt="In one line, what is RSI?")
```

### 5.2 The quant lane

```python
from datetime import date
from quant.recommend import recommend   # returns a Recommendation(source="quant")

rec = recommend("RELIANCE.BO", as_of=date.today())
print(rec.action, rec.confidence, rec.rationale)
print(rec.evidence)        # references into the data snapshot
print(rec.snapshot_id)     # the exact data behind the signal
```

The quant path is **rule-based and deterministic** — the same input produces the same output (a reproducibility test enforces this).

### 5.3 The deep lane

```python
from datetime import date
from deep.adapter import analyze   # returns a Recommendation(source="deep")

rec = analyze("TCS.NS", as_of=date.today(), portfolio=None)
print(rec.action, rec.rationale, rec.model_id)   # model_id = the local model, e.g. "llama3.2"
```

If the model's rationale contains a number that isn't in the data snapshot (within tolerance), the adapter raises a number-check error rather than returning a misleading report. This enforces the "models explain, code computes" rule.

### 5.4 Fusing both lanes

```python
from backend.app.schemas.fusion import fuse
fused = fuse(quant_rec, deep_rec)   # keeps BOTH; sets conflict=True if actions differ
```

---

## 6. The `Recommendation` contract

Every engine returns this exact shape (defined once in `backend/app/schemas/recommendation.py`):

| Field | Meaning |
| --- | --- |
| `ticker` | e.g. `RELIANCE.BO`, `TCS.NS` |
| `as_of` | the date the recommendation is for |
| `action` | `BUY` / `HOLD` / `SELL` |
| `confidence` | 0.0–1.0 |
| `horizon_days` | holding horizon in days |
| `rationale` | plain-English reasoning |
| `evidence` | references into the data snapshot |
| `source` | `quant` / `deep` / `advisor` |
| `model_id` | the model used (`None` for rule-based quant) |
| `prompt_version` | prompt version for the LLM lanes |
| `snapshot_id` | id of the exact data the decision was based on |

---

## 7. Troubleshooting

| Symptom | Fix |
| --- | --- |
| `python` not found on Windows | Use `py` and the venv's `.venv\Scripts\python.exe`. |
| Live gateway tests **skip** | Ollama isn't running. Start it; confirm `ollama list` shows `llama3.2`. |
| Model is very slow / uses the CPU | A caller bypassed the gateway and used a huge context. Always go through the gateway (it caps at 8k). |
| `ImportError: backend.app.schemas...` | You skipped `pip install -e .`. Run it inside the venv. |
| `third_party/TradingAgents` is empty | You cloned without submodules. Run `git submodule update --init --recursive`. |
| Import of `Recommendation` fails in a new module | Import from `backend.app.schemas.recommendation` only — never redefine the class. |

---

## 8. What's built vs what's next

**Built (working, tested slice):** the gateway, the quant lane, the deep adapter + number-check, the shared contract, the fusion helper, the pinned TradingAgents submodule, and a stable test suite.

**Next (per `docs/unification-plan.md`):**
- **Step 2** — one data service (yfinance + Redis cache + PostgreSQL price-bar table), news/announcements ingest, stored data snapshots.
- **Step 3** — quant `Recommendation` tables + a nightly Celery fast-lane; regime/signal overlays in the React UI.
- **Step 4** — wire the real TradingAgents graph into the deep lane (config-only), Celery `deep` queue at concurrency 1 with SSE progress.
- **Step 5** — fusion view, advisor/coach on the gateway, alerts, new UI pages; retire Chart.js.
- **Step 6** — backtest grid, paper trading, model comparison, backups + one-command restore.

See `docs/status.md` for the current week and `docs/decisions/` for the rationale behind each major choice.

---

## 9. Important disclaimer

FinTrack is a personal research and monitoring tool. It does not execute trades, it is not financial advice, and its signals are not a proven edge. Paper positions only. If you ever plan to share it with others, review SEBI rules first.
