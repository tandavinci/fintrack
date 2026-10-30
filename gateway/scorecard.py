"""Model qualification scorecard (G6 deliverable 3).

Compares two (or more) local Ollama models on three dimensions and writes a
comparison scorecard (markdown + JSON) so a human can judge whether a bigger,
slower model earns its cost on this 4 GB-VRAM machine:

  1. structured-output validity  — share of schema-constrained generations the
     model returns as valid JSON (mirrors ``tests/test_gateway_validity_rate``);
  2. number-preservation         — the "models explain, code computes"
     discipline, measured: given a tiny set of allowed figures, does the model
     report ONLY numbers from that set (any out-of-set number is a FAILURE);
  3. latency / throughput        — mean & median latency and mean tokens/sec.

STEERING rule 1 (the gateway is the ONLY path to Ollama): every model call in
this module goes through :func:`gateway.client.generate_sync`. We never call
Ollama's :11434 /api/generate ourselves. The ONE place httpx touches Ollama
here is the ``/api/tags`` availability/presence probe — reading tags is not a
model call.

The gateway always generates with ``settings.GATEWAY_MODEL`` (read at call time
inside ``_ollama_generate``). To run the SAME prompts against a DIFFERENT model
we TEMPORARILY set ``settings.GATEWAY_MODEL`` around a batch of calls and
restore it in a ``finally`` so a run never leaks the setting.

This module is PURE at import: importing it makes NO model call and NO network
call. The live runners and the CLI are the only things that touch Ollama, and
only when explicitly invoked.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from gateway import client
from gateway.settings import settings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODELS = ["llama3.2", "gemma2:9b"]
DEFAULT_VALIDITY_N = 20
DEFAULT_OUT_DIR = "docs/evaluations"

# The validity bar (Gate G1 / G4): a model qualifies only at >= 0.95 valid.
VALIDITY_THRESHOLD = 0.95

# Number-check tolerances — mirror deep/adapter.py's _is_allowed idea:
# allowed iff abs(x - a) <= max(abs_tol, rel_tol * abs(a)).
NUMBER_ABS_TOL = 0.01
NUMBER_REL_TOL = 0.001

# Models that spill out of the 4 GB VRAM budget into system RAM (slower). Used
# only to annotate the scorecard; it does not change scoring.
_RAM_SPILL_MODELS = {"gemma2:9b"}

# Strict signal schema (mirrors tests/test_gateway_validity_rate._SIGNAL_SCHEMA).
# Deliberately strict; NEVER loosened to make a model pass.
SIGNAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["signal", "confidence"],
    "properties": {
        "signal": {"type": "string", "enum": ["BUY", "HOLD", "SELL"]},
        "confidence": {"type": "number"},
    },
    "additionalProperties": False,
}

# Focused classifier prompt for the validity dimension (mirrors the reference).
SIGNAL_PROMPT = (
    "You are a trading signal classifier. Return ONLY a JSON object with "
    "exactly two keys: 'signal' (one of BUY, HOLD, SELL) and 'confidence' "
    "(a number between 0 and 1). No prose, no extra keys."
)

# Tiny schema for the number-preservation dimension: a single numeric 'price'.
_PRICE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["price"],
    "properties": {"price": {"type": "number"}},
    "additionalProperties": False,
}

# ~5 deterministic number-preservation cases. Each gives the model a SMALL set
# of allowed figures and asks it to echo ONE of them back as {"price": <n>}.
# A reply whose number is not in ``allowed`` (within tolerance) is a FAILURE.
NUMBER_CASES: list[dict[str, Any]] = [
    {"name": "close_single", "allowed": [101.5], "target": "the latest closing price"},
    {"name": "close_of_three", "allowed": [101.5, 98.2, 1234567.0], "target": "the latest closing price"},
    {"name": "high_of_two", "allowed": [250.75, 244.1], "target": "the session high"},
    {"name": "low_integer", "allowed": [1200.0, 1185.0], "target": "the session low"},
    {"name": "large_cap_price", "allowed": [3456.8, 3401.25], "target": "the last traded price"},
]


# ---------------------------------------------------------------------------
# Pure helpers (offline-unit-testable; NO I/O, NO model calls)
# ---------------------------------------------------------------------------

def number_in_allowed(
    reported: float,
    allowed: list[float],
    *,
    abs_tol: float = NUMBER_ABS_TOL,
    rel_tol: float = NUMBER_REL_TOL,
) -> bool:
    """True iff ``reported`` is within tolerance of ANY allowed value.

    Mirrors deep/adapter.py's ``_is_allowed``:
    ``abs(reported - a) <= max(abs_tol, rel_tol * abs(a))``.
    """
    for a in allowed:
        if abs(reported - a) <= max(abs_tol, rel_tol * abs(a)):
            return True
    return False


# First numeric figure: optional sign, digits with optional thousands commas,
# optional decimal. Leading $ and trailing % are stripped separately.
_LEADING_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def parse_leading_number(text: str) -> float | None:
    """Extract the first numeric figure from ``text`` as a float, else None.

    Strips ``$``, ``%``, ``+`` and thousands commas. Deterministic and simple:
    it does NOT try to understand the sentence, only to find the first number.
    ``"$1,234.50"`` -> 1234.5, ``"42%"`` -> 42.0, ``"no number"`` -> None.
    """
    if not text:
        return None
    cleaned = text.replace("$", "").replace("%", "")
    match = _LEADING_NUMBER_RE.search(cleaned)
    if not match:
        return None
    token = match.group(0).replace(",", "").lstrip("+")
    try:
        return float(token)
    except ValueError:
        return None


def summarize_latencies(
    latency_ms: list[int],
    token_counts: list[int],
) -> dict[str, float]:
    """Pure latency/throughput summary. Deterministic given inputs.

    Returns ``latency_ms_mean``, ``latency_ms_median`` (over all calls) and
    ``tokens_per_sec_mean`` (mean over calls with positive latency of
    ``eval_count / (latency_ms / 1000)``). Empty inputs -> safe zeros.
    """
    if not latency_ms:
        return {
            "latency_ms_mean": 0.0,
            "latency_ms_median": 0.0,
            "tokens_per_sec_mean": 0.0,
        }

    mean_latency = statistics.fmean(latency_ms)
    median_latency = statistics.median(latency_ms)

    tps_values: list[float] = []
    for lat, toks in zip(latency_ms, token_counts):
        if lat > 0 and toks > 0:
            tps_values.append(toks / (lat / 1000.0))
    tps_mean = statistics.fmean(tps_values) if tps_values else 0.0

    return {
        "latency_ms_mean": mean_latency,
        "latency_ms_median": float(median_latency),
        "tokens_per_sec_mean": tps_mean,
    }


@dataclass
class ModelScore:
    """One model's measured scores across the three dimensions."""

    model: str
    validity_total: int = 0
    validity_valid: int = 0
    validity_rate: float = 0.0
    number_total: int = 0
    number_preserved: int = 0
    number_preserved_rate: float = 0.0
    latency_count: int = 0
    latency_ms_mean: float = 0.0
    latency_ms_median: float = 0.0
    tokens_per_sec_mean: float = 0.0
    error_count: int = 0
    # Set when a model could not be scored (e.g. its Ollama tag is absent).
    skipped: bool = False
    note: str | None = None


