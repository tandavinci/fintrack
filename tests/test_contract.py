"""Contract tests for the shared Recommendation schema (tests 7.x).

These lock the single Recommendation contract: a well-formed payload
validates, out-of-range confidence is rejected, and JSON round-trips are
lossless.
"""

from datetime import date

import pytest
from pydantic import ValidationError

from backend.app.schemas.recommendation import Action, Recommendation


def _good_payload() -> dict:
    return {
        "ticker": "SPY",
        "as_of": date(2024, 1, 15),
        "action": Action.BUY,
        "confidence": 0.72,
        "horizon_days": 10,
        "rationale": "momentum and regime aligned bullish",
        "evidence": ["momentum.RSI=55.0000", "price.current=100.0000"],
        "source": "quant",
        "model_id": None,
        "prompt_version": None,
        "snapshot_id": "0123456789abcdef",
    }


def test_recommendation_validates_good_payload():
    rec = Recommendation(**_good_payload())
    assert rec.ticker == "SPY"
    assert rec.action is Action.BUY
    assert rec.source == "quant"
    assert rec.model_id is None


def test_recommendation_rejects_confidence_above_one():
    payload = _good_payload()
    payload["confidence"] = 1.5
    with pytest.raises(ValidationError):
        Recommendation(**payload)


def test_recommendation_json_round_trip_is_equal():
    rec = Recommendation(**_good_payload())
    dumped = rec.model_dump_json()
    restored = Recommendation.model_validate_json(dumped)
    assert restored == rec
