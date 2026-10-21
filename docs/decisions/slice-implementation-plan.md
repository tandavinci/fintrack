# Implementation Plan — Unified Local Trading Intelligence Platform (vertical slice)

Date: 2026-10-21
Source of truth: `docs/decisions/slice-design.md` (APPROVED iteration 3). This plan sequences that design into an ordered, file-by-file task list. It does not re-decide architecture; where the design leaves an implementation choice, it is resolved here with a one-line rationale.

## Environment facts discovered during exploration (read before starting)

- **Git**: `c:\GIT\Fintech\repo` is NOT its own repo yet. It sits inside the working tree of an unrelated repo (`tandavinci/Konnect`, `.git` at `C:\GIT\.git`, HEAD on "DocAI" work). `git init` at `c:\GIT\Fintech\repo` creates a nested, independent repo whose inner `.git` shadows the outer one for everything under `repo` — this is correct and intended. **Every git command MUST be scoped with `git -C c:\GIT\Fintech\repo ...`** so nothing is ever staged into the Konnect repo. Never run `git add` from a parent directory.
- **Toolchain**: `py --version` → Python 3.12.0. Bare `python`/`python3` are broken Store stubs — never call them. Venv interpreter after creation: `c:\GIT\Fintech\repo\.venv\Scripts\python.exe`.
- **Ollama**: reachable at `http://localhost:11434`; `llama3.2:latest` (~2 GB, 3B) is pulled. The live gateway tests and the validity-rate harness WILL run (not skip).
- **GitHub**: reachable — the real `git submodule add` for TradingAgents is expected to succeed; the documented-blocker fallback (design §5.4) is the contingency only.
- **Existing files**: `backend/app/schemas/recommendation.py` is the single contract (do not redefine). A stray `backend/app/schemas/__pycache__/` exists — covered by the existing `.gitignore` `__pycache__/` rule, so it will not be committed.
- **`.gitignore`**: already ignores `.venv/`, `__pycache__/`, `*.py[cod]`, `*.egg-info/`, `.pytest_cache/`, `.env*`. It does NOT yet ignore `gateway/logs/`, and `*.log` does NOT match `calls.jsonl`. A `gateway/logs/` line must be added (design §5.1).
- **Migration source**: `c:\GIT\Fintech\Not-a-Finance-advice-\backend\` — confirmed files: `config.py`, `data/{indicators,fetcher,pipeline}.py`, `agents/{regime_agent,strategy_agent}.py`, `backtesting/engine.py` (plus `server.py`/`__init__.py`/`requirements.txt`/`.env.example` which are NOT migrated). `run.py` is not under `backend/` and is out of copy scope.

All source claims in the design were verified against the actual files: `summarize(df)` is the nested dict with signed `MACD`/`MACD_Signal`/`MACD_Histogram`/`price.change_pct`; `_score_regime` has the in-function `from backend.agents.regime_agent import MarketRegime`; `StrategyAgent.__init__` resolves provider via `llm_provider > (use_llm and OPENAI_API_KEY) > DEFAULT`; `predict()` dispatches on `"openai"`/`"ollama"`/else; `Config` holds `OPENAI_API_KEY`, `POLYGON_API_KEY`, `LLM_MODEL`, `OLLAMA_*`, `API_HOST/PORT` to trim; `engine.py` imports pipeline + both agents + Config.

---

## Phase 0 — foundation: git, packaging, deps, venv, shared helpers

- [ ] 1. Initialize the MONO git repo and amend `.gitignore`.
      Run `git -C c:\GIT\Fintech\repo init -b main` (nested repo; see environment facts). Add a `gateway/logs/` line to `.gitignore` so runtime call logs are never committed; keep `tests/golden/` CSVs tracked.
      Files: `c:\GIT\Fintech\repo\.gitignore` (create `.git` via init).
      Verify: `git -C c:\GIT\Fintech\repo rev-parse --git-dir` returns `.git` (not `C:/GIT/.git`), and `git -C c:\GIT\Fintech\repo status` lists the Fintech files only.

- [ ] 2. Add package markers and the root `pyproject.toml` (design §1, §8.3).
      Create empty `__init__.py` under `backend/`, `backend/app/`, `backend/app/schemas/`, and (placeholder) `quant/`, `deep/`, `gateway/`. Create `pyproject.toml` with `[build-system]` setuptools>=68, `[project]` name `local-trading-intel`/`requires-python=">=3.12"`/version `0.0.0`/empty `dependencies`, `[tool.setuptools] packages = ["backend","quant","deep","gateway"]` (explicit list — avoids scanning `third_party/`/`frontend/`), and `[tool.pytest.ini_options]` with `asyncio_mode="auto"` and `testpaths=["tests"]`.
      Decision: editable-install packaging (design option B) over a `conftest.py` sys.path hack, so imports resolve identically under pytest, uvicorn, scripts, and future Celery jobs.
      Files: `backend/__init__.py`, `backend/app/__init__.py`, `backend/app/schemas/__init__.py`, `pyproject.toml`.
      Verify: `py -c "import tomllib,pathlib; tomllib.loads(pathlib.Path(r'c:\GIT\Fintech\repo\pyproject.toml').read_text())"` parses without error.

- [ ] 3. Write `requirements.txt` (design §6.1).
      Pin: `fastapi==0.115.*`, `uvicorn==0.30.*`, `pydantic>=2,<3`, `pydantic-settings==2.*`, `httpx==0.27.*`, `jsonschema==4.*`, `pandas==2.2.2`, `numpy==1.26.4`, `ta==0.11.0`, `yfinance==0.2.40`, `pytest==8.*`, `pytest-asyncio==0.23.*`. Do NOT include `langchain*`, `openai`, `python-dotenv`. Add a short comment noting numpy pinned to 1.26.4 (matches pandas 2.2.2 wheels, avoids numpy-2 ABI churn) and the "allow newer for 3.12 with a noted reason" escape hatch.
      Files: `requirements.txt`.
      Verify: `Get-Content requirements.txt` shows all 12 pins and no forbidden packages.

- [ ] 4. Create the venv and install dependencies (design §6.2).
      `py -m venv c:\GIT\Fintech\repo\.venv`; then `c:\GIT\Fintech\repo\.venv\Scripts\python.exe -m pip install -r requirements.txt` and `... -m pip install -e .`. Use the venv interpreter for every later test run.
      Files: none (creates `.venv/`, gitignored).
      Verify: `c:\GIT\Fintech\repo\.venv\Scripts\python.exe -c "import fastapi, pydantic, httpx, jsonschema, pandas, numpy, yfinance, pytest; print('ok')"` prints `ok`.

- [ ] 5. Write the shared snapshot + fusion helpers (design §8.1, §8.2). Depends on items 2, 4.
      `backend/app/schemas/snapshot.py`: `SNAPSHOT_TAIL_N = 60` and `compute_snapshot_id(ticker, as_of, df) -> str` — last 60 rows in order, fixed sorted column list intersected with present columns, canonical string `f"{ticker}|{as_of.isoformat()}|{','.join(cols)}"` + one line per row (oldest→newest) of cells formatted `f"{float(v):.6f}"` with NaN/None as literal `nan`, then `sha256(...).hexdigest()[:16]`. `backend/app/schemas/fusion.py`: `fuse(recs) -> {"recommendations": list(recs), "conflict": len({r.action for r in recs}) > 1}`, verbatim objects, no averaging; docstring notes Step 5.1 replaces it.
      Files: `backend/app/schemas/snapshot.py`, `backend/app/schemas/fusion.py`.
      Verify: `c:\GIT\Fintech\repo\.venv\Scripts\python.exe -c "from backend.app.schemas.snapshot import compute_snapshot_id, SNAPSHOT_TAIL_N; from backend.app.schemas.fusion import fuse; print(SNAPSHOT_TAIL_N)"` prints `60` (these are also exercised by the quant/integration tests later).

- [ ] 6. Initial commit, then add the pinned TradingAgents submodule (design §5.2, §5.3, §5.4).
      Stage the skeleton, docs, `.gitignore`, `pyproject.toml`, `requirements.txt`, `__init__.py` markers, `snapshot.py`, `fusion.py` (specific paths, never `git add .`) and commit `step-0: monorepo skeleton and planning docs`. Then `git -C c:\GIT\Fintech\repo submodule add https://github.com/TauricResearch/TradingAgents third_party/TradingAgents`, check out an exact release tag or SHA (never a moving branch), stage `third_party/TradingAgents` + `.gitmodules`, record the resolved SHA into `docs/decisions/0003-tradingagents-pinning.md`, and commit `step-0: add pinned TradingAgents submodule`. If the add fails (network), use the documented-blocker variant: create `third_party/TradingAgents/.gitkeep` + `third_party/README.md` (documents the exact `submodule add` command + intended pin) + a `docs/status.md` blocker note, and commit the documented-blocker variant.
      Files: `docs/decisions/0003-tradingagents-pinning.md`, `.gitmodules` (+ `third_party/TradingAgents` gitlink) OR `third_party/TradingAgents/.gitkeep` + `third_party/README.md` + `docs/status.md`.
      Verify: `git -C c:\GIT\Fintech\repo log --oneline` shows both `step-0` commits; `git -C c:\GIT\Fintech\repo submodule status` shows a pinned SHA (success path) OR `third_party/README.md` exists and `docs/status.md` records the blocker (fallback path).