@dataclass
class Scorecard:
    """A set of ModelScores plus the chosen recommendation."""

    scores: list[ModelScore] = field(default_factory=list)
    recommended_model: str | None = None
    recommendation_reason: str = ""


def choose_recommended(scores: list[ModelScore]) -> tuple[str, str]:
    """Pure recommendation policy.

    Policy (explicit, so review can judge it):
      1. Only models that were actually scored (not skipped) are candidates.
      2. A model "passes" iff its validity_rate >= VALIDITY_THRESHOLD (0.95).
      3. Prefer a passing model. Among passing models, require number-
         preservation >= the best other passing model's; i.e. we will not pick
         a model that preserves numbers worse than an alternative that also
         passes validity.
      4. Ties (both pass, equal number-preservation) break toward the FASTER
         model (lower median latency), which on this 4 GB-VRAM machine is also
         the smaller model that fits in VRAM — so a tie favours llama3.2.
      5. If NO model passes the validity bar, recommend the one with the
         highest validity_rate (least-bad), and say so plainly.

    Returns ``(model, human-readable reason)``.
    """
    candidates = [s for s in scores if not s.skipped]
    if not candidates:
        return ("", "No model could be scored (none available).")

    passing = [s for s in candidates if s.validity_rate >= VALIDITY_THRESHOLD]

    if not passing:
        best = max(candidates, key=lambda s: s.validity_rate)
        return (
            best.model,
            f"No model met the {VALIDITY_THRESHOLD:.0%} validity bar; "
            f"{best.model} had the highest validity rate "
            f"({best.validity_rate:.1%}). Treat this as a warning, not a pass.",
        )

    # Among passing models, the best number-preservation rate on offer.
    best_number = max(s.number_preserved_rate for s in passing)
    # Keep passing models that are at least as good as the best on numbers.
    qualified = [s for s in passing if s.number_preserved_rate >= best_number]

    # Tie-break toward the faster (lower median latency) model; that is the
    # smaller model that fits the 4 GB VRAM budget.
    winner = min(qualified, key=lambda s: s.latency_ms_median)

    others = [s for s in passing if s.model != winner.model]
    if not others:
        reason = (
            f"{winner.model} passed the {VALIDITY_THRESHOLD:.0%} validity bar "
            f"(validity {winner.validity_rate:.1%}, number-preservation "
            f"{winner.number_preserved_rate:.1%}) and was the only qualifying model."
        )
    else:
        faster_note = (
            "fastest among them" if len(qualified) > 1 else "the leading choice"
        )
        reason = (
            f"{winner.model} passed the validity bar (validity "
            f"{winner.validity_rate:.1%}, number-preservation "
            f"{winner.number_preserved_rate:.1%}), matched or beat the "
            f"alternatives on number-preservation, and is {faster_note} "
            f"(median latency {winner.latency_ms_median:.0f} ms), which also "
            f"fits the 4 GB VRAM budget."
        )
    return (winner.model, reason)


