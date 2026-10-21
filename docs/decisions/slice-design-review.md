# Slice Design Review (fresh, iteration-3)

Reviewed document: `docs/decisions/slice-design.md` (iteration 3, self-reported 0 HIGH / 2 MEDIUM / 1 NIT resolved).
Governing context read: `docs/unification-plan.md`, `docs/decisions/0004-hardware-adapted-model-strategy.md`, `backend/app/schemas/recommendation.py`.
Source re-read to verify claims: `c:\GIT\Fintech\Not-a-Finance-advice-\backend\{config.py, data/pipeline.py, data/fetcher.py, data/indicators.py, agents/strategy_agent.py, agents/regime_agent.py}`; MONO tree, `.gitignore`, git state.

This review was done without the context that produced the design. The design is unusually thorough and most load-bearing claims check out against the real source. The findings below are the places where it still does not stand alone.

---

## Findings

### 1. MEDIUM — Number-check allowable set omits the regime signal figures that the design feeds into the deep prompt

Where: section 4.2 step 2 (prompt build) vs 4.2(a) (allowable-numbers set).

The deep prompt is built from "indicator summary, recent bars, **regime**, and portfolio context" (4.2 step 2). The real `RegimeAgent` (`agents/regime_agent.py`) produces a `reasoning` string and an `indicators_used` dict that contain numeric figures the summarize dict does **not** hold — e.g. `net_change_pct`, `sma20_slope_pct`, `price_vs_sma20`, `atr_pct` (ATR as a *percent of price*, distinct from the stored `volatility.ATR` absolute value), `rsi_avg`, `price_range_pct`, `avg_volume_ratio`. `_build_reasoning` literally emits strings like `"net price change +3.2%"`, `"RSI at 57"`, `"ATR is 2.40% of price"`, `"Bollinger width 0.0421"`.

But the allowable set in 4.2(a) is only: summarize leaves + last 60 raw OHLCV + `regime.confidence` + validated portfolio. It does **not** include `regime.indicators_used` values. So if the model faithfully echoes a regime figure it was given in the prompt (e.g. `atr_pct = 2.40`, or `net_change_pct = 3.2`), the number-check will find no allowable match and raise `NumberCheckError` on a correct, non-hallucinated citation. That both fails real reports and inflates the Gate G4 "< 5% number-check failure" metric with false positives.

Concrete fix: extend 4.2(a) to also walk `regime.indicators_used` (recursively collect every numeric leaf) into the allowable set, OR explicitly exclude regime figures from the prompt (do not pass `regime.reasoning`/`indicators_used` into the model, pass only the regime label + `regime.confidence`). State which. If keeping regime context in the prompt, the allowable set line becomes:

```
allowable += numeric_leaves(regime.indicators_used)
allowable += [regime.confidence]
```

### 2. MEDIUM — Live-path lookback is too short to produce the 60-bar window the snapshot and number-check are specified around

Where: section 3.5 (live-path call) + 8.1 (`SNAPSHOT_TAIL_N = 60`) + 4.2(a) ("last 60 bars").

The only concrete live call is `DataPipeline(ticker, interval="1d").get_enriched_data(end_date=as_of.isoformat(), interval="1d", use_cache=False)`. `get_enriched_data` passes `lookback_days=None`, which `fetch_historical` (verified in `data/fetcher.py`) defaults to `Config.DEFAULT_LOOKBACK_DAYS = 120` *calendar* days. 120 calendar days ≈ 82 trading days; `get_enriched_data` then `dropna`s on `SMA_50`, which needs 50 warmed bars, leaving roughly 30 usable daily rows. `df.tail(60)` therefore returns ~30 rows on the live path, not 60.

This is not a determinism bug (same input still hashes the same), but the snapshot id and the number-check allowable set are designed and tested around a 60-bar window, and the live path silently delivers about half. Offline tests mask it because they inject a synthetic df with ≥60 warmed bars (7.1).

Concrete fix: pass an explicit lookback on the live call so ≥ `SNAPSHOT_TAIL_N + warmup` daily bars survive `dropna`. For daily bars with `SMA_50` warmup + 60 tail, request ~200 calendar days:

```python
df = pipe.get_enriched_data(end_date=as_of.isoformat(), interval="1d",
                            lookback_days=200, use_cache=False)
```

and state the minimum-bar invariant (`len(df) >= SNAPSHOT_TAIL_N` or document the "fewer than 60 on short history" fallback explicitly).

### 3. NIT — `strategy_agent` provider-resolution logic must be edited, not just the two LLM methods

Where: section 3.4.