## Phase 1 — gateway (the only path to Ollama)

- [ ] 7. Build the gateway importable core and FastAPI app (design §2). Depends on items 2-5.
      Create `gateway/settings.py` (pydantic-settings `Settings`: `OLLAMA_BASE_URL`, `GATEWAY_MODEL="llama3.2"`, `GATEWAY_NUM_CTX=8192`, `GATEWAY_REQUEST_TIMEOUT_S=120.0`, `GATEWAY_MAX_RETRIES=1`, `GATEWAY_LOG_PATH`, `MAX_PROMPT_CHARS=24000`); `gateway/logging_jsonl.py` (append-only JSONL logger writing the §2.7 fields, creates `gateway/logs/`); `gateway/client.py` (the server-free core: sync `httpx` Ollama client, `validate(text, json_schema)` = `json.loads` + `jsonschema.validate`, `generate`/`generate_sync` with one-retry-then-structured-error, the `threading.Lock`/`Condition` priority gate with module-level `_ollama_lock`/`_priority_cv`/`_quick_waiting` so `/quick` preempts `/deep`, `HASH_EMBED_DIM=256` deterministic hash-embed fallback for `/embed`, `num_ctx = min(GATEWAY_NUM_CTX, 8192)` single enforcement point); `gateway/schemas.py` (pydantic request/response for the three routes); `gateway/app.py` (FastAPI `async def` routes `POST /quick|/deep|/embed` offloading to the shared core via `anyio.to_thread.run_sync`, importing the SAME gate instances from `client.py`); input validation per §2.8 (prompt non-empty, `len <= MAX_PROMPT_CHARS` else 422 `prompt_too_long` with no model call, `json_schema` must be dict, temperature clamp `[0,2]`, `max_tokens <= 2048`).
      Files: `gateway/__init__.py`, `gateway/settings.py`, `gateway/logging_jsonl.py`, `gateway/client.py`, `gateway/schemas.py`, `gateway/app.py`.
      Verify: `c:\GIT\Fintech\repo\.venv\Scripts\python.exe -c "import gateway.client; from gateway.app import app; print('imports ok, no server started')"` succeeds, confirming the core imports without starting a server and `app` constructs.

