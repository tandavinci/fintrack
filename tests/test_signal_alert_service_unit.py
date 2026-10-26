"""OFFLINE unit tests for the signal-alert generator's PURE logic (Step 5 / G5).

These exercise the DB-free helpers in
``backend.app.services.signal_alert_service`` directly — the volatility math,
the ``dedup_hash`` determinism, and the AI-alert actionable/confidence gating —
by feeding plain inputs (number series, field values, tiny stand-in objects).
No Postgres, no Redis, no network: this module is part of the fast offline suite.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone

import pytest

from backend.app.services.signal_alert_service import (
    AI_CONFIDENCE_THRESHOLD,
    RISK_VOL_CRITICAL,
    RISK_VOL_MIN_BARS,
    RISK_VOL_THRESHOLD,
    TRADING_DAYS_PER_YEAR,
    _ai_alert_from_rec,
    _ai_is_actionable,
    _dedup_hash,
    _news_alert_from_item,
    _risk_alert_from_series,
    annualized_volatility,
    build_ai_alerts,
    build_news_alerts,
    build_risk_alerts,
    generate_and_store,
)


# --- volatility math ---------------------------------------------------------


def test_annualized_volatility_matches_hand_computation():
    """A known close series -> the exact sample-std * sqrt(252) number."""
    closes = [100.0, 102.0, 101.0, 103.0, 105.0]
    returns = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    expected = math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)

    got = annualized_volatility(closes)
    assert got is not None
    assert got == pytest.approx(expected, rel=1e-12)


def test_annualized_volatility_flat_series_is_zero():
    assert annualized_volatility([50.0, 50.0, 50.0, 50.0]) == pytest.approx(0.0)


def test_annualized_volatility_too_few_points_returns_none():
    assert annualized_volatility([]) is None
    assert annualized_volatility([100.0]) is None
    assert annualized_volatility([100.0, 101.0]) is None  # only one return


def test_annualized_volatility_ignores_nan_and_nonpositive():
    clean = annualized_volatility([100.0, 102.0, 101.0, 103.0])
    noisy = annualized_volatility([100.0, 102.0, float("nan"), 101.0, 0.0, 103.0])
    # The nan / 0.0 are dropped, leaving the same clean series.
    assert noisy == pytest.approx(clean)


def test_risk_alert_trips_only_above_threshold():
    """A high-vol series trips a RISK alert; a calm one does not."""
    # Big alternating swings -> high annualized vol (well above the 40% gate).
    volatile = [100.0, 130.0, 95.0, 125.0, 90.0, 120.0, 92.0, 118.0, 94.0, 122.0, 96.0]
    assert len(volatile) >= RISK_VOL_MIN_BARS
    vol = annualized_volatility(volatile)
    assert vol is not None and vol >= RISK_VOL_THRESHOLD

    alert = _risk_alert_from_series("TCS.NS", date(2024, 1, 15), volatile)
    assert alert is not None
    assert alert["alert_kind"] == "RISK"
    assert alert["ticker"] == "TCS.NS"
    assert "TCS.NS" in alert["title"]
    assert alert["severity"] in ("warning", "critical")

    # A gently drifting series stays under the threshold -> no alert.
    calm = [100.0 + i * 0.05 for i in range(12)]
    assert annualized_volatility(calm) < RISK_VOL_THRESHOLD
    assert _risk_alert_from_series("CALM.NS", date(2024, 1, 15), calm) is None


def test_risk_alert_skips_when_too_few_bars():
    short = [100.0, 101.0, 102.0]  # fewer than RISK_VOL_MIN_BARS
    assert _risk_alert_from_series("THIN.NS", date(2024, 1, 15), short) is None


def test_risk_alert_severity_critical_above_critical_gate():
    # Extreme alternating swings push annualized vol past the critical gate.
    wild = [100.0, 160.0, 80.0, 155.0, 78.0, 150.0, 82.0, 158.0, 79.0, 152.0, 81.0]
    vol = annualized_volatility(wild)
    assert vol is not None and vol >= RISK_VOL_CRITICAL
    alert = _risk_alert_from_series("WILD.NS", date(2024, 1, 15), wild)
    assert alert is not None
    assert alert["severity"] == "critical"


# --- dedup_hash determinism --------------------------------------------------


def test_dedup_hash_is_deterministic():
    a = _dedup_hash("AI", "RELIANCE.NS", date(2024, 1, 15), "BUY")
    b = _dedup_hash("AI", "RELIANCE.NS", date(2024, 1, 15), "BUY")
    assert a == b
    assert len(a) == 64  # sha256 hex digest


def test_dedup_hash_varies_with_each_input():
    base = _dedup_hash("AI", "RELIANCE.NS", date(2024, 1, 15), "BUY")
    assert base != _dedup_hash("RISK", "RELIANCE.NS", date(2024, 1, 15), "BUY")
    assert base != _dedup_hash("AI", "TCS.NS", date(2024, 1, 15), "BUY")
    assert base != _dedup_hash("AI", "RELIANCE.NS", date(2024, 1, 16), "BUY")
    assert base != _dedup_hash("AI", "RELIANCE.NS", date(2024, 1, 15), "SELL")


def test_dedup_hash_none_ticker_is_stable():
    a = _dedup_hash("NEWS", None, date(2024, 1, 15), "k")
    b = _dedup_hash("NEWS", None, date(2024, 1, 15), "k")
    assert a == b
    # None collapses to "" — distinct from an actual empty-string ticker only in
    # that both map to the same bucket (documented behavior); still stable.
    assert a == _dedup_hash("NEWS", "", date(2024, 1, 15), "k")


# --- AI-alert actionable / confidence gating --------------------------------


def test_ai_is_actionable_gate():
    assert _ai_is_actionable("BUY", 0.72) is True
    assert _ai_is_actionable("SELL", AI_CONFIDENCE_THRESHOLD) is True  # boundary inclusive
    assert _ai_is_actionable("BUY", AI_CONFIDENCE_THRESHOLD - 0.01) is False
    assert _ai_is_actionable("HOLD", 0.99) is False  # HOLD never actionable


def test_ai_alert_buy_high_conf_emits():
    alert = _ai_alert_from_rec(
        ticker="RELIANCE.NS",
        as_of=date(2024, 1, 15),
        action="BUY",
        confidence=0.72,
        rationale="Golden cross with rising volume.",
    )
    assert alert is not None
    assert alert["alert_kind"] == "AI"
    assert alert["ticker"] == "RELIANCE.NS"
    assert "BUY" in alert["title"]
    assert "0.72" in alert["title"]
    assert alert["detail"] == "Golden cross with rising volume."


def test_ai_alert_hold_emits_nothing():
    assert (
        _ai_alert_from_rec("X.NS", date(2024, 1, 15), "HOLD", 0.95, "sideways")
        is None
    )


def test_ai_alert_low_confidence_emits_nothing():
    assert (
        _ai_alert_from_rec("X.NS", date(2024, 1, 15), "BUY", 0.40, "weak")
        is None
    )


# --- news alert reuses the article's own dedup_hash --------------------------


@dataclass
class _NewsStub:
    ticker: str | None
    headline: str
    source: str
    url: str
    dedup_hash: str
    published_at: datetime | None = None


def test_news_alert_reuses_article_dedup_hash():
    item = _NewsStub(
        ticker="INFY.NS",
        headline="Infosys raises FY guidance",
        source="BSE",
        url="https://example.test/a",
        dedup_hash="abc123" * 8,  # already unique per article
        published_at=datetime(2024, 1, 15, 9, 0, tzinfo=timezone.utc),
    )
    alert = _news_alert_from_item(date(2024, 1, 15), item)
    assert alert["alert_kind"] == "NEWS"
    assert alert["ticker"] == "INFY.NS"
    assert alert["title"] == "Infosys raises FY guidance"
    assert alert["dedup_hash"] == item.dedup_hash  # REUSED, not recomputed
    assert "BSE" in alert["detail"] and "example.test" in alert["detail"]


def test_news_alert_title_is_trimmed_to_200():
    long_headline = "x" * 500
    item = _NewsStub(
        ticker=None,
        headline=long_headline,
        source="RSS",
        url="https://example.test/b",
        dedup_hash="d" * 64,
    )
    alert = _news_alert_from_item(date(2024, 1, 15), item)
    assert len(alert["title"]) <= 200


# --- builders are importable / callable (shape only; DB paths are integration) -


def test_public_builders_are_callable():
    # Smoke: the three builders and the entry point exist and are callable.
    # Their DB behavior is covered in the integration test.
    assert callable(build_ai_alerts)
    assert callable(build_risk_alerts)
    assert callable(build_news_alerts)
    assert callable(generate_and_store)
