# 0003 — TradingAgents pinning

Date: 2026-10-21
Status: accepted — upstream TauricResearch/TradingAgents
Gate: Step 0

## Context

TradingAgents (LangGraph multi-agent trading team) is the `deep/` engine source. It is NOT present in the workspace — `c:\GIT\Fintech\agents` is Polymarket Agents, a different project. Rule 2: never edit TradingAgents source; change behaviour only through config and the `deep/` adapter.

## Decision (accepted)

Added `TauricResearch/TradingAgents` as a git submodule at `third_party/TradingAgents`, pinned to an exact release tag resolved to a commit SHA (not a moving branch). Upgrades happen only after the scorecard + golden-set backtest pass (§8 monthly cadence, §10 stop/pivot rule).

### Resolved pin (Step 0 / FEAT-001)

- Upstream: `https://github.com/TauricResearch/TradingAgents`
- Pin: latest stable release tag `v0.7.0`
- Resolved SHA: `e8f5503e9b86d5c2710b0c76b1f686703968c9df`
- Submodule is checked out detached at this SHA; `git describe --tags` reports `v0.7.0`.

To re-pin later, `git -C third_party/TradingAgents fetch --tags`, checkout the new tag, then commit the updated submodule pointer with a `step-0:`-style message.

## Consequences

- The adapter (`deep/analyze`) and config are the only integration surface. Any upstream API change is absorbed in the adapter, behind the Recommendation contract.
- Submodule must be cloned with `--recursive` / `git submodule update --init` on fresh checkouts; note this in runbook.md.