# ---------------------------------------------------------------------------
# Availability probe (the ONLY httpx-to-Ollama call here; not a model call)
# ---------------------------------------------------------------------------

def _ollama_up() -> bool:
    """True iff Ollama answers GET /api/tags with 200 (mirrors the reference)."""
    try:
        resp = httpx.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=5.0)
        return resp.status_code == 200
    except httpx.RequestError:
        return False


def _ollama_tags() -> list[str]:
    """The model names Ollama reports present, else an empty list."""
    try:
        resp = httpx.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=5.0)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except (httpx.RequestError, ValueError):
        return []
    return [m.get("name", "") for m in (data.get("models") or [])]


def model_available(model: str) -> bool:
    """True iff ``model`` is present in Ollama's /api/tags list.

    Ollama tags are often suffixed with ``:latest``; a bare ``llama3.2`` is
    treated as present when ``llama3.2`` or ``llama3.2:latest`` is listed.
    """
    tags = _ollama_tags()
    if model in tags:
        return True
    if ":" not in model and f"{model}:latest" in tags:
        return True
    return False


# ---------------------------------------------------------------------------
# Live runners (need Ollama; exercised by the CLI / tiny integration test)
# ---------------------------------------------------------------------------

def _with_model(model: str):
    """Context-manager-free helper is avoided; callers use try/finally. This
    returns the previous model so a caller can restore it."""
    previous = settings.GATEWAY_MODEL
    settings.GATEWAY_MODEL = model
    return previous