3.4 says "delete the `openai` path" and "replace the direct-Ollama path", and that `llm_provider` values become `"rule_based"` / `"gateway"` with `use_llm=True` routing to `"gateway"`. The real `StrategyAgent.__init__` (verified) resolves the provider with `if llm_provider: ... elif use_llm and Config.OPENAI_API_KEY: self.llm_provider = "openai" else default`, and `predict()` branches on `self.llm_provider == "openai" / "ollama"`. Deleting the two methods without rewriting `__init__`'s `elif use_llm and Config.OPENAI_API_KEY: "openai"` branch and the `predict()` dispatch leaves a dangling `"openai"`/`"ollama"` route. The design implies this but does not call out the `__init__`/`predict` dispatch edit explicitly.

Concrete fix: add to 3.4 that `__init__` provider resolution becomes `self.llm_provider = llm_provider or ("gateway" if use_llm else Config.DEFAULT_LLM_PROVIDER)` and `predict()` dispatches on `"gateway"` → the new gateway method, else rule-based; the `OPENAI_API_KEY` branch is removed.

---

## Verdict inputs

HIGH: 0. MEDIUM: 2. NIT: 1. → **CHANGES_REQUESTED** (MEDIUM count > 0).

No finding touches the hardware constraints: the design uses `llama3.2` for both tiers, hard-caps `num_ctx` at 8192 at a single enforcement point, pulls no 8B model, and assumes no 8 GB VRAM — all consistent with decision 0004. No finding requires live network to pass tests (the live gateway test and validity-rate harness both skip when Ollama is unreachable; everything else is offline/stubbed/deterministic).

---

## Verified Assumptions (checked against real source)

