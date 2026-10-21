# 0004 — Hardware-adapted model strategy

Date: 2026-10-21
Status: accepted
Gate: Step 0 / Step 1

## Context

STEERING §2 assumes a Ryzen 5800X, RTX 4060 Ti with 8 GB VRAM, 48 GB RAM. The actual machine (measured with nvidia-smi / CIM) is:
- GPU: NVIDIA RTX 2050, **4 GB VRAM** (+ Intel iGPU)
- RAM: 31.5 GB
- CPU: Intel Core Ultra 5 135H (laptop)

The user explicitly asked to optimize for and develop/test on the current equipment.

## Decision

1. **Model:** use a 3B-class 4-bit model for BOTH quick and deep tiers. `llama3.2:latest` (3B) is already pulled and fits. Do NOT pull an 8B model — ~5 GB 4-bit exceeds 4 GB VRAM and runs mostly on CPU.
2. **Context cap:** always set `num_ctx=8192`. Measured: at Ollama's default 131072 ctx the model spilled to RAM and ran at 9.4 tok/s (10% GPU); at 8192 it ran at 27 tok/s (67% GPU, ~2.3 GB VRAM).
3. **Gateway enforces the cap:** the gateway sets `num_ctx=8192` on every Ollama call so no caller can accidentally trigger the slow spilled path.
4. **Deep lane cadence:** start with deep analysis on holdings only, overnight, 1 debate round, 2 analysts (`market,news`). Given 27 tok/s, re-measure one full ticker in Step 4.4 and set the nightly count from that; fall back to weekly if too slow (STEERING §10 stop/pivot).
5. **16k context goal (G1) is relaxed on this hardware:** target 8k. Record this as a known deviation; revisit only if hardware changes.

## Consequences

- The §9 "16k context" qualification item is measured at 8k on this machine.
- `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_CONTEXT_LENGTH` guidance documented in runbook.
- If the user later moves to an 8 GB+ GPU, revisit with a new decision note and re-run the scorecard.
