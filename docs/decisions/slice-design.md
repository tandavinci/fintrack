# Slice Design — Unified Local Trading Intelligence Platform (vertical slice)

Date: 2026-10-21
Status: revised (iteration 3 — resolves the iteration-2 review `slice-design-review.json`, 0 HIGH / 2 MEDIUM / 1 NIT)
Covers: unification-plan Steps 0 (git + submodule), 1 (gateway), 3 (quant), 4 (deep adapter), plus dependencies, venv, and the test strategy.

> **Iteration note (iteration 3):** this revision resolves the three open findings from the iteration-2 review (`slice-design-review.json` / `slice-design-review.md`): (1) MEDIUM — the deep number-check now **preserves the sign** on both the extracted figure and the allowable value, so a correctly-cited negative indicator (`MACD`, `MACD_Signal`, `MACD_Histogram`, `change_pct`) is no longer rejected as a hallucination (section 4.2(b)/(c), test 7.3); (2) MEDIUM — the gateway priority gate is now a **loop-agnostic `threading.Lock`/`Condition`** with a plain synchronous `generate`/`generate_sync`, removing the `asyncio.run`-per-call "bound to a different event loop" failure, and the async FastAPI routes offload to a worker thread sharing the one gate instance (section 2.5, test 7.6); (3) NIT — the `/embed` design names exactly **one** Ollama endpoint, `/api/embed` (section 2.6). The iteration-2 specifications verified correct by that review are carried forward unchanged. The load-bearing specs remain: a shared `snapshot.py` canonicalization helper (sections 3.5, 8), the exact `DataPipeline`/`StrategyAgent` daily construction (3.5), the single `jsonschema`-based gateway validator (2.4), the concrete number-check allowable-set + parse/compare rules (4.2), a fixed `HASH_EMBED_DIM` (2.6), a real slice-scoped `fuse()` helper (section 8 + 7.4), and the named constants `HORIZON_DAYS`, `DEEP_PROMPT_VERSION`, `SLICE_VALIDITY_N`, `MAX_PROMPT_CHARS`, `SNAPSHOT_TAIL_N`.

## Overview

This design resolves the integration and import boundaries for the first end-to-end vertical slice of the monorepo at `c:\GIT\Fintech\repo` (MONO). The slice wires three engines — `quant/` (rule-based + offline), `deep/` (TradingAgents adapter, callable only), and `gateway/` (the single path to Ollama) — around the one shared contract already written at `backend/app/schemas/recommendation.py`. It also locks the git/submodule plan, the dependency set, the Windows `py`/venv story, and a repeatable test strategy.

The design is bound by six hard constraints carried from STEERING and decisions 0001–0004: (1) no paid APIs or keys — only the gateway talks to Ollama; (2) TradingAgents source is never edited (config + adapter only); (3) models explain, code computes — a number-check guards the deep lane; (4) exactly one `Recommendation` schema, imported everywhere; (5) the deep lane never runs inside a web request; (6) every model call is logged. All model and context choices use the **measured** hardware (RTX 2050, 4 GB VRAM, 31.5 GB RAM) per decision 0004: `llama3.2` (3B, already pulled) for both tiers, `num_ctx` hard-capped at 8192. No 8B model is pulled or assumed.

The current skeleton has empty `backend/ deep/ docs/ frontend/ gateway/ infra/ quant/ tests/ third_party/` directories, a seeded `.gitignore`, a `README.md`, and the four decision notes. There is no git repo yet. The only engine code written so far is the `Recommendation` contract.

## Technology stack (locked once this design is approved)

- Python 3.12 via the `py` launcher. `python`/`python3` on PATH are broken Microsoft Store stubs and must not be used for host commands. A project venv lives at `c:\GIT\Fintech\repo\.venv`; its interpreter is `c:\GIT\Fintech\repo\.venv\Scripts\python.exe`.
- Web/service: FastAPI + uvicorn (gateway only in this slice; `backend/` is out of slice scope).
- Contracts/config: pydantic v2, pydantic-settings.
- HTTP: httpx (sync client for the gateway → Ollama path).
- Data/quant: pandas, numpy, `ta`, yfinance.
- Tests: pytest, pytest-asyncio.
- LLM runtime: Ollama 0.40.6 on the host at `http://localhost:11434`, model `llama3.2`, `num_ctx=8192`.
- TradingAgents: pinned git submodule at `third_party/TradingAgents`, read-only.

No other frameworks are introduced. LangChain/`langchain_openai` (used by the legacy quant LLM path) is **dropped** — the gateway replaces it.

### Named slice constants (single source of truth)

These are fixed values, not examples. Each lives in exactly one module so tests and code agree. They are referenced by section number throughout.

| Constant | Value | Home module | Used by |
| --- | --- | --- | --- |
| `HORIZON_DAYS` | `10` | `quant/recommend.py` | 3.5 — StrategyAgent horizon on daily bars (matches unification-plan 0.4 `--horizon 10`) |
| `SNAPSHOT_TAIL_N` | `60` | `backend/app/schemas/snapshot.py` | 3.5, 8, 4.2 — rows hashed for `snapshot_id` and the OHLCV window of the number-check allowable set |
| `HASH_EMBED_DIM` | `256` | `gateway/client.py` | 2.6 — placeholder embedding dimension |
| `MAX_PROMPT_CHARS` | `24000` | `gateway/settings.py` | 2.8 — prompt length cap (~6k tokens, leaves generation headroom under `num_ctx=8192`) |
| `DEEP_PROMPT_VERSION` | `"deep-v1"` | `deep/adapter.py` | 4.2, 4.3 — the `prompt_version` written on every deep `Recommendation` |
| `SLICE_VALIDITY_N` | `20` | `tests/test_gateway_validity_rate.py` | 7.5 — denominator of the validity-rate assertion |
| `NUMBER_CHECK_REL_TOL` | `0.01` | `deep/adapter.py` | 4.2 — relative tolerance (1%) for figure matching |
| `NUMBER_CHECK_ABS_TOL` | `0.01` | `deep/adapter.py` | 4.2 — absolute tolerance for small magnitudes |

