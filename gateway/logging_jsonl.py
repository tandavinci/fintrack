"""Append-only per-call JSONL logger (design 2.7).

Every model call appends exactly one JSON object (one line) to
``gateway/logs/calls.jsonl``. The log directory is created if missing. The
prompt itself is never logged — only its sha256 — to keep lines small and
avoid persisting content.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

# Serializes the short append-open across threads (the Ollama call itself is
# already serialized by the priority gate, but the logger may also be hit from
# the input-validation error paths in app.py).
_log_lock = threading.Lock()


def prompt_sha256(prompt: str) -> str:
    """sha256 hex digest of the prompt (never the raw prompt)."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def log_call(
    log_path: str,
    *,
    route: str,
    model_id: str | None,
    prompt_sha256: str,
    num_ctx: int | None,
    eval_count: int | None,
    prompt_eval_count: int | None,
    latency_ms: int | None,
    attempts: int | None,
    valid: bool | None,
    error: str | None,
) -> None:
    """Append one call record to ``log_path`` (design 2.7 field set)."""
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "route": route,
        "model_id": model_id,
        "prompt_sha256": prompt_sha256,
        "num_ctx": num_ctx,
        "eval_count": eval_count,
        "prompt_eval_count": prompt_eval_count,
        "latency_ms": latency_ms,
        "attempts": attempts,
        "valid": valid,
        "error": error,
    }
    line = json.dumps(record, separators=(",", ":"))
    with _log_lock:
        directory = os.path.dirname(log_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
