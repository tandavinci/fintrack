"""The deep lane adapter (design 4, unification-plan Step 4.1 / 4.6).

``analyze(ticker, as_of, portfolio=None) -> Recommendation`` is a **plain
synchronous callable** invoked by a Celery ``deep`` worker or an offline script
(design 4.3). It is **never** called from a web request handler (STEERING
rule 5). To keep that constraint structurally enforced this module imports no
web-framework / ``Request`` symbol, so it is impossible to mount as a route.
(The adapter source is deliberately free of the web framework's name so a
structural grep can assert the boundary; see tests 7.3.)

Flow (G4 — real graph wired, design 4.2):
  1-2. Build a data snapshot via the quant ``DataPipeline`` (same enriched df
       and ``compute_snapshot_id`` as ``quant/recommend.py`` so quant and deep
       reference one snapshot).
  3.   PROVENANCE GATE: TradingAgents fetches its OWN price history (a separate
       Yahoo series) for the run. Before trusting its report we cross-check that
       its closes move the same way ours do (``deep.provenance.compare_series``
       on day-to-day percent moves — split/dividend adjustment cancels). On a
       mismatch (or an unavailable TA fetch — uncertainty is not a pass) we
       short-circuit to a neutral HOLD and never run the graph.
  4.   RUN THE GRAPH: a real ``TradingAgentsGraph`` (``market``/``news``
       analysts, 1 debate round, checkpointing) run BY CONFIG ONLY (STEERING
       rule 2 — TradingAgents source is never edited). Every model call is
       routed through the gateway (STEERING rule 1), never at Ollama directly:
       ``backend_url`` points at ``GATEWAY_BASE_URL + "/v1"``.
  5.   RATING -> ACTION: the graph returns a 5-tier rating or ``REVIEW``;
       ``REVIEW`` is not tradeable (-> HOLD), the tiers map Buy/Overweight->BUY,
       Hold->HOLD, Underweight/Sell->SELL, anything unexpected -> HOLD.
  6.   EVIDENCE GATE: require populated, non-fallback market+news reports AND at
       least one snapshot-backed figure in them; otherwise a neutral HOLD with
       empty evidence (a report that cites nothing from our snapshot is not
       actionable).
  7.   NUMBER-CHECK (STEERING rule 3): every numeric figure in the final
       ``rationale``/``evidence`` must be present in the snapshot within
       tolerance, sign-preserving. An unmatched figure raises
       :class:`NumberCheckError`. This gate is never loosened.
  8.   Return a ``Recommendation`` with ``source="deep"`` and OUR authoritative
       ``snapshot_id``.

The number-check invariant is owned here: deep is the lane where a model writes
free-form numeric claims, so no unchecked model number escapes into a
``Recommendation`` (design 4.4).
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import date, timedelta
from numbers import Real
from typing import Any

# The real multi-agent graph and its rating vocabulary. These live in the pinned
# read-only ``third_party/TradingAgents`` submodule and are wired BY CONFIG ONLY
# (STEERING rule 2); nothing here edits TradingAgents source.
from tradingagents.agents.rating import is_review
from tradingagents.dataflows.vendors.yahoo.market import get_closes
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

from backend.app.schemas.recommendation import Action, Recommendation
from backend.app.schemas.snapshot import SNAPSHOT_TAIL_N, compute_snapshot_id
from deep.provenance import compare_series
from gateway.settings import settings
from quant.agents.regime_agent import RegimeAgent
from quant.data.indicators import TechnicalIndicators
from quant.data.pipeline import DataPipeline

# Graph attribution for stored recommendations (design 4.2 / 4.3). Bumped to
# "deep-v2" now that the real TradingAgents graph is wired (was "deep-v1" for
# the single-gateway-call stub).
DEEP_PROMPT_VERSION = "deep-v2"

# Local scratch root for a TradingAgents run's artifacts (logs/cache/memory),
# kept out of the user home and out of git. ``_build_run_config`` points
# results_dir/data_cache_dir/memory_log_path under here so a run is consistent
# and self-contained.
_TA_ROOT = os.path.join(tempfile.gettempdir(), "fintrack_ta")


def deep_memory_log_path() -> str:
    """The memory-log markdown path a deep run writes to (and the mirror reads).

    Single source of truth for the path so the service layer can construct a
    ``TradingMemoryLog`` over the EXACT file a run used, without duplicating the
    join. This is config-only (STEERING rule 2) and stays free of DB/web imports.
    """
    return os.path.join(_TA_ROOT, "memory", "trading_memory.md")

# Default horizon for a deep recommendation, mirroring the quant lane's
# HORIZON_DAYS so both lanes anchor to the same forward window (design 4.2h).
HORIZON_DAYS = 10

# How far back to pull closes for the provenance cross-check. ``as_of`` minus
# this many CALENDAR days gives a window that comfortably overlaps our snapshot
# tail (~60 trading days) once weekends/holidays are dropped.
PROVENANCE_LOOKBACK_DAYS = 90

# Rating-tier -> confidence for a successful deep recommendation (design 4.2h).
# Simple, documented, and capped: the strongest convictions (Buy/Sell) read
# highest, Hold sits neutral-low. Not an average of anything — a single tier in.
_RATING_CONFIDENCE = {
    "Buy": 0.8,
    "Overweight": 0.65,
    "Hold": 0.4,
    "Underweight": 0.65,
    "Sell": 0.8,
}

# Case-insensitive substrings that mark a report as a non-answer rather than
# real analysis. A report that is only one of these carries no evidence.
_FALLBACK_MARKERS = (
    "no significant news",
    "no data",
    "n/a",
    "not available",
    "no relevant",
    "nothing to report",
    "unavailable",
)

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


def _build_run_config() -> dict:
    """Build the TradingAgents run config BY CONFIG ONLY (STEERING rule 2).

    Copies ``DEFAULT_CONFIG`` and overrides only what the deep lane needs: the
    generic OpenAI-compatible provider pointed at OUR gateway (never a paid
    endpoint, never Ollama directly — STEERING rule 1), one debate/risk round,
    checkpointing, a low temperature, and writable local results/cache dirs
    under the OS temp dir so a run does not pollute the user profile. The
    TradingAgents source is never edited; this dict is the whole configuration
    surface.
    """
    config = dict(DEFAULT_CONFIG)

    # STEERING rule 1: the gateway is the only path to Ollama. The
    # ``openai_compatible`` provider REQUIRES a base_url; we point it at the
    # gateway's /v1, not at :11434.
    config["llm_provider"] = "openai_compatible"
    config["backend_url"] = settings.GATEWAY_BASE_URL.rstrip("/") + "/v1"
    config["deep_think_llm"] = settings.GATEWAY_MODEL
    config["quick_think_llm"] = settings.GATEWAY_MODEL

    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    config["checkpoint_enabled"] = True
    config["temperature"] = 0.1

    # Keep a run's artifacts out of the user home and out of git (STEERING
    # rule: do not hardcode the user profile path). A repo-independent local
    # scratch dir under the OS temp root.
    ta_root = _TA_ROOT
    config["results_dir"] = os.path.join(ta_root, "logs")
    config["data_cache_dir"] = os.path.join(ta_root, "cache")
    # Mirror target: pin the memory log under the same scratch root (NOT the
    # DEFAULT_CONFIG ~/.tradingagents default) so the mirror reads the exact
    # file a run wrote. Still config-only — TradingAgents source is untouched.
    config["memory_log_path"] = deep_memory_log_path()

    return config


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


def _our_closes(df) -> dict[str, float]:
    """Our snapshot tail as an ISO-date -> close mapping for the provenance gate.

    Uses the last ``SNAPSHOT_TAIL_N`` (~60) rows — the same tail the snapshot is
    built from — so the comparison is against the exact window we trust.
    """
    tail = df.tail(SNAPSHOT_TAIL_N)
    closes: dict[str, float] = {}
    for idx, row in tail.iterrows():
        iso = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
        closes[iso] = float(row["Close"])
    return closes


def _ta_closes(ticker: str, as_of: date) -> dict[str, float]:
    """TradingAgents' OWN closes over a comparable window, ISO-date -> close.

    ``get_closes`` treats ``end`` as EXCLUSIVE, so we pass ``as_of + 1 day`` to
    include the ``as_of`` bar; ``start`` reaches back ``PROVENANCE_LOOKBACK_DAYS``
    calendar days so the series overlaps our snapshot tail. Raises on an empty
    or failed fetch — the caller treats that as a mismatch (uncertainty is not a
    pass).
    """
    start = (as_of - timedelta(days=PROVENANCE_LOOKBACK_DAYS)).isoformat()
    end = (as_of + timedelta(days=1)).isoformat()
    series = get_closes(ticker, start, end)
    if series is None or getattr(series, "empty", True):
        raise ValueError("TradingAgents returned no closes for the provenance window")
    closes: dict[str, float] = {}
    for idx, value in series.items():
        iso = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
        closes[iso] = float(value)
    return closes


def _map_rating_to_action(signal: str):
    """Map a graph signal to a Recommendation action (design 4.2e).

    ``REVIEW`` is not tradeable -> HOLD. Buy/Overweight -> BUY, Hold -> HOLD,
    Underweight/Sell -> SELL. Anything unexpected falls back to HOLD (safe
    default) so an unrecognized string never becomes a trade.
    """
    if is_review(signal):
        return Action.HOLD
    mapping = {
        "Buy": Action.BUY,
        "Overweight": Action.BUY,
        "Hold": Action.HOLD,
        "Underweight": Action.SELL,
        "Sell": Action.SELL,
    }
    return mapping.get(signal, Action.HOLD)


def _is_fallback_report(text: str) -> bool:
    """Whether a report string is empty/whitespace or a known non-answer marker."""
    if not text or not text.strip():
        return True
    lowered = text.strip().lower()
    return any(marker in lowered for marker in _FALLBACK_MARKERS)


def _evidence_sufficient(final_state: dict, allowable: list[float]) -> bool:
    """Whether the graph's reports carry actionable, snapshot-backed evidence
    (design 4.2f).

    Requires: a populated, non-fallback ``market_report`` AND ``news_report``,
    AND at least one numeric figure extracted from those reports that passes the
    number-check (``_is_allowed``). A report that cites nothing from our snapshot
    is not actionable, so it short-circuits to a neutral HOLD upstream.
    """
    market = final_state.get("market_report") or ""
    news = final_state.get("news_report") or ""
    if _is_fallback_report(market) or _is_fallback_report(news):
        return False
    figures = _extract_figures([market, news])
    return any(_is_allowed(x, allowable) for x in figures)


def _compose_rationale(final_state: dict) -> str:
    """The recommendation rationale from the graph's final decision text.

    Prefer ``final_trade_decision``; fall back to a concise compose of the
    market + news reports when the decision text is empty.
    """
    decision = (final_state.get("final_trade_decision") or "").strip()
    if decision:
        return decision
    parts = [
        final_state.get("market_report") or "",
        final_state.get("news_report") or "",
    ]
    return "\n\n".join(p.strip() for p in parts if p.strip())


def _snapshot_backed_evidence(final_state: dict, allowable: list[float]) -> list[str]:
    """Evidence lines citing ONLY snapshot-backed figures from the reports.

    Each figure the reports mention that passes ``_is_allowed`` is emitted as a
    short, self-describing evidence string. Only allowed figures are cited so
    the subsequent number-check cannot be tripped by evidence we generate.
    """
    market = final_state.get("market_report") or ""
    news = final_state.get("news_report") or ""
    evidence: list[str] = []
    seen: set[float] = set()
    for source_name, text in (("market", market), ("news", news)):
        for x in _extract_figures([text]):
            if x in seen or not _is_allowed(x, allowable):
                continue
            seen.add(x)
            evidence.append(f"{source_name}_report figure {x}")
    return evidence


def _neutral_hold(
    *,
    ticker: str,
    as_of: date,
    snapshot_id: str,
    rationale: str,
    evidence: list[str],
    confidence: float,
) -> Recommendation:
    """A non-tradeable deep HOLD used by the provenance and evidence gates.

    Carries OUR authoritative ``snapshot_id`` and the deep attribution, so a
    short-circuited run is still a first-class, auditable deep recommendation.
    """
    return Recommendation(
        ticker=ticker,
        as_of=as_of,
        action=Action.HOLD,
        confidence=confidence,
        horizon_days=HORIZON_DAYS,
        rationale=rationale,
        evidence=evidence,
        source="deep",
        model_id=settings.GATEWAY_MODEL,
        prompt_version=DEEP_PROMPT_VERSION,
        snapshot_id=snapshot_id,
    )


def analyze(
    ticker: str,
    as_of: date,
    portfolio: dict | None = None,
    snapshot_sink=None,
) -> Recommendation:
    """Return a deep-lane :class:`Recommendation` for ``ticker`` at ``as_of``.

    Runs the real TradingAgents graph (market + news analysts, 1 debate round)
    BY CONFIG ONLY through the gateway, fronted by a provenance gate and an
    evidence gate and backed by the number-check. OUR ``snapshot_id`` stays
    authoritative in every return path.

    Args:
        ticker: Non-empty symbol to analyze.
        as_of: The date the recommendation is anchored to.
        portfolio: Optional mapping of ticker -> numeric holding. Validated and
            folded only into the number-check allowable set (as in the prior
            pass); it is NOT mapped into TradingAgents' PortfolioContext here
            (out of scope for this pass — the graph is run with portfolio=None).
            It does not change ``snapshot_id``.
        snapshot_sink: Optional forward-compatible persistence seam (design
            §7.4). When ``None`` it is a strict no-op and all behavior —
            including the computed ``snapshot_id`` — is unchanged. A callable may
            persist the snapshot the recommendation references.

    Raises:
        ValueError: On bad inputs or an unavailable data snapshot.
        DeepValidationError: When the TradingAgents graph run fails.
        NumberCheckError: When the final text cites a figure absent from the
            snapshot (STEERING rule 3; never loosened).
    """
    if not isinstance(ticker, str) or not ticker.strip():
        raise ValueError("ticker must be a non-empty string")
    if not isinstance(as_of, date):
        raise ValueError("as_of must be a date")
    validated_portfolio = _validate_portfolio(portfolio)

    # --- Steps 1-2: build OUR snapshot (authoritative) -------------------------
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
    # wired; never changes the computed snapshot_id.
    if snapshot_sink is not None:
        snapshot_sink(snapshot_id, ticker, as_of, df)

    # The number-check allowable set is built once up front: both the evidence
    # gate and the final number-check reference it.
    allowable = _build_allowable_set(df, summary, regime, validated_portfolio)

    # --- Step 3: PROVENANCE GATE -----------------------------------------------
    # TradingAgents fetches its OWN closes for the run; confirm they move the
    # same way ours do before trusting its report. An unavailable/failed TA
    # fetch is treated as a mismatch — uncertainty is not a pass.
    ours = _our_closes(df)
    try:
        theirs = _ta_closes(ticker, as_of)
        provenance = compare_series(ours, theirs)
    except Exception:  # noqa: BLE001 — any TA fetch failure is a non-pass
        return _neutral_hold(
            ticker=ticker,
            as_of=as_of,
            snapshot_id=snapshot_id,
            rationale="PROVENANCE_MISMATCH: TradingAgents data unavailable",
            evidence=[],
            confidence=0.0,
        )
    if not provenance.match:
        return _neutral_hold(
            ticker=ticker,
            as_of=as_of,
            snapshot_id=snapshot_id,
            rationale="PROVENANCE_MISMATCH: " + provenance.reason,
            evidence=[
                f"overlap_days={provenance.overlap_days} "
                f"max_abs_ratio_diff={provenance.max_abs_ratio_diff}"
            ],
            confidence=0.0,
        )

    # --- Step 4: RUN THE GRAPH (config-only, via the gateway) ------------------
    run_config = _build_run_config()
    try:
        graph = TradingAgentsGraph(
            selected_analysts=("market", "news"),
            config=run_config,
        )
        final_state, signal = graph.propagate(
            ticker, as_of.isoformat(), asset_type="stock", portfolio=None
        )
    except Exception as exc:  # noqa: BLE001 — a failed graph run is a hard error
        raise DeepValidationError(f"TradingAgents graph run failed: {exc}") from exc

    # --- Step 5: RATING -> ACTION ----------------------------------------------
    action = _map_rating_to_action(signal)

    # --- Step 6: EVIDENCE GATE -------------------------------------------------
    if not _evidence_sufficient(final_state, allowable):
        return _neutral_hold(
            ticker=ticker,
            as_of=as_of,
            snapshot_id=snapshot_id,
            rationale=(
                "INSUFFICIENT_EVIDENCE: the deep reports lacked populated "
                "market+news analysis with a snapshot-backed figure"
            ),
            evidence=[],
            confidence=0.1,
        )

    # --- Step 7: build the final recommendation + NUMBER-CHECK -----------------
    rationale = _compose_rationale(final_state)
    evidence = _snapshot_backed_evidence(final_state, allowable)
    confidence = _RATING_CONFIDENCE.get(signal, 0.4) if not is_review(signal) else 0.4

    rec = Recommendation(
        ticker=ticker,
        as_of=as_of,
        action=action,
        confidence=confidence,
        horizon_days=HORIZON_DAYS,
        rationale=rationale,
        evidence=evidence,
        source="deep",
        model_id=settings.GATEWAY_MODEL,
        prompt_version=DEEP_PROMPT_VERSION,
        snapshot_id=snapshot_id,
    )

    # STEERING rule 3 (never loosened): every cited figure must be snapshot-
    # backed. Raises NumberCheckError naming any offending figure.
    _number_check(rec, allowable)

    return rec