- [ ] 8. Write `gateway/client.py` thin re-export note and confirm in-process usability.
      Ensure `generate_sync` is importable as `from gateway.client import generate_sync` for quant/deep in-process callers (this is the client module the task requires). No separate server needed.
      Files: `gateway/client.py` (same file as item 7; this item only confirms the public surface).
      Verify: `c:\GIT\Fintech\repo\.venv\Scripts\python.exe -c "from gateway.client import generate_sync, validate; print('ok')"` prints `ok`.

- [ ] 9. Gateway unit tests — offline, deterministic (design §7.6). Depends on item 7.
      `tests/test_gateway_unit.py`: validator (valid JSON passes; `json_parse_error`; `schema_validation_failed`); hash-embed placeholder (deterministic, L2-normalized, `dim=256`); JSONL logger writes all required fields; priority gate ordering using a fake slow HTTP client on two threads (`/quick` completes first / a waiting `/deep` yields while `_quick_waiting>0`); the regression calling `generate_sync` twice in-process succeeds (proves no `asyncio.run`-per-call loop defect); the `num_ctx` cap (`min(…,8192)`); the `prompt_too_long` 422 path writes no log line.
      Files: `tests/test_gateway_unit.py`.
      Verify: `c:\GIT\Fintech\repo\.venv\Scripts\python.exe -m pytest tests/test_gateway_unit.py -q` — all pass.

## Phase 2 — quant package (migrate + rewire)

