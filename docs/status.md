# Status Log

Format per STEERING §11. Newest week on top.

## Week 2 (2026-10-21) — Unified slice complete (FEAT-001 … FEAT-005)

Current step / gate: Slice done; Gate G1 (structured-output validity) passed.

Built across all FEATs:
- FEAT-001 — local LLM gateway: server-free core (`gateway/client.py`) that is
  the only component talking to Ollama and the single `num_ctx=8192`
  enforcement point; `generate_sync` (/quick + /deep) with one-retry-then-
  structured-error; a `threading` priority gate shared by the async FastAPI
  routes and in-process callers; per-call JSONL logging; and the deterministic
  hash-embedding fallback. Pinned TradingAgents submodule added
  (`third_party/TradingAgents` @ `v0.7.0`, SHA
  `e8f5503e9b86d5c2710b0c76b1f686703968c9df`).
- FEAT-002 — shared contract: the ONE `Recommendation` schema plus the
  contract-adjacent `snapshot.py` (`compute_snapshot_id`, `SNAPSHOT_TAIL_N=60`)
  and `fusion.py` (`fuse` — keep both + conflict flag, never average).
- FEAT-003 — quant core: migrated rule-based library (`quant/`) with
  `recommend(ticker, as_of, df=None)`, deterministic evidence, daily-bar guard.
- FEAT-004 — deep adapter: `deep.analyze(...)` as a plain callable (no web
  framework import), gateway `/deep` call under the shared schema, and the
  sign-preserving number-check so no unchecked model figure escapes.
- FEAT-005 — integration + stability tests and this wrap-up: integration test
  (both lanes share one `snapshot_id`; `fuse` keep-both in both directions),
  live gateway test, and the N=20 validity-rate gate.

Measured results (from `gateway/logs/calls.jsonl`, this hardware — RTX 2050
4 GB, `llama3.2` 3B, `num_ctx=8192`):
- Structured-output validity rate: **20/20 = 1.000** on the dedicated
  `SLICE_VALIDITY_N=20` harness (≥ 0.95 Gate G1 passes). Across all logged
  schema-constrained `/deep` calls in the 3 stability runs: 105/105 = 1.000.
- Gateway throughput: mean ≈ **4.5 tok/s**, peak ≈ 7.4 tok/s. Generations are
  short (schema-constrained signal JSON + terse free-form replies), so the
  per-call token counts are small; this is the measured steady state on the
  4 GB-VRAM laptop and is consistent with the 8k-context decision in ADR 0004
  (the 27 tok/s figure there was a warm single-ticker benchmark, not the mixed
  short-call test traffic measured here).
- Full-suite stability: three consecutive `pytest` runs each reported
  **39 passed, 0 failed** — identical pass counts, zero flakiness.

Blockers: none outstanding. TradingAgents is pinned and checked out (no
documented-blocker was needed); the real LangGraph wiring remains a documented
TODO seam in `deep/adapter.py` (config-only, STEERING rule 2) for a later step.

Decision notes added: 0005 (hash-embedding fallback), 0006 (editable-install
import wiring). Runbook now documents the ONE import line, the `pip install -e .`
step, and the fresh-checkout order.

## Week 1 (2026-10-21)

Current step / gate: Step 0 (Foundations) — scaffolding, pre-G0.

Done:
- Verified the real state of all three source repos (see scope.md). Confirmed `c:\GIT\Fintech\agents` is Polymarket Agents, NOT TradingAgents — TradingAgents must be added as a submodule.
- Verified local toolchain: git 2.51, Python 3.12 via `py` launcher (PATH `python` is a broken Store stub), Node 24.15, Docker 29.6, Ollama 0.40.6 (only `llama3.2` ~3B pulled).
- Created the monorepo skeleton at `c:\GIT\Fintech\repo` with the STEERING §4 layout.
- Wrote docs: scope.md, unification-plan.md, status.md, backlog.md, decisions/0001-0003, runbook.md, tests/golden/README.

Blocked / awaiting user decision:
- Monorepo location confirmation (proposed `c:\GIT\Fintech\repo`).
- TradingAgents submodule URL/tag to pin (proposed `TauricResearch/TradingAgents` @ latest release tag).
- Golden-set exact ticker list (proposed in tests/golden/README).
- Whether to `git init` MONO now or fold into an existing repo.

Risks (top 3):
1. 8 GB VRAM is tight for an 8B model at 16k context — deep lane speed may force weekly-only runs (stop/pivot rule). Mitigation: measure in Step 0.5 before committing.
2. Two source repos share an owner (`tandavinci`) and have divergent frontends (Recharts vs Chart.js); merge must be staged to avoid breaking the Tracker (rule 5).
3. yfinance reliability for `.BO`/`.NS` and past-dated fundamentals withheld — backtests must use technical + news inputs only (rule 7).

Next week (top 3):
1. Confirm the four blocked decisions with the user; then `git init` MONO + add submodule (0.2, 0.3).
2. Record the three baselines (0.4) and pull + benchmark an 8B 4-bit model (0.5).
3. Build the golden set and its loader test (0.7); pass Gate G0.

Decision notes added: 0001 (monorepo strategy), 0002 (Windows Docker/Ollama route), 0003 (TradingAgents pinning).
