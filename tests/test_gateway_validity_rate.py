"""Structured-output validity-rate gate (design 7.5, Gate G1).

Ollama is reachable here, so this RUNS; it skips with a clear message only if
Ollama is down. It issues ``SLICE_VALIDITY_N`` (default 20) schema-constrained
generations, computes ``valid_count / N`` and asserts the rate is >= 0.95
(Gate G1). The measured rate is printed to stdout for the status write-up.

If the rate falls below 0.95 the correct response is to tighten the
prompt/retry — NEVER to loosen the schema (STEERING: models explain, code
computes).
"""

from __future__ import annotations

import os

import httpx
import pytest

from gateway import client
from gateway.settings import settings

SLICE_VALIDITY_N = int(os.environ.get("SLICE_VALIDITY_N", "20"))
VALIDITY_THRESHOLD = 0.95

# The schema the generations are constrained to — deliberately strict; it is
# never loosened to make the gate pass.
_SIGNAL_SCHEMA = {
    "type": "object",
    "required": ["signal", "confidence"],
    "properties": {
        "signal": {"type": "string", "enum": ["BUY", "HOLD", "SELL"]},
        "confidence": {"type": "number"},
    },
    "additionalProperties": False,
}

# A focused prompt + the gateway's one retry drive the rate above the gate.
_PROMPT = (
    "You are a trading signal classifier. Return ONLY a JSON object with "
    "exactly two keys: 'signal' (one of BUY, HOLD, SELL) and 'confidence' "
    "(a number between 0 and 1). No prose, no extra keys."
)


def _ollama_up() -> bool:
    try:
        resp = httpx.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=5.0)
        return resp.status_code == 200
    except httpx.RequestError:
        return False


@pytest.mark.skipif(
    not _ollama_up(),
    reason="Ollama not reachable at the configured OLLAMA_BASE_URL",
)
def test_structured_output_validity_rate(capsys):
    valid_count = 0
    for _ in range(SLICE_VALIDITY_N):
        resp = client.generate_sync(
            _PROMPT,
            route="deep",
            json_schema=_SIGNAL_SCHEMA,
            temperature=0.1,
        )
        if resp["valid"]:
            valid_count += 1

    rate = valid_count / SLICE_VALIDITY_N
    # Recorded to stdout for the status.md write-up (captured by pytest -s).
    with capsys.disabled():
        print(
            f"\n[validity-rate] valid={valid_count}/{SLICE_VALIDITY_N} "
            f"rate={rate:.3f} threshold={VALIDITY_THRESHOLD}"
        )

    assert rate >= VALIDITY_THRESHOLD, (
        f"structured-output validity rate {rate:.3f} < {VALIDITY_THRESHOLD} "
        f"(valid {valid_count}/{SLICE_VALIDITY_N}); tighten the prompt/retry, "
        f"never loosen the schema"
    )
