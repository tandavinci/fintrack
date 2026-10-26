"""Signal-alert generator service (Step 5 / G5 system-generated alerts).

Turns the nightly pipeline output into three flavors of SYSTEM-generated alert
and persists them (idempotently, keyed on ``dedup_hash``) into ``signal_alerts``.
This is a SEPARATE subsystem from the user-configured ``alerts`` table — the
latter is untouched.

Design for testability: the pure logic (given source ROWS -> alert dicts) is
factored into ``_ai_alerts_from_rows`` / ``_risk_alerts_from_series`` /
``_news_alerts_from_items`` and small helpers (``annualized_volatility``,
``_dedup_hash``, ``_ai_is_actionable``) that take plain inputs and need NO DB,
so the offline unit tests exercise them directly. The public ``build_*``
functions do the DB reads (reusing the existing sync repos) and delegate to the
pure helpers; ``generate_and_store`` wires all three to the upsert repo, wrapping
each source in try/except so one failing source never aborts the others.

Numbers come from stored data (daily closes, stored recommendations, ingested
headlines) — models explain, code computes (STEERING rule 3).
"""

from __future__ import annotations

import hashlib
import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

from sqlalchemy import select, union

from backend.app.database import create_sync_session
from backend.app.models.portfolio import Holding
from backend.app.models.stock import Stock
from backend.app.repositories.news_repository import SyncNewsRepository
from backend.app.repositories.price_bar_repository import SyncPriceBarRepository
from backend.app.repositories.recommendation_repository import (
    SyncRecommendationRepository,
)
from backend.app.repositories.signal_alert_repository import SyncSignalAlertRepository

logger = logging.getLogger(__name__)

# --- tunable thresholds (named constants so they're easy to adjust) ----------

#: Minimum quant confidence for an actionable BUY/SELL to surface as an AI alert.
AI_CONFIDENCE_THRESHOLD = 0.6
#: Annualized volatility (fraction) above which a held ticker trips a RISK alert.
RISK_VOL_THRESHOLD = 0.4
#: Annualized vol above this is "critical" (vs "warning"); otherwise "warning".
RISK_VOL_CRITICAL = 0.7
#: Trailing daily closes used for the volatility estimate.
RISK_VOL_WINDOW = 30
#: Minimum daily closes required to compute volatility (else skip the ticker).
RISK_VOL_MIN_BARS = 10
#: Trading days per year used to annualize daily-return volatility.
TRADING_DAYS_PER_YEAR = 252
#: Look-back window for "fresh" news (hours).
NEWS_FRESH_HOURS = 24
#: Cap on the number of NEWS alerts emitted per run (newest first).
NEWS_MAX_ALERTS = 20
#: Daily interval tag used for price-bar reads.
_INTERVAL_DAILY = "1d"


# --- pure helpers (no DB — directly unit-tested) -----------------------------


def _dedup_hash(kind: str, ticker: Optional[str], as_of: date, key: str) -> str:
    """Deterministic sha256 over (kind | ticker | as_of | stable-key).

    ``ticker`` None collapses to an empty string so market-wide alerts hash
    stably. Same inputs -> same hash (so a re-run dedups), different inputs ->
    different hash.
    """
    parts = "|".join([kind, ticker or "", as_of.isoformat(), key])
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


def annualized_volatility(closes: Sequence[float]) -> Optional[float]:
    """Annualized volatility from a series of daily closes.

    Computes simple daily returns, their SAMPLE standard deviation (ddof=1), and
    annualizes by ``sqrt(252)``. Returns ``None`` when there are too few closes
    to form at least two returns (so the caller can skip rather than error).
    """
    usable = [float(c) for c in closes if c is not None and c == c and c > 0]
    if len(usable) < 3:
        return None
    returns = [usable[i] / usable[i - 1] - 1.0 for i in range(1, len(usable))]
    n = len(returns)
    if n < 2:
        return None
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)  # sample variance
    daily_std = math.sqrt(variance)
    return daily_std * math.sqrt(TRADING_DAYS_PER_YEAR)


def _risk_severity(vol: float) -> str:
    """Map an annualized volatility to a severity label."""
    return "critical" if vol >= RISK_VOL_CRITICAL else "warning"


def _ai_is_actionable(action: str, confidence: float) -> bool:
    """True iff the quant action is BUY/SELL AND confidence clears the gate."""
    return action in ("BUY", "SELL") and confidence >= AI_CONFIDENCE_THRESHOLD