- [ ] 10. Migrate the six source modules into `quant/` with the import rewrite (design §3.1, §3.2, §3.3). Depends on items 2, 4.
      Copy `indicators.py`, `fetcher.py`, `pipeline.py` → `quant/data/`; `regime_agent.py`, `strategy_agent.py` → `quant/agents/`; `engine.py` → `quant/backtesting/`; `config.py` → `quant/config.py`. Add `quant/__init__.py`, `quant/data/__init__.py`, `quant/agents/__init__.py`, `quant/backtesting/__init__.py`. Apply the pure prefix swap `from backend.` → `from quant.` in all copied files (including the in-function `from backend.agents.regime_agent import MarketRegime` in `strategy_agent._score_regime`). Do NOT copy `server.py`/`run.py`/`__init__.py`/`requirements.txt`/`.env.example`. Record the source commit SHA in a one-line migration note at the top of `quant/__init__.py`.
      Files: `quant/__init__.py`, `quant/config.py`, `quant/data/{__init__,indicators,fetcher,pipeline}.py`, `quant/agents/{__init__,regime_agent,strategy_agent}.py`, `quant/backtesting/{__init__,engine}.py`.
      Verify: `c:\GIT\Fintech\repo\.venv\Scripts\python.exe -c "import quant.config, quant.data.pipeline, quant.agents.strategy_agent, quant.backtesting.engine; print('ok')"` prints `ok` with no `backend.`-import errors; `Select-String -Path quant\**\*.py -Pattern 'from backend\.'` returns nothing.

- [ ] 11. Trim `quant/config.py` and rewire `strategy_agent` LLM mode through the gateway (design §3.4; review NIT 3). Depends on items 7, 10.
      In `quant/config.py` drop `OPENAI_API_KEY`, `POLYGON_API_KEY`, `LLM_MODEL`, `OLLAMA_*`, `API_HOST`/`API_PORT` and the `dotenv` import/`load_dotenv()`; keep data/interval/indicator/backtest settings and `DEFAULT_LLM_PROVIDER="rule_based"`. In `strategy_agent.py`: delete `_predict_with_llm` + its `langchain_openai` import; replace `_predict_with_ollama` with a gateway-backed method using `from gateway.client import generate_sync` passing a small prediction JSON schema, keeping the parse-and-fall-back-to-rule-based behavior; rewrite `__init__` provider resolution to `self.llm_provider = llm_provider or ("gateway" if use_llm else Config.DEFAULT_LLM_PROVIDER)` (remove the `OPENAI_API_KEY` branch); rewrite `predict()` to dispatch `"gateway"` → the new method, else rule-based (remove the `"openai"`/`"ollama"` branches). Mirror the provider-resolution fix in `quant/backtesting/engine.py.__init__` (its `elif use_llm: "openai"` becomes `"gateway"`).
      Files: `quant/config.py`, `quant/agents/strategy_agent.py`, `quant/backtesting/engine.py`.
      Verify: `Select-String -Path quant\**\*.py -Pattern 'openai|langchain|OPENAI_API_KEY'` returns nothing; `c:\GIT\Fintech\repo\.venv\Scripts\python.exe -c "from quant.agents.strategy_agent import StrategyAgent; a=StrategyAgent(interval='1d', horizon=10); print(a.llm_provider)"` prints `rule_based`.

- [ ] 12. Write `quant/recommend.py` — the slice entry point (design §3.5). Depends on items 5, 11.
      `HORIZON_DAYS = 10`; `recommend(ticker, as_of, df=None) -> Recommendation`. Live path (only when `df is None`): `DataPipeline(ticker=ticker, interval="1d")` then `get_enriched_data(end_date=as_of.isoformat(), interval="1d", lookback_days=200, use_cache=False)` (explicit 200-day lookback so ≥60 warmed daily bars survive `dropna` — resolves review finding 2). Guard: raise `ValueError("recommend() requires daily bars")` if a supplied/resulting frame is non-daily. Run `RegimeAgent().classify(df)` then `StrategyAgent(interval="1d", horizon=HORIZON_DAYS).predict(df, regime)`. Map `Prediction` → `Recommendation`: `action=Action(prediction.signal.value)`, `confidence`, `horizon_days=prediction.horizon_days` (valid only because interval is daily), `rationale=prediction.reasoning`, `evidence` via a fixed-order `build_evidence(summary)` helper (the §3.5 ten-key ordered list, floats `f"{v:.4f}"`, ints `str(v)`, skip absent), `source="quant"`, `model_id=None`, `prompt_version=None`, `snapshot_id=compute_snapshot_id(ticker, as_of, df)`.
      Files: `quant/recommend.py`.
      Verify: exercised by item 13 (contract + quant tests); `c:\GIT\Fintech\repo\.venv\Scripts\python.exe -c "import quant.recommend; print(quant.recommend.HORIZON_DAYS)"` prints `10`.

