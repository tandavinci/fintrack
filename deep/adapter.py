"""The deep lane adapter (design 4, unification-plan Step 4.1 / 4.6).

``analyze(ticker, as_of, portfolio=None) -> Recommendation`` is a **plain
synchronous callable** invoked by a Celery ``deep`` worker or an offline script
(design 4.3). It is **never** called from a web request handler (STEERING
rule 5). To keep that constraint structurally enforced this module imports no
web-framework / ``Request`` symbol, so it is impossible to mount as a route.
(The adapter source is deliberately free of the web framework's name so a
structural grep can assert the boundary; see tests 7.3.)

Flow (design 4.2):
  1-2. Build a data snapshot via the quant ``DataPipeline`` (same enriched df
       and ``compute_snapshot_id`` as ``quant/recommend.py`` so quant and deep
       reference one snapshot), then build a prompt from the indicator summary,
       recent bars, regime, and optional portfolio context.
  3-4. Call ``gateway.client.generate_sync`` on the ``/deep`` route passing the
       JSON schema derived from :class:`Recommendation` (the gateway enforces
       ``num_ctx=8192`` + one retry), then construct ``Recommendation(**obj)``.
       A gateway ``valid=false`` or a construction error raises
       :class:`DeepValidationError`.
  5.   The number-check (STEERING rule 3): every numeric figure the model wrote
       into ``rationale`` / ``evidence`` must be present in the snapshot within
       tolerance, sign-preserving. An unmatched figure raises
       :class:`NumberCheckError`.
  6.   Return a ``Recommendation`` with ``source="deep"``.

The number-check invariant is owned here: deep is the lane where a model writes
free-form numeric claims, so no unchecked model number escapes into a
``Recommendation`` (design 4.4).
"""

from __future__ import annotations

import json
import re
from datetime import date
from numbers import Real
from typing import Any

from backend.app.schemas.recommendation import Recommendation
from backend.app.schemas.snapshot import SNAPSHOT_TAIL_N, compute_snapshot_id
from gateway.client import generate_sync
from quant.agents.regime_agent import RegimeAgent
from quant.data.indicators import TechnicalIndicators
from quant.data.pipeline import DataPipeline

# Prompt/graph attribution for stored recommendations (design 4.2 / 4.3). Bump
# to "deep-v2" when the real TradingAgents graph is wired (see the TODO seam).
DEEP_PROMPT_VERSION = "deep-v1"

# Number-check tolerances (design 4.2c): within 1% relative OR 0.01 absolute.
NUMBER_CHECK_REL_TOL = 0.01
NUMBER_CHECK_ABS_TOL = 0.01

# Signed figure extractor (design 4.2b): optional sign, digits with optional
# thousands commas, optional decimal, optional trailing % or x. Sign preserved.
_FIGURE_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?\s*%?x?", re.IGNORECASE)


class DeepValidationError(Exception):
    """Raised when the gateway reports ``valid=false`` or the returned JSON
    cannot be constructed into a :class:`Recommendation` (design 4.4)."""


class NumberCheckError(Exception):
    """Raised when the model text cites a numeric figure absent from the data
    snapshot within tolerance (STEERING rule 3, design 4.2).

    ``offending`` lists every signed figure that matched no allowable value.
    """

    def __init__(self, offending: list[float]) -> None:
        self.offending = offending
        super().__init__(f"number_check_failed: {offending}")


def _validate_portfolio(portfolio: dict | None) -> dict[str, float]:
    """Coerce/reject a portfolio mapping (design 4.4).

    ``portfolio`` must be a mapping of ticker -> numeric holding. Non-numeric
    values (and bools, which are ints in Python but not valid holdings) are
    rejected so no bad figure leaks into the allowable-numbers set.
    """
    if portfolio is None:
        return {}
    if not isinstance(portfolio, dict):
        raise ValueError("portfolio must be a mapping of ticker -> numeric holding")
    validated: dict[str, float] = {}
    for key, value in portfolio.items():
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"portfolio holding for {key!r} is not numeric: {value!r}")
        validated[str(key)] = float(value)
    return validated


def _recommendation_json_schema() -> dict:
    """The JSON schema the gateway constrains generation to (design 4.2 step 3)."""
    return Recommendation.model_json_schema()