def _trim(text: str, limit: int) -> str:
    """Trim ``text`` to ``limit`` chars (ellipsis when cut); never raise on None."""
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _ai_alert_from_rec(ticker: str, as_of: date, action: str, confidence: float, rationale: str) -> Optional[dict]:
    """Pure: build one AI alert dict from a quant recommendation's fields.

    Returns ``None`` when the signal is not actionable or confidence is too low
    (HOLD, or BUY/SELL below the threshold -> no alert).
    """
    if not _ai_is_actionable(action, confidence):
        return None
    pct = f"{confidence:.2f}"
    return {
        "ticker": ticker,
        "alert_kind": "AI",
        "severity": "info",
        "title": f"Quant {action} signal: {ticker} (conf {pct})",
        "detail": _trim(rationale, 2000),
        "as_of": as_of,
        "dedup_hash": _dedup_hash("AI", ticker, as_of, action),
    }


def _risk_alert_from_series(ticker: str, as_of: date, closes: Sequence[float]) -> Optional[dict]:
    """Pure: build one RISK alert dict from a held ticker's recent closes.

    Returns ``None`` when there aren't enough bars to compute volatility, or the
    volatility is below the threshold (not a risk worth surfacing).
    """
    if len(closes) < RISK_VOL_MIN_BARS:
        return None
    vol = annualized_volatility(closes)
    if vol is None or vol < RISK_VOL_THRESHOLD:
        return None
    pct = round(vol * 100)
    return {
        "ticker": ticker,
        "alert_kind": "RISK",
        "severity": _risk_severity(vol),
        "title": f"Elevated volatility: {ticker} ({pct}%/yr)",
        "detail": (
            f"Annualized volatility {pct}% over the last {len(closes)} daily closes "
            f"(threshold {round(RISK_VOL_THRESHOLD * 100)}%)."
        ),
        "as_of": as_of,
        "dedup_hash": _dedup_hash("RISK", ticker, as_of, "vol"),
    }


def _news_alert_from_item(as_of: date, item) -> dict:
    """Pure: build one NEWS alert dict from a news item (reuses its dedup_hash).

    ``item`` is any object exposing ``ticker``, ``headline``, ``source``, ``url``
    and ``dedup_hash`` (the ORM row, or a plain stand-in in tests). The news
    item's own ``dedup_hash`` is the alert's dedup key — it is already unique per
    article, so an article only ever yields one alert.
    """
    source = getattr(item, "source", "") or ""
    url = getattr(item, "url", "") or ""
    detail = f"{source} — {url}".strip(" —") if (source or url) else ""
    return {
        "ticker": getattr(item, "ticker", None),
        "alert_kind": "NEWS",
        "severity": "info",
        "title": _trim(getattr(item, "headline", "") or "(headline unavailable)", 200),
        "detail": _trim(detail, 2000),
        "as_of": as_of,
        "dedup_hash": getattr(item, "dedup_hash"),
    }


# --- universe helper (reused from the quant task pattern) --------------------


def _held_and_universe_tickers(session) -> tuple[list[str], list[str]]:
    """Return (held_tickers, universe_tickers).

    ``held`` = distinct ``Stock.ticker_symbol`` for current Holdings.
    ``universe`` = distinct tickers across Holdings + WatchlistItem (the nightly
    universe; the AI + NEWS builders scan it, the RISK builder scans held only).
    """
    from backend.app.models.portfolio import WatchlistItem

    held_q = select(Stock.ticker_symbol).join(Holding, Holding.stock_id == Stock.id)
    watch_q = select(Stock.ticker_symbol).join(
        WatchlistItem, WatchlistItem.stock_id == Stock.id
    )

    held = _distinct_nonempty(session.execute(held_q).scalars().all())
    universe_rows = session.execute(
        select(union(held_q, watch_q).subquery().c.ticker_symbol)
    ).scalars().all()
    universe = _distinct_nonempty(universe_rows)
    return held, universe


def _distinct_nonempty(rows: Iterable[Optional[str]]) -> list[str]:
    """De-duplicate preserving order, dropping falsy entries."""
    seen: set[str] = set()
    out: list[str] = []
    for value in rows:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


# --- builders (DB reads + delegate to the pure helpers) ----------------------