- [ ] 13. Contract + quant tests — offline, deterministic (design §7.1; contract test G). Depends on item 12.
      `tests/test_contract.py`: `Recommendation` validates a good payload, rejects `confidence>1` (pydantic `ValidationError`), survives a JSON round-trip (`model_dump_json` → `model_validate_json`). `tests/test_quant_recommend.py`: build a synthetic DAILY OHLCV DataFrame (or small CSV under `tests/golden/`) with ≥60 warmed bars engineered to a known regime/signal — NO yfinance call; assert `source="quant"`, `model_id is None`, `snapshot_id` is 16 hex chars, `action in {BUY,HOLD,SELL}`, `0<=confidence<=1`, `horizon_days==10`, `evidence` non-empty + deterministic; reproducibility: two calls byte-identical (incl. `snapshot_id`, `evidence`); direct `compute_snapshot_id` unit test (same df → same id; one-cell change → different id); indicator/regime math on known inputs.
      Files: `tests/test_contract.py`, `tests/test_quant_recommend.py`, optional `tests/golden/*.csv`.
      Verify: `c:\GIT\Fintech\repo\.venv\Scripts\python.exe -m pytest tests/test_contract.py tests/test_quant_recommend.py -q` — all pass.

## Phase 3 — deep adapter

- [ ] 14. Write `deep/adapter.py` — plain callable + number-check (design §4). Depends on items 5, 7, 10.
      `deep/__init__.py` + `deep/adapter.py` with `analyze(ticker, as_of, portfolio=None) -> Recommendation`; module docstring forbids FastAPI import (plain callable, never in a web request). Constants `DEEP_PROMPT_VERSION="deep-v1"`, `NUMBER_CHECK_REL_TOL=0.01`, `NUMBER_CHECK_ABS_TOL=0.01`. Flow: build snapshot via the quant `DataPipeline` (same `compute_snapshot_id` as quant); build prompt from indicator summary + recent bars + regime + portfolio; call `generate_sync` with the `Recommendation`-derived JSON schema; construct `Recommendation(**obj)` (raise `DeepValidationError` on `valid=false`/bad fields); run the number-check (allowable set = recursive numeric leaves of `summarize(df)` + last 60 raw OHLCV + `regime.confidence` + `regime.indicators_used` numeric leaves [resolves review finding 1] + validated portfolio numerics; extract signed figures from `rationale`+`evidence` preserving sign, strip commas/`%`/`x`; compare signed within 1% rel OR 0.01 abs; raise `NumberCheckError(offending=[...])` on any unmatched figure); return `source="deep"`, `model_id`=gateway model, `prompt_version="deep-v1"`, shared `snapshot_id`. Add the marked TODO seam for the real TradingAgents graph call referencing `third_party/TradingAgents` and unification-plan Step 4.2; never edit TradingAgents source.
      Files: `deep/__init__.py`, `deep/adapter.py`.
      Verify: `c:\GIT\Fintech\repo\.venv\Scripts\python.exe -c "import deep.adapter, inspect; assert 'fastapi' not in inspect.getsource(deep.adapter).lower(); print('ok')"` prints `ok`.

- [ ] 15. Deep adapter tests — stubbed gateway (design §7.3). Depends on item 14.
      `tests/test_deep_adapter.py`, monkeypatching `gateway.client.generate_sync` (no network): (a) all-figures-in-snapshot response → `Recommendation` with `source="deep"`, `model_id` set, `prompt_version=="deep-v1"`, `snapshot_id` equals `compute_snapshot_id` for the same input; (b) planted hallucinated figure (outside both tolerances) → `NumberCheckError` naming the offender, plus a boundary case within 1% passes; (c) signed-value regression: snapshot with negative `MACD=-0.42`, faithful `-0.42` passes, sign-flipped `+0.42` rejected; structural assertion that the adapter never imports FastAPI.
      Files: `tests/test_deep_adapter.py`.
      Verify: `c:\GIT\Fintech\repo\.venv\Scripts\python.exe -m pytest tests/test_deep_adapter.py -q` — all pass.

## Phase 4 — integration, live tests, stability, wrap-up