A new shared module `backend/app/schemas/snapshot.py` holds the canonicalization + `snapshot_id` helper and `SNAPSHOT_TAIL_N`, so both `quant/recommend.py` and `deep/adapter.py` import the **one** implementation (section 8). It lives next to `recommendation.py` because it is contract-adjacent (it computes the contract's `snapshot_id`) and belongs to the same single-source-of-truth package.

---

## 1. Shared import wiring — one `Recommendation`, imported by all engines

### Decision: a minimal `pyproject.toml` at the repo root installed editable (`pip install -e .`)

Two approaches were on the table:

- **A — repo-root `conftest.py` adding the repo root to `sys.path`.** Simple and zero-install, but it only fixes imports *under pytest*. The moment `deep/adapter.py` or `quant/recommend.py` is imported by a non-test caller (a Celery job in Step 3.2/4.3, a REPL, the gateway importing nothing-from-quant-but-quant-importing-the-contract), `from backend.app.schemas.recommendation import Recommendation` fails because the repo root is not on `sys.path`. It papers over the boundary instead of defining it.
- **B — a minimal `pyproject.toml` making the repo root an editable-installable package.** `pip install -e .` into the venv puts the repo root on the venv's `sys.path` permanently (via the `.pth`/editable finder), so every entry point — pytest, Celery, uvicorn, a plain script — resolves the same imports identically. It also gives us one place to declare dependencies later.

**Choose B.** It makes the import boundary a real, installed fact rather than a test-time trick, which is what a long-lived monorepo with queued jobs (Celery) needs.

### Concrete files and contents direction

`c:\GIT\Fintech\repo\pyproject.toml` (new). Direction, not final code:

- `[build-system]` with `requires = ["setuptools>=68"]`, `build-backend = "setuptools.build_meta"`.
- `[project]` name `local-trading-intel`, `requires-python = ">=3.12"`, version `0.0.0`. Leave `dependencies` empty here and keep the pinned list in `requirements.txt` (section 6) — the editable install exists for path/import wiring, not dependency management, to avoid two competing sources of truth.
- `[tool.setuptools]` must declare the packages that ship as import roots so the editable install exposes them: `packages = ["backend", "quant", "deep", "gateway"]` **or** use `[tool.setuptools.packages.find]` with `include = ["backend*", "quant*", "deep*", "gateway*"]`. Choose explicit `packages = [...]` to avoid setuptools scanning `third_party/` (the submodule) or `frontend/`.
- `[tool.pytest.ini_options]` with `asyncio_mode = "auto"` and `testpaths = ["tests"]` so pytest-asyncio and discovery are configured in one place.

**The single contract rule.** `Recommendation`, `Action` live only in `backend/app/schemas/recommendation.py` (already written — do not redefine, do not add engine-specific fields). Every engine imports it with the exact line:

```python
from backend.app.schemas.recommendation import Recommendation, Action
```

For this to resolve, `backend/` and `backend/app/` and `backend/app/schemas/` each need an `__init__.py` (the schema file exists but the package markers may not — the implementer must add any missing `__init__.py` so `backend.app.schemas` is a real package). `quant/`, `deep/`, and `gateway/` likewise get `__init__.py` files (section 3 lists the quant ones). The install step is: `c:\GIT\Fintech\repo\.venv\Scripts\python.exe -m pip install -e .` from the repo root.

**Edge case:** the editable install must be re-run only if `pyproject.toml`'s package list changes, not on every code edit (editable installs track source live). The fresh-checkout/runbook note (section 5) must include `pip install -e .` after `pip install -r requirements.txt`.

---

## 2. Gateway contract

The gateway is the only component that talks to Ollama (rule 1) and the only place `num_ctx` is set, so no caller can trigger the slow spilled-to-RAM path (decision 0004 §3). It is both an importable core and a FastAPI service; the importable core works **without** starting a server.

### 2.1 File layout under `gateway/`

- `gateway/__init__.py`
- `gateway/settings.py` — pydantic-settings `Settings`.
- `gateway/client.py` — the importable core: the Ollama HTTP client plus the `generate`/`validate` functions and the priority lock. No FastAPI import here.
- `gateway/schemas.py` — pydantic request/response models for the three routes.
- `gateway/logging_jsonl.py` — the per-call JSONL logger.
- `gateway/app.py` — FastAPI app; defines the three routes as thin wrappers over `client.py`.
- `gateway/logs/` — created at runtime; `gateway/logs/calls.jsonl` is the log sink. Git-ignored (section 5).

### 2.2 Config via pydantic-settings (`gateway/settings.py`)

`Settings(BaseSettings)` with env-overridable fields and these defaults:

- `OLLAMA_BASE_URL: str = "http://localhost:11434"`
- `GATEWAY_MODEL: str = "llama3.2"`
- `GATEWAY_NUM_CTX: int = 8192`
- plus operational knobs with defaults: `GATEWAY_REQUEST_TIMEOUT_S: float = 120.0` (deep prompts on a 3B model at 27 tok/s need headroom), `GATEWAY_MAX_RETRIES: int = 1` (one retry on invalid JSON, then structured error), `GATEWAY_LOG_PATH: str = "gateway/logs/calls.jsonl"`, `MAX_PROMPT_CHARS: int = 24000` (the prompt length cap, section 2.8).

`num_ctx` is **hard-capped**: the client sets `options.num_ctx = min(settings.GATEWAY_NUM_CTX, 8192)` on every Ollama call. Even if someone sets `GATEWAY_NUM_CTX` higher via env, the cap wins. This is the single enforcement point for decision 0004 §3; document it as such in the code.

### 2.3 The three routes

All three are `POST`. Request/response shapes (pydantic models in `gateway/schemas.py`):

**`POST /quick`** — interactive, high priority.
- Request: `{ prompt: str, json_schema: dict | None = None, system: str | None = None, temperature: float = 0.1, max_tokens: int = 512 }`.
- Response: `{ text: str, valid: bool, model_id: str, prompt_hash: str, eval_count: int, prompt_eval_count: int, latency_ms: int, attempts: int, error: str | None }`.

**`POST /deep`** — queued, low priority, preemptible by `/quick`.
- Request: same shape as `/quick` plus `max_tokens: int = 1024`. Callers that want structured output pass `json_schema`.
- Response: same shape as `/quick`.

**`POST /embed`** — embeddings (see fallback in 2.6).
- Request: `{ input: str | list[str] }`.
- Response: `{ embeddings: list[list[float]], dim: int, method: Literal["ollama", "hash_placeholder"], model_id: str | None }`.

Both `/quick` and `/deep` map to the same model (`llama3.2`) — one model for both tiers (decision 0004 §1). The tiers differ only in **priority** and default `max_tokens`, not model.

### 2.4 Schema-constrained generation + one-retry-then-structured-error

**Decision (resolves review finding 3): one deterministic validator, `jsonschema`.** When a request carries `json_schema`, the client passes it to Ollama as `format=<json_schema>` (Ollama's structured-output mode) and sets `options.temperature` low. The validate step is a single, machine-independent function `validate(text: str, json_schema: dict) -> tuple[bool, str | None]`:

```
1. try: obj = json.loads(text)        -> on JSONDecodeError return (False, "json_parse_error")
2. try: jsonschema.validate(obj, json_schema)  -> on ValidationError return (False, "schema_validation_failed")
3. return (True, None)
```

`jsonschema` is **added to `requirements.txt`** (section 6.1) — it is pure-Python and 3.12-safe, with no hardware sensitivity, so "if available" ambiguity is eliminated: it is always available. The gateway validator does nothing beyond this: it validates the parsed object against exactly the schema it was handed, no more. It does **not** construct `Recommendation(**obj)` — that pydantic-contract check belongs to the deep adapter's own post-gateway step (section 4.2 step 4), where the target type is known to be `Recommendation`. The gateway stays generic so any caller can pass any schema.

Flow:
1. First call with `format=json_schema`. `validate(...)`.
2. If invalid (either `json_parse_error` or `schema_validation_failed`): retry **once**, appending a terse corrective instruction ("Return ONLY valid JSON matching the schema; previous output failed validation") to the prompt.
3. If the retry is still invalid: return a **structured error** — HTTP 200 with `valid=false`, `error="schema_validation_failed_after_retry"`, and the raw `text` preserved so the caller can inspect it. The route does not raise; callers (deep adapter) decide what to do. `attempts=2` is recorded.

When no `json_schema` is given, `valid` is reported as `true` for any non-empty text (free-form `/quick` chat). Empty text → `valid=false`, `error="empty_response"`.

### 2.5 Priority lock so `/quick` preempts `/deep`

Ollama runs one model, one request at a time (`OLLAMA_NUM_PARALLEL=1`, decision 0002). The gateway must not let a long `/deep` generation block an interactive `/quick`.

**Decision (resolves review finding 2): a loop-agnostic `threading`-based priority gate, not an `asyncio` lock.** The earlier `asyncio`-lock design was infeasible as written: a module-level `asyncio.Lock()`/`asyncio.Condition` binds to the event loop on first use, while a `generate_sync` wrapper built on `asyncio.run` creates and *closes* a new loop on every call. The second in-process `generate_sync` call would then await a lock bound to the first, now-closed loop and raise `RuntimeError: ... bound to a different event loop`. Quant's blanket fallback-on-failure would silently mask this and defeat the gateway path after the first call; the deep adapter would surface the error. The two engines in this slice (`quant.strategy_agent`, `deep.adapter`) are **plain synchronous callables**, not async, so the natural, loop-free mechanism is `threading`, which both the sync callers and the async FastAPI routes can share without any event-loop identity.

Design — one module-level gate in `client.py`, used by every path:

- A module-level `threading.Lock()` (`_ollama_lock`) guards the actual (synchronous `httpx`) HTTP call to Ollama, so only one in-flight generation exists regardless of which caller holds it.
- A module-level `threading.Condition` (`_priority_cv`) protects an `int` counter `_quick_waiting` recording how many `/quick` requests are queued or in flight.
- `/quick` path: under `_priority_cv`, increment `_quick_waiting` and `notify_all`; acquire `_ollama_lock`; make the call; release `_ollama_lock`; then under `_priority_cv` decrement `_quick_waiting` and `notify_all`.
- `/deep` path: under `_priority_cv`, `wait_for(lambda: _quick_waiting == 0)` so a queued/in-flight `/quick` drains first; then acquire `_ollama_lock` and make the call. If a `/quick` arrives while `/deep` holds `_ollama_lock`, the quick request blocks on `_ollama_lock` and is served next (boundary-level preemption).
- This yields "quick preempts deep at the request boundary." True mid-generation preemption is not possible with Ollama's HTTP API and is out of scope; deep generations are short on a 3B model, so boundary-level preemption is sufficient. Document this limitation in the code.

Because the gate is `threading`-based, the importable core's `generate` is an ordinary **synchronous** function. `generate_sync` is simply `generate` (no `asyncio.run`, no per-call loop) — the quant strategy agent and the deep adapter call it directly in-process. The FastAPI routes (`gateway/app.py`) are `async def`, but they must **not** call the blocking core on the event loop thread: each route invokes the synchronous `generate` via `await anyio.to_thread.run_sync(...)` (Starlette ships `anyio`), so the blocking `httpx`/`threading` call runs on a worker thread while the loop stays responsive. **The routes and the in-process sync callers import and use the same module-level `_ollama_lock`/`_priority_cv`/`_quick_waiting` instances from `client.py`** — this is load-bearing: if `/quick` served by uvicorn and an in-process `/deep` Celery job used different lock instances they would not serialize against each other and the 2.5 preemption guarantee would not hold. There is exactly one gate, in `client.py`, shared by both the async route layer and the synchronous in-process path.

**Edge case — single-process assumption.** The `threading` gate serializes callers **within one OS process**. In this slice the gateway service (uvicorn) and the in-process quant/deep callers that import `gateway.client` directly are each a single process, and Ollama itself enforces `OLLAMA_NUM_PARALLEL=1` as the real backstop, so two separate processes hitting Ollama still cannot run concurrent generations (the second blocks at Ollama). The in-process `threading` gate is the *ordering/preemption* mechanism; Ollama's own single-slot queue is the hard concurrency limit. Cross-process priority ordering (e.g. a separate Celery worker process vs the uvicorn process) is out of slice scope and noted for Step 4.3 when the `deep` queue becomes a separate worker.

### 2.6 `/embed` fallback strategy

`llama3.2` is a text-generation model; Ollama's embeddings endpoint may or may not return usable embeddings for it. **One endpoint name is used throughout (resolves review finding 3): `POST {OLLAMA_BASE_URL}/api/embed`** — the current Ollama embeddings route. The older alias `/api/embeddings` is **not** used anywhere in this design; the implementer targets `/api/embed` only. Strategy:

1. Attempt Ollama embeddings via `POST {OLLAMA_BASE_URL}/api/embed` with `model=GATEWAY_MODEL`. If it returns a non-empty vector, use it; `method="ollama"`, `dim=len(vector)` (whatever Ollama's native dimension is — not forced to `HASH_EMBED_DIM`).
2. On any failure (HTTP error, empty vector, model does not support embeddings): fall back to a **deterministic local hashing-based embedding placeholder** that needs no network download. Design: fixed dimension **`HASH_EMBED_DIM = 256`** (a stated constant, not an example — section "Named slice constants"), compute per-token hashes (`hashlib.sha256` of lowercased `\W`-split tokens) folded into the `HASH_EMBED_DIM` buckets (hashing-trick / feature hashing), L2-normalize. Deterministic for the same input, zero dependencies, no download. `method="hash_placeholder"`, `dim=256`.
3. **Dimension reconciliation (resolves review finding 6):** the two paths can return different `dim` (Ollama native vs 256). The response therefore always carries both `dim` and `method`, and the contract is: **callers must branch on `method`** and must not assume a single dimension across methods. For this slice the guaranteed, always-available path is `hash_placeholder` at `dim=256`; the Ollama path is best-effort. A downstream store that requires one fixed dimension (none exists in this slice — pgvector population is Step 2) must pin to `HASH_EMBED_DIM` and, if it also wants to use the Ollama path, project/truncate the Ollama vector to `HASH_EMBED_DIM` (truncate-then-L2-renormalize); this slice does not do that projection because nothing downstream stores vectors yet, and the design states the rule so Step 2 inherits it.
4. The placeholder is **explicitly documented** as a stopgap for pipeline wiring and local tests, not for semantic search quality. Step 1.3/2 will revisit real embeddings (a dedicated embedding model or pgvector population) — note this in the code docstring and in `docs/status.md`. This slice only needs `/embed` to return stable vectors so downstream wiring and tests pass offline.

### 2.7 Per-call JSONL logging (rule 8)

Every call to Ollama (from any of the three routes, and from the importable core when used in-process) appends exactly one line to `gateway/logs/calls.jsonl`. The log is append-only, one JSON object per line, written by `gateway/logging_jsonl.py`. Fields:

- `ts` — ISO-8601 UTC timestamp.
- `route` — `"quick" | "deep" | "embed"`.
- `model_id` — the resolved model string (`llama3.2`).
- `prompt_sha256` — `hashlib.sha256(prompt.encode()).hexdigest()` (hash, never the raw prompt — avoids logging content and keeps lines small).
- `num_ctx` — the enforced value (8192).
- `eval_count`, `prompt_eval_count` — token counts from the Ollama response (null for embed if absent).
- `latency_ms` — wall-clock of the Ollama call.
- `attempts` — 1 or 2 (reflects the one-retry path).
- `valid` — bool (validation result; null for free-form).
- `error` — string or null.

The logger creates `gateway/logs/` if missing. Writes use a short lock/append-open to stay safe under the single-request serialization. A written log line is an assertion target in the gateway test (section 7).

### 2.8 Error handling (gateway)

| Failure | Recoverable? | Caller receives | Logged |
| --- | --- | --- | --- |
| Ollama unreachable (connection error/timeout) | fatal for this call | HTTP 503 from the route; `generate_sync` raises `GatewayUnavailable` | yes — `error="ollama_unreachable"`, `latency_ms` = elapsed |
| Ollama HTTP 4xx/5xx | fatal for this call | HTTP 502; exception in core | yes — `error="ollama_http_<code>"` |
| Invalid JSON / schema mismatch, 1st attempt | recoverable | — (internal retry) | the retry's line records `attempts=2` |
| Invalid after retry | handled | HTTP 200, `valid=false`, raw text kept | yes — `error="schema_validation_failed_after_retry"` |
| Empty response | handled | HTTP 200, `valid=false` | yes — `error="empty_response"` |
| Embed: Ollama embeddings unsupported | recoverable | placeholder vector, `method="hash_placeholder"` | yes — `error=null`, `method` recorded |

Validation rules for gateway inputs: `prompt` required, non-empty, length-capped at **`MAX_PROMPT_CHARS = 24000`** characters (resolves review finding 9 — a concrete, testable boundary: ~6k tokens at ~4 chars/token, leaving generation headroom under the hard `num_ctx=8192` cap). A prompt with `len(prompt) > MAX_PROMPT_CHARS` returns HTTP 422 `prompt_too_long` and is **not** sent to Ollama (no log line, since no model call occurs). `json_schema`, if present, must be a dict (HTTP 422 `invalid_json_schema` otherwise). `temperature` clamped to `[0, 2]`. `max_tokens` clamped to a sane ceiling (`<= 2048`).

**Testability:** `client.py` has no FastAPI dependency, so `generate`/`validate`/the hash-embed/the logger are all unit-testable in-process. The real-Ollama behavior (section 7) is an integration test that skips when Ollama is unreachable.

---

## 3. Quant package layout (unification-plan Step 3.1)

### 3.1 Target tree under `c:\GIT\Fintech\repo\quant\`

```
quant/
  __init__.py
  config.py                      # from backend/config.py, trimmed
  recommend.py                   # NEW — the slice entry point
  data/
    __init__.py
    indicators.py                # from backend/data/indicators.py
    fetcher.py                   # from backend/data/fetcher.py
    pipeline.py                  # from backend/data/pipeline.py
  agents/
    __init__.py
    regime_agent.py              # from backend/agents/regime_agent.py
    strategy_agent.py            # from backend/agents/strategy_agent.py
  backtesting/
    __init__.py
    engine.py                    # from backend/backtesting/engine.py
```

Source is `c:\GIT\Fintech\Not-a-Finance-advice-\backend\`. Copy the working tree (decision 0001 — clean copy, not subtree), preserving the source commit SHA in a migration note at copy time.

### 3.2 Import rewrite rule: `from backend.xxx` → `from quant.xxx`

Every moved module currently imports siblings as `from backend.config import Config`, `from backend.data.pipeline import DataPipeline`, `from backend.agents.regime_agent import ...`, etc. The mechanical rewrite is:

- `from backend.config` → `from quant.config`
- `from backend.data.` → `from quant.data.`
- `from backend.agents.` → `from quant.agents.`
- `from backend.backtesting.` → `from quant.backtesting.`
- also the in-function import inside `strategy_agent._score_regime`: `from backend.agents.regime_agent import MarketRegime` → `from quant.agents.regime_agent import MarketRegime`.

This is a pure prefix swap `backend.` → `quant.` across the six moved files. **Risk/edge case:** MONO *also* has a `backend/` (the Tracker shell + the `Recommendation` contract). The rewrite must swap only the quant-internal imports to `quant.`; it must **not** touch the one new import of the shared contract, which stays `from backend.app.schemas.recommendation import ...` (that `backend` is MONO's Tracker package, a different thing from the old AI-Trading-Agent `backend`). Because the old source has no reference to `app.schemas`, a blind `backend.` → `quant.` replace is safe for the copied files; the contract import is added fresh in `recommend.py` and is written correctly from the start.

### 3.3 Drop the FastAPI layer and `run.py` CLI

Per Step 3.1, the moved code is library code only. Do **not** copy the source repo's `server.py`/FastAPI app or `run.py` CLI. The quant engine is driven by `quant/recommend.py` and (later) a Celery job (Step 3.2, out of this slice). This keeps quant import-only with no server/CLI entry points.

### 3.4 Rewire `strategy_agent`'s optional LLM mode through the gateway

The source `StrategyAgent` has three modes: `rule_based` (default, no deps), `openai` (via `langchain_openai` — a paid API, **forbidden** by rule 1), and `ollama` (direct httpx to Ollama — **forbidden** because only the gateway may talk to Ollama, rule 1).

Rewire:
- **Delete the `openai` path** entirely (`_predict_with_llm` and the `langchain_openai` import). It violates the no-paid-API rule and drops the LangChain dependency.
- **Replace the direct-Ollama path** (`_predict_with_ollama`'s direct `httpx.post` to `/api/generate`) with a call to the gateway core: `from gateway.client import generate_sync` and request structured JSON via the gateway (passing a small JSON schema for the prediction dict), with the gateway enforcing `num_ctx=8192` and logging the call. The method keeps its existing "parse + fall back to rule-based on any failure" behavior — if the gateway is unreachable or returns `valid=false`, it falls back to `_predict_rule_based`.
- **Offline default stays rule-based.** `Config.DEFAULT_LLM_PROVIDER = "rule_based"`; with no gateway running, quant produces deterministic rule-based predictions. The `llm_provider` values become `"rule_based"` (default) and `"gateway"` (replacing both `"openai"` and `"ollama"`). `use_llm=True` now routes to `"gateway"`, never to a paid API.
- `quant/config.py` is trimmed: drop `OPENAI_API_KEY`, `POLYGON_API_KEY`, `LLM_MODEL="gpt-4o-mini"`, `OLLAMA_*` (the gateway owns Ollama config now), `API_HOST`/`API_PORT` (no server). Keep the data/interval/indicator/backtest settings. The `python-dotenv` `load_dotenv()` call can stay or be dropped; prefer dropping it since the gateway owns runtime LLM config and quant needs no secrets.

**Edge case / `.BO`/`.NS`:** the source `resolve_ticker` handles forex/commodity suffixes but passes stock tickers through unchanged, so `RELIANCE.BO` / `TCS.NS` already work as-is (yfinance accepts them directly). No change needed for the slice; Step 2 does the fuller data unification.

### 3.5 `quant/recommend.py` — the slice entry point

New module. Signature:

```python
HORIZON_DAYS = 10  # slice constant; one period = one day on daily bars (matches unification-plan 0.4)

def recommend(ticker: str, as_of: date, df: pd.DataFrame | None = None) -> Recommendation:
```

**The daily-interval path (resolves review findings 1 and 2).** The source `DataPipeline` defaults `interval` to `Config.DEFAULT_INTERVAL = "30m"` and `get_enriched_data` takes `end_date: str` / `lookback_days`, not an `as_of: date`; and `StrategyAgent` defaults `interval="30m"` with `horizon = Config.DEFAULT_PREDICTION_HORIZON = 2` periods, where `Prediction.horizon_days` is a "legacy field name" that actually carries the **period** count. Left to defaults, the slice would produce 30-minute bars and a `horizon_days` of `2` meaning "2 × 30-minute periods" (one hour) mislabeled as days. The slice therefore pins **daily** construction explicitly, which is what makes the contract's day-denominated fields correct:

```python
# live path only (skipped when df is injected for offline tests)
pipe = DataPipeline(ticker=ticker, interval="1d")
df = pipe.get_enriched_data(end_date=as_of.isoformat(), interval="1d", use_cache=False)
```

- **Daily interval is mandatory for the slice.** The 30m default is never used here; both the constructor and the `get_enriched_data` call pass `interval="1d"`. `end_date=as_of.isoformat()` is the inclusive yfinance end bound on the live path. For offline tests a synthetic/golden daily `df` is injected and this whole block is **skipped** (no network).
- `StrategyAgent` is constructed as **`StrategyAgent(interval="1d", horizon=HORIZON_DAYS)`** so `self.interval == "1d"` and `self.horizon == 10`. On daily bars one period equals one day, so `prediction.horizon_days` (== `self.horizon`) is now a true day count and maps straight through. The slice asserts `prediction.interval == "1d"` before trusting the `horizon_days` mapping; if any caller ever supplies a non-daily `df`, `recommend` raises `ValueError("recommend() requires daily bars")` rather than emit a wrong-unit horizon.

Behavior:
- Run `RegimeAgent.classify(df)` then `StrategyAgent(interval="1d", horizon=HORIZON_DAYS).predict(df, regime)` (rule-based by default — deterministic).
- Map the internal `Prediction` to the shared `Recommendation`:
  - `ticker=ticker`, `as_of=as_of`.
  - `action` = `Action(prediction.signal.value)` (BUY/HOLD/SELL — the enum **values** align; member order is irrelevant for value construction).
  - `confidence` = `prediction.confidence` (rule-based path yields `round(min(confidence, 0.95), 3)`, always within `[0, 1]`).
  - `horizon_days` = `prediction.horizon_days` **only because `interval=="1d"`** (= `HORIZON_DAYS` = 10 days). Guarded as above.
  - `rationale` = `prediction.reasoning`.
  - `evidence` = pinned, reproducible (resolves review finding 11). Built by a module-level `build_evidence(summary: dict) -> list[str]` helper that reads `prediction.indicators_snapshot` (the `TechnicalIndicators.summarize` dict) in a **fixed order** and emits short `"key=value"` strings, each value formatted with `f"{v:.4f}"` for floats and `str(v)` for ints, skipping keys absent from the summary. The exact ordered key list is: `price.current`, `price.change_pct`, `moving_averages.SMA_20`, `moving_averages.SMA_50`, `momentum.RSI`, `momentum.MACD`, `momentum.MACD_Signal`, `volatility.ATR`, `volatility.BB_Width`, `volume.Volume_Ratio`. Example output: `["price.current=2431.5000", "momentum.RSI=57.2900", ...]`. Because `summarize` already rounds and the formatting/order are fixed, `evidence` is byte-identical for identical input (Gate G3 / test 7.1). `evidence` is guaranteed non-empty because `price.current` is always present in `summarize`.
  - `source="quant"`.
  - `model_id=None` (rule-based — the contract explicitly allows None for rule-based quant).
  - `prompt_version=None`.
  - `snapshot_id` = **set** via the one shared helper `compute_snapshot_id(ticker, as_of, df)` from `backend/app/schemas/snapshot.py` (section 8). This makes `snapshot_id` reproducible and identical to what the deep adapter computes for the same input (the integration test relies on it).
- Returns a validated `Recommendation` (construction raises on any contract violation — caught by the caller/test).

**Snapshot ownership:** `snapshot_id` is computed by the shared `backend/app/schemas/snapshot.py` helper (section 8), imported by both `quant.recommend` and `deep.adapter` so they cannot diverge. In Step 2 the snapshot object becomes a first-class stored entity; until then the deterministic hash is the contract's `snapshot_id`. The deep adapter (section 4) builds the same `df`/`as_of`/`ticker` so both engines reference one snapshot — the integration test passes the same inputs to both and asserts equal ids.

### 3.6 Quant error handling

| Operation | Failure | Recoverable? | Caller receives | Logged |
| --- | --- | --- | --- | --- |
| `DataPipeline.get_enriched_data` (network) | yfinance empty/raises `ValueError` | fatal | exception propagates to caller | not in this slice (quant has no logger; caller logs) |
| insufficient warmed-up rows | too few bars after `dropna` | fatal | `ValueError` with bar count | — |
| gateway LLM mode | gateway unreachable / `valid=false` | recoverable | silent fallback to rule-based | the gateway logged its own failed call |
| `Recommendation(**...)` | bad field (e.g. confidence out of range) | fatal (programming error) | pydantic `ValidationError` | — |

Validation of external input to `recommend`: `ticker` required non-empty str; `as_of` a `date`; `df` if provided must contain OHLCV + the indicator columns the agents read (else the agents' own `pd.isna` guards degrade to HOLD-ish rule outputs — acceptable, deterministic).

**Testability:** fully unit-testable offline by injecting a synthetic/golden `df` (no yfinance call). Deterministic rule-based path → identical output for identical input (Gate G3's "same run twice = identical").

---

## 4. Deep adapter boundary (unification-plan Step 4.1, 4.6)

### 4.1 `deep/adapter.py` — a plain callable, never inside a web request

```
deep/
  __init__.py
  adapter.py
```

Signature:

```python
def analyze(ticker: str, as_of: date, portfolio: dict | None = None) -> Recommendation:
```

This is a **plain synchronous callable** invoked by a Celery `deep` worker or a script (Step 4.3), **never** from a FastAPI request handler (rule 5). The design records this constraint in the module docstring and keeps `adapter.py` free of any FastAPI/`Request` import so it is structurally impossible to mount as a route.

### 4.2 Flow

1. Build a **data snapshot** for `ticker` as of `as_of` — reuse the quant `DataPipeline` to get the enriched df and the same deterministic `snapshot_id` as `quant/recommend.py` (so quant and deep reference one snapshot). `portfolio` (holdings dict) is folded into the prompt context when present (Step 4.1 `PortfolioContext`); it does not change `snapshot_id`.
2. Build a prompt from the snapshot (indicator summary, recent bars, regime, and portfolio context) instructing the model to return a `Recommendation`-shaped JSON and to cite only figures present in the snapshot.
3. Call the gateway `/deep` route (or `gateway.client.generate_sync` in-process) passing the JSON schema derived from `Recommendation` so the gateway runs schema-constrained generation + the one-retry path. `num_ctx=8192` is enforced by the gateway.
4. **Validate** the returned JSON against the `Recommendation` schema (construct `Recommendation(**obj)`); on `valid=false` from the gateway or a construction error, raise `DeepValidationError` (fatal for this ticker — the Celery job records and moves on).
5. **Number-check (rule 3, Step 4.6) — fully specified (resolves review finding 5).** The real `TechnicalIndicators.summarize(df)` is a **nested** dict (`price`, `moving_averages`, `momentum`, `volatility`, `volume`) of values rounded to 2–4 dp, plus an `int` `volume.Volume`. The check is deterministic:

   **(a) Build the allowable-numbers set** (a list of floats):
   - Recursively walk the `summarize(df)` dict and collect **every numeric leaf** (int or float, at any nesting depth) as a float.
   - Add the last `SNAPSHOT_TAIL_N` (= 60) bars' raw `Open, High, Low, Close, Volume` values from `df` (raw, unrounded floats; `Volume` cast to float).
   - Add `regime.confidence` (the `RegimeClassification.confidence` float).
   - If `portfolio` is present, add each **validated** numeric holding value (after the input validation in 4.4 coerces/rejects non-numeric entries, so no bad figure leaks in).
   - De-duplicate into a set-like list. This is the allowable set.

   **(b) Extract figures from the model text** (`rationale` + each `evidence` string) with the regex `[-+]?\d[\d,]*(?:\.\d+)?\s*%?x?` applied case-insensitively. For each match the sign is **preserved** (resolves review finding 1): a leading `+` is dropped, a leading `-` is kept as part of the value, so the parsed float carries its real sign. Then strip thousands-separator commas and a trailing `%` or `x`, and parse to a **signed** float. This matters because the allowable set (step a) legitimately holds negative values — `momentum.MACD`, `momentum.MACD_Signal`, `momentum.MACD_Histogram` (`MACD = ema_fast - ema_slow`, frequently negative) and `price.change_pct` on a down day — verified against `TechnicalIndicators.summarize`. A model correctly citing `MACD = -0.42` must extract as `-0.42` (not `0.42`) so it matches the stored `-0.42`; stripping the sign would compare `0.42` against `-0.42` (`abs(0.42 - (-0.42)) = 0.84`, outside tolerance) and reject a faithful report. A trailing `%` does **not** rescale the value (the model's `57.3%` is compared against stored `57.29`, both as `57.3`/`57.29`) — RSI/percent fields in the snapshot are already stored as plain numbers, so no ×100 conversion is applied. Bare years/counts that happen to match an allowable value are permitted (they match by construction).

   **(c) Compare — on signed values.** A **signed** figure `x` is allowed if there exists a **signed** allowable value `a` with `abs(x - a) <= max(NUMBER_CHECK_ABS_TOL, NUMBER_CHECK_REL_TOL * abs(a))` — i.e. within **1% relative OR 0.01 absolute** (the absolute floor covers small magnitudes like `0.0` or `0.15`). Both sides keep their sign, so a correctly-cited `-0.42` matches the stored `-0.42` and a sign-flipped `+0.42` is (correctly) rejected as not present in the snapshot. Any extracted figure matching **no** allowable value → **reject**: raise `NumberCheckError(offending=[...])` naming every offending figure (fatal for this report — a hallucinated number invalidates the answer; Gate G4 tracks the failure rate < 5%). The planted-figure test (7.3) uses a value outside tolerance of every allowable number so the rejection is deterministic.

6. On success return a `Recommendation` with `source="deep"`, `model_id` = the gateway model (`llama3.2`, read from the gateway response's `model_id`), `prompt_version = DEEP_PROMPT_VERSION` (= `"deep-v1"`), `snapshot_id` = the shared snapshot id from `compute_snapshot_id` (section 8, identical to quant's for the same input).

### 4.3 TODO: real TradingAgents graph call

The real TradingAgents LangGraph run plugs in at step 2–3 above (prompt build + model call). For this slice, the adapter uses the gateway directly with a hand-built prompt. A clearly-marked `TODO` block in `adapter.py` marks the exact seam where the TradingAgents graph call goes, referencing `third_party/TradingAgents` and unification-plan **Step 4.2** (configure by config only: `llm_provider="openai_compatible"`, `backend_url`=gateway, `max_debate_rounds=1`, risk rounds 1, analysts `market,news`, `checkpoint_enabled=True`, low temperature). **TradingAgents source is never edited** (rule 2) — the adapter imports and configures it only. The number-check and the `Recommendation` mapping wrap whatever TradingAgents returns, so swapping the stub for the real graph does not change the adapter's contract. When the real graph is wired, `DEEP_PROMPT_VERSION` is bumped (e.g. `"deep-v2"`) so stored recommendations stay attributable to the prompt/graph that produced them; the stub keeps `"deep-v1"`.

### 4.4 Deep adapter error handling

| Operation | Failure | Recoverable? | Caller receives | Logged |
| --- | --- | --- | --- | --- |
| snapshot build | data unavailable | fatal for ticker | `ValueError` propagates | gateway not involved |
| gateway `/deep` call | unreachable | fatal for ticker | `GatewayUnavailable` | gateway logged the attempt |
| schema validation | `valid=false` / bad fields | fatal for ticker | `DeepValidationError` | gateway logged `schema_validation_failed_after_retry` |
| number-check | hallucinated figure | fatal for ticker | `NumberCheckError` (lists offending figures) | caller/Celery logs the rejection; Gate G4 tracks failure rate < 5% |

Validation of inputs: `ticker` non-empty str; `as_of` a `date`; `portfolio` optional dict — if present, validate it is a mapping of ticker→numeric holding and coerce/reject non-numeric values (a bad portfolio figure must not leak into the snapshot's allowable-numbers set).

**Invariant ownership:** the number-check invariant (rule 3) is owned by the **deep adapter layer**, because deep is the lane where a model writes free-form numeric claims; quant's numbers are code-computed and need no check. The adapter enforces it before returning, so no unchecked model number ever escapes into a `Recommendation`.

**Testability:** unit-testable by stubbing `gateway.client.generate_sync` to return (a) a schema-valid `Recommendation` JSON whose numbers are all in the snapshot → passes, and (b) one with a planted figure absent from the snapshot → `NumberCheckError`. No network needed for these two cases.

---

## 5. Git + submodule plan (unification-plan Step 0.2, 0.3)

### 5.1 `git init` and `.gitignore`

`git init` at `c:\GIT\Fintech\repo` (MONO is a new repo per decision 0001; `c:\GIT\Fintech` itself is not a repo). Default branch `main`.

`.gitignore` already exists and covers `.venv/`. It must be amended to also cover `gateway/logs/` (runtime call logs must never be committed). Add a `gateway/logs/` line. Keep tracking the golden CSVs explicitly (they are test fixtures, section 7).

### 5.2 Commit sequence (one task = one commit, message `step-N: ...`)

1. `step-0: skeleton + docs + gitignore + pyproject` — the existing skeleton dirs (with `__init__.py` added where packages are needed, including `backend/app/schemas/`), docs, `.gitignore` (with `gateway/logs/`), `pyproject.toml`, `requirements.txt`, and the shared contract-adjacent helpers `backend/app/schemas/snapshot.py` + `backend/app/schemas/fusion.py` (section 8). The initial commit.
2. `step-0: add TradingAgents submodule (pinned)` — the submodule add + gitlink + the pinned SHA recorded in decision 0003 (see 5.3). Separate commit so the submodule pin is auditable.
3. `step-1: local LLM gateway` — `gateway/` (settings, client, schemas, logging, app) + gateway tests.
4. `step-3: quant core` — the migrated `quant/` tree with the import rewrite, dropped server/CLI, gateway-wired LLM mode, `quant/recommend.py` + quant tests + golden fixtures.
5. `step-4: deep adapter` — `deep/adapter.py` + deep tests.
6. `step-?: integration + stability tests` — the cross-engine integration test and the 3×-run stability/validity-rate harness.

Commits are created only when the user asks (per git-safety); this sequence is the *plan*, not an instruction to auto-commit. Stage specific files, not `git add .`.

### 5.3 TradingAgents submodule — pinned, not a moving branch

```
git -C c:\GIT\Fintech\repo submodule add https://github.com/TauricResearch/TradingAgents third_party/TradingAgents
git -C c:\GIT\Fintech\repo\third_party\TradingAgents checkout <release-tag-or-SHA>
git -C c:\GIT\Fintech\repo add third_party/TradingAgents .gitmodules
```

Pin to an exact release tag or commit SHA (never `main`/a moving branch), per decision 0003. Record the resolved SHA back into `docs/decisions/0003-tradingagents-pinning.md` once added (decision 0003 has an open item for this).

### 5.4 Documented-blocker fallback if GitHub is unreachable

If `submodule add` fails (no network / GitHub unreachable), do **not** block the slice. Fallback:
- Create `third_party/TradingAgents/.gitkeep` so the path exists and is tracked.
- Create `third_party/README.md` documenting that TradingAgents is a pending pinned submodule (`TauricResearch/TradingAgents`, read-only, rule 2), the intended pin, and the exact `submodule add` command to run once connectivity returns.
- Add a note to `docs/status.md` under the current week: "TradingAgents submodule blocked — GitHub unreachable; placeholder in place; deep adapter runs against the gateway stub until the submodule is pinned."

The deep adapter's slice behavior (gateway stub + number-check, section 4.3) does **not** depend on the submodule, so the slice is fully testable under the fallback. The submodule only matters when the real graph call (TODO) is wired in Step 4.2.

---

## 6. Dependencies + venv (unification-plan Step 0)

### 6.1 `requirements.txt` at the repo root

Pinned set (preferred pins, with the "allow newer for 3.12 with a noted reason" escape hatch):

```
fastapi==0.115.*          # service framework (gateway)
uvicorn==0.30.*           # ASGI server (gateway)
pydantic>=2,<3            # v2 required by the Recommendation contract
pydantic-settings==2.*    # gateway Settings
httpx==0.27.*             # gateway -> Ollama
jsonschema==4.*           # gateway schema validation (section 2.4) — pure-Python, 3.12-safe
pandas==2.2.2             # quant (preferred pin)
numpy==1.26.4             # quant (preferred pin)
ta==0.11.0                # quant indicators (preferred pin)
yfinance==0.2.40          # quant data (preferred pin)
pytest==8.*               # tests
pytest-asyncio==0.23.*    # async gateway tests
```

Notes on pins:
- `yfinance==0.2.40`, `pandas==2.2.2`, `numpy==1.26.4`, `ta==0.11.0` are the preferred pins from the step prompt and are all 3.12-compatible, so they are used as-is. `numpy==1.26.4` (not 2.x) is deliberate — it matches pandas 2.2.2's tested wheels and avoids the numpy-2 ABI churn; keep it unless a transitive dep forces 2.x (then record the bump reason inline).
- `fastapi`/`uvicorn`/`pydantic-settings`/`httpx`/`pytest`/`pytest-asyncio` are pinned to current minor-series wildcards rather than exact patch, because they have no hardware/data sensitivity and 3.12 wheels are stable; this keeps the slice installable without chasing a single patch. If reproducibility later demands it, freeze exact versions via `pip freeze`.
- `jsonschema==4.*` is **added** this iteration (review finding 3) so the gateway's one validator (`json.loads` + `jsonschema.validate`) is always available, not a machine-dependent "if available" branch. It is pure-Python with stable 3.12 wheels and no hardware sensitivity.
- **Dropped:** `langchain_openai`/`langchain`, `python-dotenv` (quant no longer needs secrets), `openai`. The gateway replaces all LLM-client code. No paid-API client is installed (rule 1).
- Note the source quant repo used `ta`-equivalent logic hand-rolled in `indicators.py` (pure pandas/numpy); `ta` is listed because the step prompt requires it and Step 2's data unification may use it, but the migrated `indicators.py` has no hard dependency on it. Keep `ta` pinned so the environment is ready for Step 2.

### 6.2 venv with the `py` launcher

- Create: `py -m venv c:\GIT\Fintech\repo\.venv` (the `py` launcher, because `python`/`python3` on PATH are broken Store stubs).
- The venv interpreter is `c:\GIT\Fintech\repo\.venv\Scripts\python.exe`. All host Python runs through it: `c:\GIT\Fintech\repo\.venv\Scripts\python.exe -m pip install -r requirements.txt` then `... -m pip install -e .`.
- Never invoke bare `python`/`python3`. Use `py` only to *create* the venv; after that use the venv's `python.exe` explicitly (or activate the venv).
- `.venv/` is git-ignored (already in `.gitignore`).

Runbook addition (fresh checkout): `git submodule update --init --recursive` → `py -m venv .venv` → `.venv\Scripts\python.exe -m pip install -r requirements.txt` → `.venv\Scripts\python.exe -m pip install -e .`.

---

## 7. Test strategy (unification-plan gates G0/G1/G3/G4)

All tests live under `tests/`. pytest config lives in `pyproject.toml` (`asyncio_mode="auto"`, `testpaths=["tests"]`). Golden fixtures live under `tests/golden/`.

### 7.1 Quant tests — offline-safe, deterministic

- `tests/test_quant_recommend.py`. Feed a **synthetic/deterministic DataFrame** (or a small CSV under `tests/golden/`) directly into `quant.recommend.recommend(...)` so **no yfinance call** happens. Build a df with known OHLCV + indicator columns engineered to force a specific regime/signal.
- Assert: returns a valid `Recommendation` with `source="quant"`, `model_id is None`, `snapshot_id` set, exactly 16 hex chars (section 8.1), `action in {BUY,HOLD,SELL}`, `0<=confidence<=1`, `horizon_days == 10` (= `HORIZON_DAYS`, confirming the daily-bar unit fix), `evidence` non-empty and deterministic (the pinned ordered `key=value` list from section 3.5).
- **Determinism:** call twice with the same df → byte-identical `Recommendation` (Gate G3 "same run twice = identical"). Assert equality, including `snapshot_id` and `evidence`. Add a direct unit test of `compute_snapshot_id` (section 8.1): same df → same id; a one-cell change → different id.
- Also unit-test the regime agent and indicator math against the synthetic df (known inputs → known indicator values). The synthetic df is a **daily** OHLCV frame (so `interval="1d"` holds) with enough rows that `dropna` leaves ≥ `SNAPSHOT_TAIL_N` warmed-up bars.

### 7.2 Gateway test — against REAL local Ollama `llama3.2`

- `tests/test_gateway_live.py`. **Skips with a clear message** if Ollama is unreachable (probe `GET {OLLAMA_BASE_URL}/api/tags`; on failure `pytest.skip("Ollama not reachable at <url> — start Ollama with llama3.2 to run live gateway tests")`). This keeps CI/offline runs green while still exercising the real path when Ollama is up.
- When Ollama is up, assert:
  1. A free-form `/quick` call returns non-empty `text`.
  2. A schema-constrained call (pass a small JSON schema, e.g. the `Recommendation` schema or a minimal `{signal, confidence}` schema) returns **valid JSON** that passes validation; exercise the **one-retry path** by using a schema the model sometimes misses and asserting `attempts<=2` and eventual `valid=true` (or a clean structured error if the model truly can't comply — assert the structured-error shape in that case).
  3. `num_ctx=8192` was enforced — assert the written log line's `num_ctx == 8192`.
  4. A **call-log line was written** to `gateway/logs/calls.jsonl` with all required fields (`model_id`, `prompt_sha256`, `eval_count`, `prompt_eval_count`, `latency_ms`, `valid`). Read the last line and assert the shape.
- Use the importable core (`gateway.client`) directly where possible so the test needs no running uvicorn; optionally also hit the FastAPI app via `httpx`/`TestClient` for the route layer.

### 7.3 Deep adapter test — schema-valid + number-check

- `tests/test_deep_adapter.py`. **Stub `gateway.client.generate_sync`** (monkeypatch) so no network/Ollama is needed:
  1. Stub returns a schema-valid `Recommendation` JSON whose every numeric figure in `rationale`/`evidence` is present in the snapshot (drawn from `summarize(df)` values, e.g. the exact `price.current`/`RSI`) → `analyze(...)` returns a `Recommendation` with `source="deep"`, `model_id` set (the gateway model), `prompt_version == "deep-v1"` (= `DEEP_PROMPT_VERSION`), `snapshot_id` matching `compute_snapshot_id` for the same input (so it equals the quant snapshot id, section 8.1).
  2. Stub returns a response with a **planted hallucinated figure** — a number chosen outside both `NUMBER_CHECK_REL_TOL` (1%) and `NUMBER_CHECK_ABS_TOL` (0.01) of every allowable value (section 4.2 (a)–(c)) → assert `analyze(...)` raises `NumberCheckError` and `error.offending` names the planted figure. Also assert a boundary case: a figure within 1% of a snapshot value (e.g. `57.3` vs stored `57.29`) passes.
  3. **Signed-value regression (resolves review finding 1):** build the snapshot so `summarize(df)` holds a negative `momentum.MACD` (e.g. `-0.42`). Stub (a) returns a rationale correctly citing `MACD = -0.42` → asserts the faithful negative figure **passes** (no `NumberCheckError`); stub (b) returns a rationale citing the sign-flipped `MACD = +0.42` (a value not in the snapshot) → asserts it is **rejected**. This proves the number-check preserves the sign on both sides and no longer rejects legitimate negative indicator values.
- Also assert the adapter never imports FastAPI (structural check that it is a plain callable).

### 7.4 Integration test — quant + deep on the same snapshot, conflict flag

- `tests/test_integration_slice.py`. Run `quant.recommend.recommend` and `deep.adapter.analyze` on the **same synthetic daily snapshot** (deep's gateway call stubbed to a schema-valid, number-clean response). Assert both return valid `Recommendation`s whose `snapshot_id` are **equal** — both computed by the one `compute_snapshot_id` helper (section 8.1), which is the join key the fusion step relies on.
- **Conflict behavior (rule 4 / §5) — real slice helper (resolves review finding 7).** The slice ships a small, clearly-slice-scoped function `fuse(recs: list[Recommendation]) -> dict` living in `backend/app/schemas/fusion.py` (next to the contract it operates on), returning `{"recommendations": list[Recommendation], "conflict": bool}` where `conflict = len({r.action for r in recs}) > 1` and the input `Recommendation` objects are returned **verbatim** in `recommendations` — no averaging, no mutation of `action`/`confidence`. Test 7.4 asserts against this real function: when quant and deep actions differ, `conflict is True` and both original objects are present with their original `action`/`confidence` unchanged; when they agree, `conflict is False` and both are still retained. The module docstring states explicitly that **Step 5.1 replaces this with the product fusion view** (the UI side-by-side); `fuse()` is the minimal slice-level enforcement of the invariant, not the final product surface.

### 7.5 Stability + structured-output validity rate

- Run the **full suite 3 consecutive times** to prove stability: `.venv\Scripts\python.exe -m pytest` ×3 (a small PowerShell loop in the runbook, or `pytest-repeat` is **not** added to keep the pin list fixed — use three explicit invocations). All three runs must pass identically (offline tests deterministic; the live gateway test skips or passes consistently).
- **Structured-output validity rate (resolves review finding 8):** when Ollama is up, a dedicated `tests/test_gateway_validity_rate.py` (Ollama-gated) issues exactly **`SLICE_VALIDITY_N = 20`** schema-constrained generations — a fixed, named constant defined in that test module, so the assertion denominator is unambiguous (the full 100-run §1.4 harness is a Step 1 scorecard task, not this slice). It computes `valid_count / SLICE_VALIDITY_N` and asserts `>= 0.95`. **Gate G1 targets ≥95%**; the test records the measured rate into stdout and asserts only when Ollama is present (skips with a clear message otherwise). The one-retry path counts a generation as valid if the retry validates. This rate is also derivable after the fact from `gateway/logs/calls.jsonl` (`valid` field) — the harness can read the log to report the rate across all runs.

### 7.6 What is unit vs integration

- **Unit (offline, deterministic, always run):** quant indicators/regime/recommend, deep adapter with stubbed gateway (both number-check branches), the gateway's hash-embed placeholder, the JSONL logger, the schema validator, the priority-gate ordering. The priority gate is now `threading`-based (section 2.5), so it is unit-tested without an event loop: stub the Ollama call with a fake slow HTTP client, launch a `/deep` call on one thread, then a `/quick` call on another, and assert the `/quick` completes first (or that a `/deep` waiting while `_quick_waiting > 0` yields). Crucially, a regression test calls the synchronous `generate_sync` **twice in a row in-process** and asserts both succeed — proving the old `asyncio.run`-per-call "bound to a different event loop" failure (review finding 2) cannot recur.
- **Integration (Ollama-gated, skip if unreachable):** the live gateway text/JSON/retry/num_ctx/log-line test and the validity-rate harness.
- **Cross-engine integration (offline, deep stubbed):** the quant+deep same-snapshot conflict-flag test.

A design that forces the LLM into unit tests would be flaky; isolating the only non-deterministic dependency (Ollama) behind a skip-gate and stubbing it everywhere else keeps the suite stable across the 3× runs.

---

## 8. Shared snapshot + fusion helpers (new in iteration 2)

Two findings (4 and 7) require one concrete, shared implementation so quant and deep cannot diverge. Both live in the contract package `backend/app/schemas/` alongside `recommendation.py`, because both are contract-adjacent: one computes the contract's `snapshot_id`, the other enforces the contract's conflict/no-average invariant.

### 8.1 `backend/app/schemas/snapshot.py` — `compute_snapshot_id` (resolves review finding 4)

One helper, imported by **both** `quant/recommend.py` and `deep/adapter.py`:

```python
SNAPSHOT_TAIL_N = 60

def compute_snapshot_id(ticker: str, as_of: date, df: pd.DataFrame) -> str: ...
```

Canonicalization is pinned exactly so the hash is byte-stable across runs and machines (Gate G3):

1. Take the last `SNAPSHOT_TAIL_N` (= 60) rows of `df` (`df.tail(60)`), preserving row order.
2. Select a **fixed, sorted** column set, intersected with the columns present, applied in this literal sorted order:
   `["ATR","BB_Lower","BB_Middle","BB_Upper","BB_Width","Close","EMA_12","EMA_26","High","Low","MACD","MACD_Histogram","MACD_Signal","Open","RSI","SMA_20","SMA_50","Volume","Volume_Ratio","Volume_SMA"]`.
   Columns not present in `df` are skipped (never error); no other columns are hashed.
3. Build a canonical string: start with a header line `f"{ticker}|{as_of.isoformat()}|{','.join(selected_cols)}"`, then one line per row (oldest→newest) of the selected cell values joined by `,`. Each cell is formatted by a single rule: floats and ints as `f"{float(v):.6f}"`; `NaN`/`None` rendered as the literal string `nan`. (Rendering ints through the float format keeps one branch; `Volume` becomes e.g. `1000000.000000`, which is stable.)
4. `snapshot_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]` (16 hex chars — compact, collision-safe for this scale, and `snapshot_id` is a plain `str` in the contract).

This removes every unspecified degree of freedom the review flagged: N is 60, float format is `:.6f`, NaN is literal `nan`, column order is the fixed sorted list, and truncation is `[:16]`. Determinism holds because the rule-based path has no `datetime.now()`/random calls (verified) and the df is the only input.

### 8.2 `backend/app/schemas/fusion.py` — `fuse` (resolves review finding 7)

```python
def fuse(recs: list[Recommendation]) -> dict:
    actions = {r.action for r in recs}
    return {"recommendations": list(recs), "conflict": len(actions) > 1}
```

No averaging, no mutation — inputs are returned verbatim. `conflict` is true iff the engines disagree on `action`. This is the slice-level enforcement of rule 4 / unification-plan §5; the docstring records that **Step 5.1 replaces it** with the product fusion view. Test 7.4 asserts against this function directly.

### 8.3 Package-marker requirement

`backend/app/schemas/` must be an importable package. The implementer adds any missing `__init__.py` under `backend/`, `backend/app/`, and `backend/app/schemas/` so `from backend.app.schemas.snapshot import compute_snapshot_id` and `from backend.app.schemas.fusion import fuse` resolve under the editable install (section 1).

---

## Open structural questions

None blocking the implementer. The one upstream open item is the exact TradingAgents pin (SHA/tag) in decision 0003 — the slice is designed to proceed under the documented-blocker fallback (section 5.4) regardless, and the real graph call is a marked TODO (section 4.3) that does not change the adapter contract. Golden-ticker exact list (unification-plan 0.7) is a Step 0 user-confirmation item; the quant/integration tests here use synthetic fixtures and do not depend on it.

---

## Review responses

### Iteration 3 — resolves the iteration-2 review (`slice-design-review.json`: 0 HIGH, 2 MEDIUM, 1 NIT; verdict CHANGES_REQUESTED)

All three findings are **addressed** — none backlogged or ignored. Each aligns directly with a requirement already in scope (rule 3 number-check / Gate G4 for finding 1, the single-model single-request gateway for finding 2, and the `/embed` contract for finding 3), so there is nothing to justify-ignore. The real source (`c:\GIT\Fintech\Not-a-Finance-advice-\backend\data\indicators.py`, `agents\strategy_agent.py`) was re-read to confirm that `summarize(df)` genuinely produces signed values (`price.change_pct`, `momentum.MACD`, `MACD_Signal`, `MACD_Histogram`), which is exactly what made finding 1 real.

| # | Sev | Area | Resolution | Where |
| --- | --- | --- | --- | --- |
| 1 | MED | Number-check strips sign from model figures but keeps signs on allowable values → faithful negatives rejected | **Addressed.** Extraction now **preserves the sign** (drop a leading `+` only, keep `-`), parsing a signed float; comparison is signed on both sides via `abs(x - a) <= max(ABS_TOL, REL_TOL*abs(a))`. A correctly-cited `MACD = -0.42` matches the stored `-0.42`; a sign-flipped `+0.42` is rejected. State in 4.2(b). New signed-value regression in test 7.3. | 4.2(b), 4.2(c), 7.3 |
| 2 | MED | Module-level `asyncio.Lock` driven through per-call `asyncio.run` → "bound to a different event loop" on the 2nd in-process call | **Addressed.** Replaced with a loop-agnostic `threading.Lock` (`_ollama_lock`) + `threading.Condition` (`_priority_cv`) guarding `_quick_waiting`; `generate`/`generate_sync` are plain synchronous functions (no per-call loop). Async FastAPI routes offload the blocking core via `anyio.to_thread.run_sync` and **share the one gate instance** from `client.py`, so uvicorn `/quick` and an in-process `/deep` job serialize against each other. Regression test calls `generate_sync` twice in-process. | 2.5, 7.6 |
| 3 | NIT | `/embed` named two Ollama endpoints (`/api/embed` vs `/api/embeddings`) | **Addressed.** One endpoint throughout: `POST {OLLAMA_BASE_URL}/api/embed` (current Ollama route); the `/api/embeddings` alias is not used anywhere. | 2.6 |

### Iteration 2 — resolved the prior review (2 HIGH, 6 MEDIUM, 4 NIT)

Iteration 2 resolved the prior `slice-design-review.json` (verdict CHANGES_REQUESTED: 2 HIGH, 6 MEDIUM, 4 NIT). Every finding was **addressed** — none backlogged or ignored, because each aligns directly with a requirement already in scope (the one-`Recommendation` contract, determinism/Gate G3, rule-3 number-check/Gate G4, and the hard hardware caps). The source files named in the review were re-read to pin the fixes to the real APIs. The iteration-2 review verified all of these resolutions correct against the real source (its "Verified Assumptions" list); they are carried forward unchanged in iteration 3.

| # | Sev | Area | Resolution | Where |
| --- | --- | --- | --- | --- |
| 1 | HIGH | DataPipeline daily `as_of` call | **Addressed.** Pinned `DataPipeline(ticker=ticker, interval="1d")` + `get_enriched_data(end_date=as_of.isoformat(), interval="1d", use_cache=False)`; daily interval mandatory; call skipped when `df` injected for offline tests. | 3.5 |
| 2 | HIGH | `horizon_days` units | **Addressed.** `StrategyAgent(interval="1d", horizon=HORIZON_DAYS)` with `HORIZON_DAYS=10`; map `horizon_days` only when `interval=="1d"` (one period = one day); `ValueError` guard on non-daily df. | 3.5, constants table |
| 3 | MED | Gateway schema validation | **Addressed.** One validator: `json.loads` → `jsonschema.validate(obj, json_schema)`; `jsonschema==4.*` added to requirements; `Recommendation(**obj)` reserved for the deep adapter's own post-gateway check. | 2.4, 6.1 |
| 4 | MED | `snapshot_id` determinism | **Addressed.** New shared `backend/app/schemas/snapshot.py::compute_snapshot_id`: last 60 rows, fixed sorted column set, `f"{float(v):.6f}"`, literal `nan`, `sha256(...).hexdigest()[:16]`; imported by both quant and deep. | 8.1, 3.5 |
| 5 | MED | Deep number-check | **Addressed.** Allowable set = recursive numeric leaves of `summarize(df)` + last 60 raw OHLCV + `regime.confidence` + validated portfolio; extraction regex (strip sign/commas/`%`/`x`); compare 1% rel OR 0.01 abs; `NumberCheckError(offending=[...])`. | 4.2 |
| 6 | MED | `/embed` dimension | **Addressed.** `HASH_EMBED_DIM=256` fixed; response carries `dim`+`method`; callers branch on `method`; projection-to-256 rule stated for any future single-dim store (Step 2). | 2.6, constants table |
| 7 | MED | Fusion helper home | **Addressed.** Real slice-scoped `backend/app/schemas/fusion.py::fuse(recs) -> {recommendations, conflict}`, `conflict = len({r.action}) > 1`, no averaging; test 7.4 asserts against it; Step 5.1 replaces it. | 8.2, 7.4 |
| 8 | NIT | Validity-rate N | **Addressed.** `SLICE_VALIDITY_N = 20` fixed constant in `tests/test_gateway_validity_rate.py`. | 7.5, constants table |
| 9 | NIT | Prompt length cap | **Addressed.** `MAX_PROMPT_CHARS = 24000` (~6k tokens); over-cap → HTTP 422 `prompt_too_long`, no model call/log. | 2.8, 2.2 |
| 10 | NIT | Deep `prompt_version` | **Addressed.** `DEEP_PROMPT_VERSION = "deep-v1"`; bumped when the real TradingAgents graph is wired. | 4.2, 4.3, constants table |
| 11 | NIT | Quant `evidence` | **Addressed.** Pinned `build_evidence` with a fixed ordered key list and `f"{v:.4f}"` formatting from `indicators_snapshot`; reproducible for identical input. | 3.5 |

No finding conflicts with the original requirements, so there is nothing to justify-ignore. The two HIGH fixes (daily interval + day-denominated horizon) and the determinism/number-check specs were all confirmed against the actual source signatures in `c:\GIT\Fintech\Not-a-Finance-advice-\backend\` (`config.py`, `data/pipeline.py`, `data/indicators.py`, `agents/strategy_agent.py`, `agents/regime_agent.py`) during this revision. Hardware constraints from decision 0004 (llama3.2 both tiers, `num_ctx` hard-capped at 8192, no 8B, no 8 GB VRAM) were preserved unchanged — no finding touched them.

---

## CARRIED-FORWARD REQUIREMENTS (design loop ended at iteration 3 — orchestrator decision)

The design loop was ended with these THREE findings from `slice-design-review.json` (iteration 3) still open. They are NOT dismissed — they are mandatory implementation requirements for the build phase, and the CODE REVIEW must verify each against running code. They were moved to the build phase because they are narrow implementation details better proven on executing code than in a text spec; every load-bearing design point (single Recommendation contract, import wiring, hardware caps llama3.2/num_ctx=8192, daily-interval + horizon fix, signed number-check, snapshot determinism, threading-lock gateway concurrency, offline-safe tests) was verified sound.

**REQ-1 (was MEDIUM finding 1) — number-check allowable set must include regime figures.**
The deep prompt is built from indicator summary + recent bars + regime + portfolio. `RegimeAgent` reasoning/`indicators_used` contain figures NOT in `summarize()` (e.g. `net_change_pct`, `atr_pct`, `rsi_avg`, `price_range_pct`, `avg_volume_ratio`, `sma20_slope_pct`). Implement ONE of:
  (a) extend the allowable-set builder to also recursively collect numeric leaves of `regime.indicators_used`, OR
  (b) feed only the regime label + `regime.confidence` into the prompt (drop `reasoning`/`indicators_used`).
Choose (a) if regime context stays in the prompt. Add a regression test: a deep report that faithfully cites a regime figure (e.g. `net_change_pct`) must PASS the number-check.

**REQ-2 (was MEDIUM finding 2) — live quant path must fetch enough bars for the 60-bar snapshot window.**
The live `get_enriched_data` call must pass an explicit lookback so ≥ `SNAPSHOT_TAIL_N` (60) daily bars survive the `dropna` on `SMA_50` (50-bar warmup). Use `get_enriched_data(end_date=as_of.isoformat(), interval="1d", lookback_days=200, use_cache=False)` and assert the invariant `len(df) >= SNAPSHOT_TAIL_N` (or document+implement an explicit short-history fallback). Offline tests inject ≥60 warmed bars and must keep doing so.

**REQ-3 (was NIT finding 3) — finish the StrategyAgent provider-dispatch rewrite.**
When dropping the `openai` method and rerouting LLM calls through the gateway, also rewrite `StrategyAgent.__init__` provider resolution and `predict()` dispatch: `self.llm_provider = llm_provider or ("gateway" if use_llm else Config.DEFAULT_LLM_PROVIDER)`; `predict()` dispatches `"gateway"` to the new gateway method, else rule-based; REMOVE the `OPENAI_API_KEY` branch entirely (no dangling `openai`/`ollama` routes). Grep the migrated quant for `openai`/`OPENAI` afterward and confirm none remain active.