def run_validity(
    model: str, n: int
) -> tuple[int, int, list[int], list[int], int]:
    """Run ``n`` schema-constrained generations against ``model``.

    Temporarily sets ``settings.GATEWAY_MODEL = model`` (restored in finally),
    issues ``n`` ``generate_sync(route="deep", json_schema=SIGNAL_SCHEMA)``
    calls, and tallies validity, latency and completion-token counts.

    Returns ``(valid, total, latencies_ms, token_counts, errors)``.
    """
    valid = 0
    latencies: list[int] = []
    token_counts: list[int] = []
    errors = 0

    previous = _with_model(model)
    try:
        for _ in range(n):
            resp = client.generate_sync(
                SIGNAL_PROMPT,
                route="deep",
                json_schema=SIGNAL_SCHEMA,
                temperature=0.1,
            )
            if resp.get("valid"):
                valid += 1
            if resp.get("error"):
                errors += 1
            latencies.append(int(resp.get("latency_ms") or 0))
            token_counts.append(int(resp.get("eval_count") or 0))
    finally:
        settings.GATEWAY_MODEL = previous

    return valid, n, latencies, token_counts, errors


def _number_prompt(case: dict[str, Any]) -> str:
    """Build a number-preservation prompt that hands the model a SMALL set of
    allowed figures and asks it to report ONE of them as {"price": <number>}.

    The whole point is the "models explain, code computes" discipline: the
    model must NOT invent a figure, only echo one of the supplied numbers. Any
    other number is a preservation failure.
    """
    allowed_str = ", ".join(_format_number(a) for a in case["allowed"])
    return (
        "You are a careful reporting tool. The ONLY numbers you may use are: "
        f"{allowed_str}. Do not invent, round, or compute any other number. "
        f"Report {case['target']} as a JSON object with exactly one key "
        '"price" whose value is one of those numbers. No prose, no extra keys.'
    )


def run_number_preservation(
    model: str, cases: list[dict[str, Any]]
) -> tuple[int, int]:
    """Measure number-preservation for ``model`` over ``cases``.

    For each case: build a prompt giving the model a small allowed set, call
    ``generate_sync`` constrained to the tiny price schema, parse the returned
    number, and check it is in the allowed set (within tolerance). A reply with
    a number NOT in the allowed set — or no parseable number — is a FAILURE.

    Returns ``(preserved, total)``.
    """
    preserved = 0
    total = len(cases)

    previous = _with_model(model)
    try:
        for case in cases:
            resp = client.generate_sync(
                _number_prompt(case),
                route="deep",
                json_schema=_PRICE_SCHEMA,
                temperature=0.1,
            )
            reported = _extract_price(resp.get("text", ""))
            if reported is not None and number_in_allowed(reported, case["allowed"]):
                preserved += 1
    finally:
        settings.GATEWAY_MODEL = previous

    return preserved, total


def _extract_price(text: str) -> float | None:
    """Pull the numeric 'price' from a model reply.

    Prefers the JSON ``price`` field (the schema the call was constrained to);
    falls back to the first number in the text if JSON parsing fails.
    """
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "price" in obj:
            value = obj["price"]
            if isinstance(value, bool):
                return None
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                return parse_leading_number(value)
    except (ValueError, TypeError):
        pass
    return parse_leading_number(text)


def score_model(model: str, validity_n: int) -> ModelScore:
    """Compose the two runners + latency summary into one ModelScore."""
    valid, v_total, latencies, token_counts, errors = run_validity(model, validity_n)
    preserved, n_total = run_number_preservation(model, NUMBER_CASES)
    lat = summarize_latencies(latencies, token_counts)

    return ModelScore(
        model=model,
        validity_total=v_total,
        validity_valid=valid,
        validity_rate=(valid / v_total) if v_total else 0.0,
        number_total=n_total,
        number_preserved=preserved,
        number_preserved_rate=(preserved / n_total) if n_total else 0.0,
        latency_count=len(latencies),
        latency_ms_mean=lat["latency_ms_mean"],
        latency_ms_median=lat["latency_ms_median"],
        tokens_per_sec_mean=lat["tokens_per_sec_mean"],
        error_count=errors,
        skipped=False,
        note=("spills into RAM on 4 GB VRAM" if model in _RAM_SPILL_MODELS else None),
    )