- [ ] 16. Integration + live gateway + validity-rate tests (design §7.2, §7.4, §7.5). Depends on items 7, 12, 14.
      `tests/test_integration_slice.py`: run `quant.recommend.recommend` and `deep.adapter.analyze` (deep's gateway stubbed to a schema-valid, number-clean response) on the SAME synthetic daily snapshot; assert equal `snapshot_id`; assert `fuse([quant_rec, deep_rec])` keeps BOTH verbatim and `conflict is True` when actions differ, `False` when they agree (never averages). `tests/test_gateway_live.py`: probe `GET {OLLAMA_BASE_URL}/api/tags`, `pytest.skip` with a clear message if unreachable; else assert `/quick` returns non-empty text, a schema-constrained call returns valid JSON (allow one retry, `attempts<=2`), the last `gateway/logs/calls.jsonl` line has `num_ctx==8192` and all required fields (model id, prompt hash, tokens, latency, validation result). `tests/test_gateway_validity_rate.py`: `SLICE_VALIDITY_N=20` schema-constrained generations, assert `valid_count/20 >= 0.95`, skip if Ollama unreachable (Ollama IS up in this environment, so these run).
      Files: `tests/test_integration_slice.py`, `tests/test_gateway_live.py`, `tests/test_gateway_validity_rate.py`.
      Verify: `c:\GIT\Fintech\repo\.venv\Scripts\python.exe -m pytest tests/test_integration_slice.py tests/test_gateway_live.py tests/test_gateway_validity_rate.py -q` — all pass (live ones run since Ollama is reachable). If validity rate < 0.95, tighten the prompt/retry — never loosen the schema.

- [ ] 17. Full-suite stability — run 3× and measure (design §7.5; task G). Depends on item 16.
      Run `c:\GIT\Fintech\repo\.venv\Scripts\python.exe -m pytest` three consecutive times. All three must pass identically. Capture gateway tok/s and structured-output validity rate from `gateway/logs/calls.jsonl` (the `valid` field) and the test output. If any flakiness appears, fix by tightening prompts/retry, not by loosening the schema.
      Files: none (or a small PowerShell loop noted in the runbook).
      Verify: three consecutive `pytest` runs all report the same pass counts with no failures.

- [ ] 18. Wrap-up docs + import-wiring documentation (design §1 doc requirement, task E + H). Depends on item 17.
      Document the single shared-import mechanism (editable `pip install -e .`, the one import line `from backend.app.schemas.recommendation import Recommendation, Action`, re-run only when the package list changes) and the fresh-checkout order in `docs/runbook.md`. Update `docs/status.md` with what was built, measured results (gateway tok/s, structured-output validity rate over N=20, test pass counts across the 3 runs), and any blockers (e.g. submodule, if the fallback was used). Add a decision note for each material choice made during implementation (embedding hash fallback, editable-install import wiring) under `docs/decisions/`.
      Files: `docs/runbook.md`, `docs/status.md`, `docs/decisions/*` (new note(s)).
      Verify: `docs/runbook.md` contains the exact import line and `pip install -e .` step; `docs/status.md` Week section shows the measured numbers and the 3-run pass counts.

## Commit sequence (design §5.2) — commits created only as the workflow directs

`step-0: monorepo skeleton and planning docs` (item 6a) → `step-0: add pinned TradingAgents submodule` (item 6b, or documented-blocker variant) → `step-1: local LLM gateway` (items 7-9) → `step-3: quant core` (items 10-13) → `step-4: deep adapter` (items 14-15) → `step-?: integration + stability tests` (items 16-17) → wrap-up docs (item 18). Stage specific files, never `git add .`, always `git -C c:\GIT\Fintech\repo`. Do NOT push. Do NOT open a PR.

## Hard constraints (must hold throughout)

No paid APIs/keys — only the gateway talks to Ollama. Never edit TradingAgents source. Models explain, code computes — the deep number-check enforces it. Exactly one `Recommendation` schema, imported everywhere from `backend/app/schemas/recommendation.py`. The deep lane is never inside a web request. Every model call is logged to `gateway/logs/calls.jsonl`. Hardware: RTX 2050 / 4 GB VRAM / 31.5 GB RAM — `num_ctx` hard-capped at 8192, `llama3.2` for both tiers, no 8B model. Toolchain: `py` launcher creates the venv; all host Python runs through `c:\GIT\Fintech\repo\.venv\Scripts\python.exe`.
