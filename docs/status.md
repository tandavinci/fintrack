# Status Log

Format per STEERING §11. Newest week on top.

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