def _build_prompt(
    ticker: str,
    as_of: date,
    summary: dict,
    recent_bars: list[dict],
    regime,
    portfolio: dict[str, float],
) -> str:
    """Build the deep prompt from the snapshot (design 4.2 step 2).

    The model is instructed to return Recommendation-shaped JSON and to cite
    ONLY figures present in the snapshot (the number-check enforces this).
    """
    portfolio_block = (
        json.dumps(portfolio, sort_keys=True) if portfolio else "none"
    )
    return (
        "You are a disciplined equity analyst. Using ONLY the data snapshot "
        "below, produce a trading recommendation.\n"
        "Rules: cite ONLY numbers that appear in the snapshot; do not invent, "
        "round differently, or compute new figures. Explain in words; the "
        "numbers come from the snapshot.\n\n"
        f"TICKER: {ticker}\n"
        f"AS_OF: {as_of.isoformat()}\n\n"
        f"INDICATOR SUMMARY:\n{json.dumps(summary, sort_keys=True)}\n\n"
        f"RECENT BARS (oldest -> newest):\n{json.dumps(recent_bars)}\n\n"
        f"MARKET REGIME: {regime.regime.value} "
        f"(confidence {regime.confidence})\n"
        f"REGIME SIGNALS: {json.dumps(regime.indicators_used, sort_keys=True)}\n\n"
        f"PORTFOLIO CONTEXT: {portfolio_block}\n\n"
        "Return a JSON object matching the Recommendation schema. Put your "
        "reasoning in 'rationale' and snapshot references in 'evidence'."
    )


def _recent_bars(df) -> list[dict]:
    """The last ``SNAPSHOT_TAIL_N`` raw OHLCV bars as plain dicts (prompt use)."""
    cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    tail = df.tail(SNAPSHOT_TAIL_N)
    bars = []
    for _, row in tail.iterrows():
        bars.append({c: float(row[c]) for c in cols})
    return bars


def _collect_numeric_leaves(node: Any, out: list[float]) -> None:
    """Recursively collect every numeric leaf (int/float, not bool) as float."""
    if isinstance(node, bool):
        return
    if isinstance(node, Real):
        out.append(float(node))
    elif isinstance(node, dict):
        for value in node.values():
            _collect_numeric_leaves(value, out)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _collect_numeric_leaves(value, out)


def _build_allowable_set(
    df,
    summary: dict,
    regime,
    portfolio: dict[str, float],
) -> list[float]:
    """Build the allowable-numbers set (design 4.2a).

    - every numeric leaf of ``summarize(df)``,
    - the last 60 raw OHLCV values (Volume cast to float),
    - ``regime.confidence``,
    - every numeric leaf of ``regime.indicators_used``,
    - the validated portfolio numerics.
    """
    allowable: list[float] = []

    _collect_numeric_leaves(summary, allowable)

    cols = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    tail = df.tail(SNAPSHOT_TAIL_N)
    for _, row in tail.iterrows():
        for col in cols:
            allowable.append(float(row[col]))

    allowable.append(float(regime.confidence))
    _collect_numeric_leaves(regime.indicators_used, allowable)

    allowable.extend(portfolio.values())

    # De-duplicate while keeping floats; order is irrelevant for membership.
    return list({v for v in allowable})


def _parse_figure(token: str) -> float | None:
    """Parse one regex match into a signed float (design 4.2b).

    Drops a leading ``+``, keeps a leading ``-``, strips thousands commas and a
    trailing ``%``/``x``. A trailing ``%`` does NOT rescale the value.
    """
    cleaned = token.strip().lower()
    cleaned = cleaned.rstrip("%x").strip()
    cleaned = cleaned.replace(",", "")
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_figures(texts: list[str]) -> list[float]:
    """Extract every signed numeric figure from the given strings (design 4.2b)."""
    figures: list[float] = []
    for text in texts:
        for match in _FIGURE_RE.findall(text):
            value = _parse_figure(match)
            if value is not None:
                figures.append(value)
    return figures


def _is_allowed(x: float, allowable: list[float]) -> bool:
    """A signed figure is allowed if it is within tolerance of any allowable
    value: ``abs(x - a) <= max(ABS_TOL, REL_TOL * abs(a))`` (design 4.2c)."""
    for a in allowable:
        if abs(x - a) <= max(NUMBER_CHECK_ABS_TOL, NUMBER_CHECK_REL_TOL * abs(a)):
            return True
    return False


def _number_check(rec: Recommendation, allowable: list[float]) -> None:
    """Enforce the number-check on ``rec`` (design 4.2). Raises
    :class:`NumberCheckError` naming every offending figure."""
    texts = [rec.rationale, *rec.evidence]
    offending = [x for x in _extract_figures(texts) if not _is_allowed(x, allowable)]
    if offending:
        raise NumberCheckError(offending=offending)


