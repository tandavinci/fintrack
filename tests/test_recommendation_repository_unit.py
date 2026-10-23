"""Offline unit tests for the recommendation repository mapping + upsert keys.

These assert, with NO database, that:
- ``_row_values`` maps the single Pydantic Recommendation contract to columns
  one-to-one, storing ``action`` as its plain string value and ``evidence`` as a
  plain list.
- ``_upsert_statement`` targets the (ticker, as_of, source) conflict keys and
  does NOT try to overwrite those identity columns on conflict.

The statement is a compiled SQLAlchemy Insert construct, so its ON CONFLICT
target + SET clause are inspectable offline (no Postgres connection needed).
"""

from __future__ import annotations

from datetime import date

from backend.app.repositories.recommendation_repository import (
    _row_values,
    _upsert_statement,
)
from backend.app.schemas.recommendation import Action, Recommendation


def _sample_rec() -> Recommendation:
    return Recommendation(
        ticker="RELIANCE.NS",
        as_of=date(2024, 1, 15),
        action=Action.BUY,
        confidence=0.73,
        horizon_days=10,
        rationale="trend up, RSI healthy",
        evidence=["price.current=100.0000", "momentum.RSI=55.0000"],
        source="quant",
        model_id=None,
        prompt_version=None,
        snapshot_id="abc123def4567890",
    )


def test_row_values_maps_contract_to_columns():
    rec = _sample_rec()
    values = _row_values(rec)

    assert values["ticker"] == "RELIANCE.NS"
    assert values["as_of"] == date(2024, 1, 15)
    # action stored as the plain string value, not the Enum.
    assert values["action"] == "BUY"
    assert values["confidence"] == 0.73
    assert values["horizon_days"] == 10
    assert values["rationale"] == "trend up, RSI healthy"
    assert values["evidence"] == ["price.current=100.0000", "momentum.RSI=55.0000"]
    assert isinstance(values["evidence"], list)
    assert values["source"] == "quant"
    assert values["model_id"] is None
    assert values["prompt_version"] is None
    assert values["snapshot_id"] == "abc123def4567890"


def test_row_values_action_rendered_from_enum_value():
    for action, expected in [(Action.BUY, "BUY"), (Action.HOLD, "HOLD"), (Action.SELL, "SELL")]:
        rec = _sample_rec().model_copy(update={"action": action})
        assert _row_values(rec)["action"] == expected


def test_upsert_targets_ticker_asof_source_conflict_keys():
    stmt = _upsert_statement(_sample_rec())

    # The ON CONFLICT clause must key on the unique constraint's columns
    # (index_elements inference).
    on_conflict = stmt._post_values_clause
    assert list(on_conflict.inferred_target_elements) == ["ticker", "as_of", "source"]


def test_upsert_does_not_overwrite_identity_columns():
    stmt = _upsert_statement(_sample_rec())
    # update_values_to_set is a list of (column_name, value) tuples.
    update_cols = {name for name, _ in stmt._post_values_clause.update_values_to_set}

    # Identity columns are NEVER in the DO UPDATE SET clause.
    assert "ticker" not in update_cols
    assert "as_of" not in update_cols
    assert "source" not in update_cols

    # The mutable payload columns ARE refreshed on conflict.
    for col in ("action", "confidence", "horizon_days", "rationale", "evidence",
                "model_id", "prompt_version", "snapshot_id"):
        assert col in update_cols
