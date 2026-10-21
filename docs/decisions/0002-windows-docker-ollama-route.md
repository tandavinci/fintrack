# 0002 — Windows Docker + Ollama route

Date: 2026-10-21
Status: proposed (confirm at Step 1)
Gate: Step 0 / Step 1

## Context

OS is Windows. GPU is an RTX 4060 Ti (8 GB VRAM). Ollama 0.40.6 is already installed and running natively on the host. STEERING §2 offers two routes: WSL2 for Docker, or run Ollama natively and call it from containers at `host.docker.internal:11434`.

## Decision

Run Ollama natively on the Windows host; containers reach it at `host.docker.internal:11434`. The gateway is the only component that calls Ollama (rule 1).

Rationale: GPU passthrough into Docker Desktop on Windows is fragile; the host already has a working Ollama with GPU access. Keeping Ollama on the host sidesteps container GPU config entirely.

## Consequences

- `infra/docker-compose.yml` must inject `OLLAMA_BASE_URL=http://host.docker.internal:11434` into the gateway service (and add `extra_hosts: ["host.docker.internal:host-gateway"]` for Linux parity if ever run there).
- Set `OLLAMA_MAX_LOADED_MODELS=1` and `OLLAMA_NUM_PARALLEL=1` in the host Ollama environment (one model, one request at a time — §2).
- Revisit if moving the whole stack to WSL2 later; record a new decision if so.