def build_ai_alerts(as_of: date, session=None) -> list[dict]:
    """AI alerts: one per universe ticker with an actionable quant signal."""
    owns = session is None
    session = session or create_sync_session()
    try:
        _, universe = _held_and_universe_tickers(session)
        rec_repo = SyncRecommendationRepository(session)
        alerts: list[dict] = []
        for ticker in universe:
            by_source = rec_repo.latest_by_source(ticker)
            rec = by_source.get("quant")
            if rec is None:
                continue
            alert = _ai_alert_from_rec(
                ticker=ticker,
                as_of=as_of,
                action=rec.action,
                confidence=float(rec.confidence),
                rationale=rec.rationale,
            )
            if alert is not None:
                alerts.append(alert)
        return alerts
    finally:
        if owns:
            session.close()


def build_risk_alerts(as_of: date, session=None) -> list[dict]:
    """RISK alerts: one per HELD ticker whose recent volatility is elevated."""
    owns = session is None
    session = session or create_sync_session()
    try:
        held, _ = _held_and_universe_tickers(session)
        bar_repo = SyncPriceBarRepository(session)
        # Read a generous window and keep the last RISK_VOL_WINDOW closes.
        end = datetime.combine(as_of, datetime.min.time()) + timedelta(days=1)
        start = end - timedelta(days=RISK_VOL_WINDOW * 3 + 10)
        alerts: list[dict] = []
        for ticker in held:
            bars = bar_repo.read_bars(ticker, _INTERVAL_DAILY, start, end)
            closes = [float(b.close) for b in bars][-RISK_VOL_WINDOW:]
            alert = _risk_alert_from_series(ticker, as_of, closes)
            if alert is not None:
                alerts.append(alert)
        return alerts
    finally:
        if owns:
            session.close()


def build_news_alerts(as_of: date, session=None) -> list[dict]:
    """NEWS alerts: one per fresh headline (last 24h) for universe + market-wide."""
    owns = session is None
    session = session or create_sync_session()
    try:
        _, universe = _held_and_universe_tickers(session)
        news_repo = SyncNewsRepository(session)
        as_of_dt = datetime.combine(as_of, datetime.max.time()).replace(
            tzinfo=timezone.utc
        )
        cutoff = as_of_dt - timedelta(hours=NEWS_FRESH_HOURS)

        # Universe tickers (each recent_for_ticker also returns market-wide rows,
        # i.e. ticker IS NULL) plus an explicit market-wide (None) pass. We
        # dedup by the news item's own dedup_hash so overlap is harmless.
        candidates = [None, *universe]
        by_hash: dict[str, object] = {}
        for ticker in candidates:
            for item in news_repo.recent_for_ticker(
                ticker, limit=NEWS_MAX_ALERTS, as_of=as_of_dt
            ):
                published = getattr(item, "published_at", None)
                if published is not None:
                    pub = published if published.tzinfo else published.replace(tzinfo=timezone.utc)
                    if pub < cutoff:
                        continue
                by_hash[item.dedup_hash] = item

        fresh = list(by_hash.values())
        # Newest first; rows without published_at sort last.
        fresh.sort(
            key=lambda it: getattr(it, "published_at", None) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return [_news_alert_from_item(as_of, it) for it in fresh[:NEWS_MAX_ALERTS]]
    finally:
        if owns:
            session.close()


def generate_and_store(as_of: Optional[date] = None) -> dict:
    """Run all three builders and upsert their alerts (idempotent on dedup_hash).

    Each builder is wrapped in try/except so one failing source (a dead symbol,
    a thin frame, an ingest hiccup) never aborts the others. Returns per-source
    BUILT counts plus the number of NEW rows actually INSERTED (dedup-aware).
    """
    as_of = as_of or date.today()
    logger.info("Generating signal alerts (as_of=%s)", as_of.isoformat())

    session = create_sync_session()
    try:
        ai_alerts = _safe_build(build_ai_alerts, "AI", as_of, session)
        risk_alerts = _safe_build(build_risk_alerts, "RISK", as_of, session)
        news_alerts = _safe_build(build_news_alerts, "NEWS", as_of, session)

        all_alerts = [*ai_alerts, *risk_alerts, *news_alerts]
        repo = SyncSignalAlertRepository(session)
        inserted = repo.upsert_many(all_alerts) if all_alerts else 0
    finally:
        session.close()

    summary = {
        "ai": len(ai_alerts),
        "risk": len(risk_alerts),
        "news": len(news_alerts),
        "inserted": inserted,
    }
    logger.info("Signal alert generation complete: %s", summary)
    return summary


def _safe_build(builder, label: str, as_of: date, session) -> list[dict]:
    """Run one builder, isolating its failure (logged, counted as empty)."""
    try:
        return builder(as_of, session=session)
    except Exception:
        logger.exception("signal-alert builder %s failed", label)
        return []