def build_scorecard(models: list[str], validity_n: int) -> Scorecard:
    """Score each AVAILABLE model, choose the recommended one, return a Scorecard.

    A model whose Ollama tag is absent is recorded as a skipped ModelScore with
    a clear note rather than being silently dropped.
    """
    scores: list[ModelScore] = []
    for model in models:
        if not model_available(model):
            scores.append(
                ModelScore(
                    model=model,
                    skipped=True,
                    note="not pulled in Ollama (absent from /api/tags) — skipped",
                )
            )
            continue
        scores.append(score_model(model, validity_n))

    recommended, reason = choose_recommended(scores)
    return Scorecard(
        scores=scores,
        recommended_model=recommended or None,
        recommendation_reason=reason,
    )


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def _format_number(value: float) -> str:
    """Render a number without a trailing ``.0`` for integers, else as-is."""
    if value == int(value):
        return str(int(value))
    return repr(value)


def _round(value: float, digits: int) -> float:
    """Deterministic rounding for JSON/markdown output."""
    return round(float(value), digits)


def _scorecard_to_dict(scorecard: Scorecard) -> dict[str, Any]:
    """Scorecard -> plain dict with deterministically rounded floats."""
    scores_out: list[dict[str, Any]] = []
    for s in scorecard.scores:
        d = asdict(s)
        d["validity_rate"] = _round(s.validity_rate, 4)
        d["number_preserved_rate"] = _round(s.number_preserved_rate, 4)
        d["latency_ms_mean"] = _round(s.latency_ms_mean, 1)
        d["latency_ms_median"] = _round(s.latency_ms_median, 1)
        d["tokens_per_sec_mean"] = _round(s.tokens_per_sec_mean, 2)
        scores_out.append(d)
    return {
        "scores": scores_out,
        "recommended_model": scorecard.recommended_model,
        "recommendation_reason": scorecard.recommendation_reason,
    }


def _markdown_table(scorecard: Scorecard) -> str:
    """Render the human-readable comparison table + recommendation."""
    header = (
        "| Model | Validity rate | Number-preservation | "
        "Mean latency (ms) | Median latency (ms) | Tokens/sec | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
    )
    rows: list[str] = []
    for s in scorecard.scores:
        if s.skipped:
            rows.append(
                f"| `{s.model}` | — | — | — | — | — | {s.note or 'skipped'} |"
            )
            continue
        note = s.note or ""
        rows.append(
            f"| `{s.model}` "
            f"| {s.validity_rate:.1%} ({s.validity_valid}/{s.validity_total}) "
            f"| {s.number_preserved_rate:.1%} ({s.number_preserved}/{s.number_total}) "
            f"| {s.latency_ms_mean:.0f} "
            f"| {s.latency_ms_median:.0f} "
            f"| {s.tokens_per_sec_mean:.2f} "
            f"| {note} |"
        )
    return header + "\n".join(rows) + "\n"