- **Single `Recommendation` contract.** `backend/app/schemas/recommendation.py` defines exactly one `Recommendation` + `Action`; `source` is `Literal["quant","deep","advisor"]`, `model_id` is nullable (rule-based quant → `None`), `snapshot_id` is a plain `str`. The design's mapping (quant `source="quant"`/`model_id=None`; deep `source="deep"`/`model_id=llama3.2`) fits the contract. Verified.
- **Shared-import mechanism is unambiguous.** Section 1 picks exactly one mechanism (option B): a root `pyproject.toml` installed `pip install -e .`, with explicit `packages = ["backend","quant","deep","gateway"]`, and the single import line `from backend.app.schemas.recommendation import Recommendation, Action`. Option A is explicitly rejected with a reason. One mechanism, not "A or B". Verified unambiguous.
- **Package markers genuinely missing.** MONO `backend/` currently has no `__init__.py` at any level under `backend/app/schemas/`; the design correctly requires the implementer to add them (1 and 8.3). Verified.
- **`StrategyAgent` signature.** `__init__(horizon, use_llm, lessons, interval, llm_provider, asset_class)` — so `StrategyAgent(interval="1d", horizon=HORIZON_DAYS)` is valid. Verified.
- **`horizon_days` is a legacy period count = `self.horizon`.** `Prediction.horizon_days` is set to `self.horizon` and documented "Legacy field name kept for API compatibility". Design's daily-bar fix (one period = one day only when `interval=="1d"`, with a `ValueError` guard) is the correct remedy. Verified.
- **Defaults that force the fix.** `Config.DEFAULT_INTERVAL = "30m"`, `DEFAULT_PREDICTION_HORIZON = 2`, `DEFAULT_LLM_PROVIDER = "rule_based"`. Verified — the design's explicit `interval="1d"` pin is necessary.
- **Rule-based determinism.** `_predict_rule_based` and `RegimeAgent.classify` contain no `datetime.now()`/random; confidence is `round(min(confidence, 0.95), 3)`. Section 8.1's determinism claim holds. Verified.
- **In-function import.** `strategy_agent._score_regime` does `from backend.agents.regime_agent import MarketRegime` — the design's prefix-swap rule accounts for it. Verified.
- **Three LLM modes and their transports.** `openai` via `langchain_openai.ChatOpenAI`; `ollama` via direct `httpx.post(f"{OLLAMA_BASE_URL}/api/generate", ...)`. The design's "delete openai, reroute ollama through the gateway" matches the real code. Verified.
- **`get_enriched_data` signature.** Accepts `start_date, end_date, lookback_days, interval, use_cache` — the design's call keywords are all valid. Verified (but see finding 2 on the missing lookback).
- **`summarize(df)` is a nested dict with signed values.** `price.change_pct`, `momentum.MACD`/`MACD_Signal`/`MACD_Histogram` are genuinely signable (MACD = ema_fast − ema_slow); `volume.Volume` is an `int`; values rounded 2–4 dp. This is exactly what makes the iteration-3 signed-number-check fix real. Verified.
- **Evidence key list exists in `summarize`.** All ten keys in 3.5 (`price.current`, `price.change_pct`, `moving_averages.SMA_20/50`, `momentum.RSI/MACD/MACD_Signal`, `volatility.ATR/BB_Width`, `volume.Volume_Ratio`) are produced by `summarize`; `price.current` is unconditional, so `evidence` is non-empty. Verified.
- **Snapshot column set matches daily output.** The 8.1 sorted column list (`ATR, BB_*, Close, EMA_12/26, High, Low, MACD*, Open, RSI, SMA_20, SMA_50, Volume, Volume_Ratio, Volume_SMA`) matches the columns the indicators actually add for `interval="1d"` (`get_indicator_periods("1d")` → SMA_20/SMA_50). Verified.
- **`RegimeClassification.confidence` exists** and `RegimeAgent()` constructs with no args. Verified (and relevant to finding 1).
- **`.BO`/`.NS` pass-through.** `Config.resolve_ticker` returns stock tickers unchanged; yfinance accepts `.BO`/`.NS`. Design's "no change needed" is correct. Verified.
- **Git / skeleton / gitignore state.** MONO is not yet a git repo (nor is the parent); skeleton dirs exist; `.gitignore` covers `.venv/` but not `gateway/logs/`, and `.jsonl` is not matched by the existing `*.log` rule — the design correctly requires adding a `gateway/logs/` line (5.1). Verified.
- **`run.py` / `server.py` drop.** `server.py` is under `backend/`; `run.py` sits at the SRC-Q repo root. Since section 3.1 copies only `backend/`, `run.py` is out of the copy scope anyway, and `server.py` is correctly excluded. The design's "drop server/CLI" outcome is correct. Verified.
- **Gateway constraints coherent.** Single `num_ctx` enforcement point (`min(GATEWAY_NUM_CTX, 8192)`), one `jsonschema` validator, one-retry-then-structured-error (HTTP 200 `valid=false`), `MAX_PROMPT_CHARS=24000` pre-send 422, single `/api/embed` endpoint with a deterministic 256-dim hash fallback, per-call JSONL fields all enumerated, importable core free of FastAPI. Internally consistent and server-free-importable as claimed. Verified by inspection (no live Ollama needed).
- **Priority gate is loop-agnostic.** `threading.Lock` + `Condition` guarding `_quick_waiting`, one shared gate instance in `client.py`, async routes offloading via `anyio.to_thread.run_sync`; the old `asyncio.run`-per-call defect is removed and a twice-in-a-row `generate_sync` regression test is specified (2.5, 7.6). Design is self-consistent. Verified by inspection.
- **Offline-safe test strategy.** Quant tests inject synthetic df (no yfinance); deep tests stub `generate_sync` (both pass and `NumberCheckError` branches, plus the signed-value regression); the real-Ollama gateway test and the `SLICE_VALIDITY_N=20` validity-rate harness both `pytest.skip` when Ollama is unreachable; the conflict-flag integration test runs offline against the real `fuse()`; stability = 3× explicit `pytest` runs. No test needs live network to pass. Verified against the described structure.
- **Documented-blocker submodule fallback.** If `submodule add` fails, `.gitkeep` + `third_party/README.md` + `status.md` note keep the path tracked, and the deep slice (gateway stub + number-check) does not depend on the submodule, so the slice stays testable. Coherent. Verified.

## Unverified / Wrong Assumptions

- **Wrong (finding 1):** the claim that the 4.2(a) allowable set is sufficient to accept all faithful deep-report figures is incorrect — regime reasoning figures fed into the prompt are not in the set.
- **Incomplete (finding 2):** the live-path lookback is unspecified and defaults to a value that cannot fill the 60-bar window the rest of the design assumes.
- **Incomplete (finding 3):** the `StrategyAgent.__init__`/`predict` provider-dispatch rewrite is implied but not stated.
- **Unverified (no live dependency available):** actual Ollama behavior for `format=<json_schema>` structured output, the real tokens/sec and whether `/deep` generations complete within `GATEWAY_REQUEST_TIMEOUT_S=120`, and whether `/api/embed` returns a usable vector for `llama3.2` vs falling back to the hash placeholder. These are correctly gated behind skip-on-unreachable tests, so they do not block implementation; they are flagged only as items that cannot be confirmed statically.
- **Unverified (out of slice / upstream):** the exact TradingAgents pin SHA (decision 0003 open item) — the design proceeds under the documented-blocker fallback and a marked TODO, so this does not block the slice.