def analyze(
    ticker: str,
    as_of: date,
    portfolio: dict | None = None,
    snapshot_sink=None,
) -> Recommendation:
    """Return a deep-lane :class:`Recommendation` for ``ticker`` at ``as_of``.

    Args:
        ticker: Non-empty symbol to analyze.
        as_of: The date the recommendation is anchored to.
        portfolio: Optional mapping of ticker -> numeric holding (folded into
            the prompt context; does not change ``snapshot_id``).
        snapshot_sink: Optional forward-compatible persistence seam (design
            §7.4). When ``None`` (the only Step-2 case) it is a strict no-op and
            all behavior — including the computed ``snapshot_id`` — is
            unchanged. Steps 3/4 may pass a callable to persist the snapshot the
            recommendation references; no Step-2 code passes a non-None sink.

    Raises:
        ValueError: On bad inputs or an unavailable data snapshot.
        DeepValidationError: On a gateway ``valid=false`` or a construction error.
        NumberCheckError: When the model cites a figure absent from the snapshot.
    """
    if not isinstance(ticker, str) or not ticker.strip():
        raise ValueError("ticker must be a non-empty string")
    if not isinstance(as_of, date):
        raise ValueError("as_of must be a date")
    validated_portfolio = _validate_portfolio(portfolio)

    # --- Steps 1-2: build the snapshot + prompt --------------------------------
    pipeline = DataPipeline(ticker=ticker, interval="1d")
    df = pipeline.get_enriched_data(
        end_date=as_of.isoformat(),
        interval="1d",
        lookback_days=200,
        use_cache=False,
    )
    if df is None or getattr(df, "empty", True):
        raise ValueError("deep analyze() requires a non-empty data snapshot")

    summary = TechnicalIndicators.summarize(df)
    regime = RegimeAgent().classify(df)
    snapshot_id = compute_snapshot_id(ticker, as_of, df)

    # Forward-compatible persistence seam (design §7.4). A no-op when no sink is
    # wired (the only Step-2 case); never changes the computed snapshot_id.
    if snapshot_sink is not None:
        snapshot_sink(snapshot_id, ticker, as_of, df)

    prompt = _build_prompt(
        ticker,
        as_of,
        summary,
        _recent_bars(df),
        regime,
        validated_portfolio,
    )

    # ==========================================================================
    # TODO (unification-plan Step 4.2): wire the real TradingAgents LangGraph.
    # --------------------------------------------------------------------------
    # The real deep lane replaces the hand-built prompt + single gateway call
    # (steps 2-3 here) with a TradingAgents graph run. TradingAgents lives at
    # `third_party/TradingAgents` (pinned read-only submodule) and is configured
    # by CONFIG ONLY (STEERING rule 2 — never edit its source):
    #   llm_provider     = "openai_compatible"
    #   backend_url      = the gateway base URL (never a paid endpoint)
    #   max_debate_rounds = 1   (risk rounds 1)
    #   analysts         = ["market", "news"]
    #   checkpoint_enabled = True
    #   temperature      = low
    # The number-check and the Recommendation mapping below wrap whatever the
    # graph returns, so swapping the stub for the real graph does NOT change
    # this adapter's contract. When wired, bump DEEP_PROMPT_VERSION to
    # "deep-v2" so stored recommendations stay attributable to the graph that
    # produced them.
    # ==========================================================================

    # --- Steps 3-4: gateway /deep call + schema validation ---------------------
    response = generate_sync(
        prompt,
        route="deep",
        json_schema=_recommendation_json_schema(),
        temperature=0.1,
    )
    if not response.get("valid"):
        raise DeepValidationError(
            f"gateway returned invalid output: {response.get('error')}"
        )
    try:
        obj = json.loads(response["text"])
        model_rec = Recommendation(**obj)
    except (ValueError, TypeError) as exc:
        raise DeepValidationError(f"could not construct Recommendation: {exc}") from exc

    # --- Step 5: number-check --------------------------------------------------
    allowable = _build_allowable_set(df, summary, regime, validated_portfolio)
    _number_check(model_rec, allowable)

    # --- Step 6: success return ------------------------------------------------
    return Recommendation(
        ticker=ticker,
        as_of=as_of,
        action=model_rec.action,
        confidence=model_rec.confidence,
        horizon_days=model_rec.horizon_days,
        rationale=model_rec.rationale,
        evidence=model_rec.evidence,
        source="deep",
        model_id=response.get("model_id"),
        prompt_version=DEEP_PROMPT_VERSION,
        snapshot_id=snapshot_id,
    )