def _markdown_doc(scorecard: Scorecard) -> str:
    """Full markdown document for ``model_scorecard.md``."""
    rec = scorecard.recommended_model or "(none)"
    lines = [
        "# Model qualification scorecard (G6 deliverable 3)",
        "",
        "Compares local Ollama models on structured-output validity, "
        "number-preservation (the \"models explain, code computes\" discipline), "
        "and latency / throughput. Every model call was routed through the "
        "gateway (`gateway.client.generate_sync`) — the only path to Ollama.",
        "",
        "## Comparison",
        "",
        _markdown_table(scorecard),
        "## Recommendation",
        "",
        f"**Recommended model: `{rec}`**",
        "",
        scorecard.recommendation_reason,
        "",
        "## Hardware note (4 GB VRAM budget)",
        "",
        "This machine has an RTX 2050 with **4 GB VRAM**. `llama3.2` (~2 GB, "
        "3B) fits in VRAM. `gemma2:9b` (~5.4 GB) does NOT fit and **spills into "
        "system RAM**, which makes it materially slower per token. The bigger "
        "model only earns its cost if it clears the validity bar AND preserves "
        "numbers at least as well as the smaller model; the latency columns "
        "above quantify what that cost is.",
        "",
        "## Method",
        "",
        f"- Validity: {DEFAULT_VALIDITY_N} schema-constrained generations "
        "against a strict BUY/HOLD/SELL + confidence schema; validity rate = "
        f"valid / N; bar = {VALIDITY_THRESHOLD:.0%}.",
        "- Number-preservation: each case hands the model a small set of allowed "
        "figures and asks it to report one of them; any number not in the "
        "allowed set (within tolerance) is a FAILURE. The schema is never "
        "loosened to make a model pass.",
        "- Latency / throughput: mean & median latency and mean tokens/sec "
        "(completion tokens / elapsed seconds) over the validity generations.",
        "",
    ]
    return "\n".join(lines)


def write_scorecard(scorecard: Scorecard, out_dir: str | Path) -> dict[str, str]:
    """Write ``model_scorecard.md`` and ``model_scorecard.json`` to ``out_dir``.

    Returns the two written paths keyed ``"markdown"`` and ``"json"``.
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    md_path = out_path / "model_scorecard.md"
    json_path = out_path / "model_scorecard.json"

    md_path.write_text(_markdown_doc(scorecard), encoding="utf-8")
    json_path.write_text(
        json.dumps(_scorecard_to_dict(scorecard), indent=2) + "\n",
        encoding="utf-8",
    )
    return {"markdown": str(md_path), "json": str(json_path)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _stdout_table(scorecard: Scorecard) -> str:
    """A concise plain-text comparison for stdout."""
    lines = ["Model qualification scorecard", "=" * 60]
    for s in scorecard.scores:
        if s.skipped:
            lines.append(f"{s.model:<14} SKIPPED — {s.note}")
            continue
        lines.append(
            f"{s.model:<14} "
            f"validity={s.validity_rate:.1%} ({s.validity_valid}/{s.validity_total})  "
            f"numbers={s.number_preserved_rate:.1%} ({s.number_preserved}/{s.number_total})  "
            f"median={s.latency_ms_median:.0f}ms  "
            f"tps={s.tokens_per_sec_mean:.1f}"
            + (f"  [{s.note}]" if s.note else "")
        )
    lines.append("-" * 60)
    lines.append(f"Recommended: {scorecard.recommended_model or '(none)'}")
    lines.append(scorecard.recommendation_reason)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m gateway.scorecard",
        description=(
            "Compare local Ollama models on structured-output validity, "
            "number-preservation, and latency; write a comparison scorecard "
            "(markdown + JSON). Every model call is routed through the gateway."
        ),
    )
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="Comma-separated Ollama model names (default: %(default)s).",
    )
    parser.add_argument(
        "--validity-n",
        type=int,
        default=DEFAULT_VALIDITY_N,
        help="Number of schema-constrained generations per model (default: %(default)s).",
    )
    parser.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help="Directory for the written scorecard files (default: %(default)s).",
    )
    args = parser.parse_args(argv)

    if not _ollama_up():
        print(
            f"ERROR: Ollama is not reachable at {settings.OLLAMA_BASE_URL}. "
            "The scorecard needs a live model server; not pretending to score.",
            file=sys.stderr,
        )
        return 2

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        print("ERROR: no models given.", file=sys.stderr)
        return 2

    scorecard = build_scorecard(models, args.validity_n)
    paths = write_scorecard(scorecard, args.out_dir)

    print(_stdout_table(scorecard))
    print()
    print(f"Wrote {paths['markdown']}")
    print(f"Wrote {paths['json']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
