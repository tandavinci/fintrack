# 0003 — TradingAgents pinning

Date: 2026-10-21
Status: accepted — upstream TauricResearch/TradingAgents
Gate: Step 0

## Context

TradingAgents (LangGraph multi-agent trading team) is the `deep/` engine source. It is NOT present in the workspace — `c:\GIT\Fintech\agents` is Polymarket Agents, a different project. Rule 2: never edit TradingAgents source; change behaviour only through config and the `deep/` adapter.

## Decision (proposed)

Add `TauricResearch/TradingAgents` as a git submodule at `third_party/TradingAgents`, pinned to an exact release tag or commit SHA (not a moving branch). Upgrades happen only after the scorecard + golden-set backtest pass (§8 monthly cadence, §10 stop/pivot rule).

## Open items (need user input)

- Confirm the upstream is `https://github.com/TauricResearch/TradingAgents` (the widely used one), or provide the fork/URL you intend to use.
- Choose the pin: latest stable release tag vs a specific SHA. Record the exact SHA here once added.

## Consequences

- The adapter (`deep/analyze`) and config are the only integration surface. Any upstream API change is absorbed in the adapter, behind the Recommendation contract.
- Submodule must be cloned with `--recursive` / `git submodule update --init` on fresh checkouts; note this in runbook.md.
