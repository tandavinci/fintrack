# 0005 — Hash-embedding fallback in the gateway

Date: 2026-10-21
Status: accepted
Gate: Step 1 / FEAT-001

## Context

The gateway exposes an `embed()` surface (design 2.6) that the retrieval/RAG
wiring will eventually call. On this hardware only `llama3.2` is pulled, and a
dedicated embedding model is not guaranteed to be present or reachable. A
missing or unreachable embedding model must not crash the offline pipeline or
the test suite, which run without any network guarantee.

## Decision

`gateway.client.embed()` tries Ollama's `POST /api/embed` first
(`method="ollama"`, native dimension). On any failure it falls back to a
deterministic feature-hashing placeholder (`method="hash_placeholder"`):

- fixed dimension `HASH_EMBED_DIM = 256`,
- each token is hashed (sha256) into a bucket with a sign bit, then the vector
  is L2-normalized,
- identical input always yields the identical vector (determinism), which is
  what the offline tests assert.

The response always carries both `dim` and `method`; callers MUST branch on
`method` and MUST NOT assume one dimension across the two methods.

This is a STOPGAP for offline wiring and local tests, **not** a semantic
embedding. A later step replaces it with a real embedding model / pgvector
population.

## Consequences

- The slice is testable and runnable with zero embedding infrastructure.
- Any similarity/ranking built on the placeholder is positional, not semantic;
  downstream consumers must treat `hash_placeholder` vectors as non-semantic
  until the real model lands.
- Swapping in a real embedding model is a drop-in change behind the same
  `embed()` contract (the `ollama` branch), with no caller changes required
  beyond honoring `method`/`dim`.
