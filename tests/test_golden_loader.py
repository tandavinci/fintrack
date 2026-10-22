"""Offline golden-fixture loader + offline G2-style equality (design §9.3).

FAST, NO infra, NO network. Loads each committed ``tests/golden/<TICKER>_1d.parquet``
fixture and asserts row counts + date ranges (satisfies Gate G0 "golden data
loads in a test"), then feeds the loaded raw frame as the ``df=`` argument to
``quant.recommend`` and as the patched ``DataPipeline`` return for
``deep.analyze`` so an offline G2-style equality (equal ``snapshot_id`` across
lanes) runs WITHOUT network.

The parquet fixtures are MATERIALIZED by the integration run
(``test_golden_materialize.py``) which needs the live stack + yfinance. Until a
materialization run has committed them, every test here SKIPS-with-reason so the
offline suite stays green on a bare machine.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from backend.app.schemas.snapshot import compute_snapshot_id
from deep import adapter
from deep.adapter import analyze
from quant.data.indicators import TechnicalIndicators
from quant.recommend import recommend

GOLDEN_DIR = Path(__file__).parent / "golden"
MIN_GOLDEN_ROWS = 60
AS_OF = date(2024, 6, 3)  # offline anchor; the fixtures are point-in-time frames

_OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _golden_files() -> list[Path]:
    return sorted(GOLDEN_DIR.glob("*_1d.parquet"))


def _require_fixtures() -> list[Path]:
    files = _golden_files()
    if not files:
        pytest.skip(
            "no tests/golden/*_1d.parquet fixtures yet - materialize them with "
            "the stack up via `pytest -m integration tests/test_golden_materialize.py`"
        )
    return files


def _enriched(raw: pd.DataFrame) -> pd.DataFrame:
    e = TechnicalIndicators.add_all(raw, interval="1d")
    return e.dropna(subset=["SMA_50", "RSI"])


def test_at_least_one_golden_fixture_or_skip():
    """Gate G0: golden data loads in a test (skips cleanly until materialized)."""
    files = _require_fixtures()
    assert files


def test_each_golden_loads_with_valid_shape_and_range():
    """Every saved parquet loads to a tz-naive float64 OHLCV frame with >=60 rows
    and a sane, strictly increasing date range."""
    for path in _require_fixtures():
        df = pd.read_parquet(path, engine="pyarrow")
        assert list(df.columns) == _OHLCV_COLUMNS, f"{path.name}: unexpected columns"
        assert len(df) >= MIN_GOLDEN_ROWS, f"{path.name}: only {len(df)} rows"
        assert isinstance(df.index, pd.DatetimeIndex), f"{path.name}: index not datetime"
        assert df.index.tz is None, f"{path.name}: index must be tz-naive"
        assert df.index.is_monotonic_increasing, f"{path.name}: dates not sorted"
        assert df.index.is_unique, f"{path.name}: duplicate timestamps"
        for col in _OHLCV_COLUMNS:
            assert str(df[col].dtype) == "float64", f"{path.name}.{col} not float64"


def test_golden_feeds_quant_and_deep_to_one_snapshot_id(monkeypatch):
    """Offline G2-style equality: for every golden fixture, the raw frame fed to
    ``quant.recommend(df=)`` and to the patched ``deep.analyze`` pipeline yields
    the SAME ``compute_snapshot_id`` across both lanes (no network)."""
    files = _require_fixtures()

    def _fake_generate_sync(prompt, **kwargs):
        # A schema-valid, number-clean deep Recommendation (actions/figures from
        # the snapshot). The number-check only inspects rationale/evidence, so a
        # figure-free rationale always passes.
        rec = {
            "ticker": "GOLDEN",
            "as_of": AS_OF.isoformat(),
            "action": "HOLD",
            "confidence": 0.5,
            "horizon_days": 10,
            "rationale": "Signals are mixed; hold.",
            "evidence": ["trend neutral"],
            "source": "deep",
            "snapshot_id": "placeholder",
        }
        return {"text": json.dumps(rec), "valid": True, "model_id": "llama3.2"}

    monkeypatch.setattr(adapter, "generate_sync", _fake_generate_sync)

    for path in files:
        raw = pd.read_parquet(path, engine="pyarrow")
        enriched = _enriched(raw)
        if len(enriched) < 1:
            pytest.skip(f"{path.name}: enriched frame empty after warmup dropna")

        # deep.analyze builds its own enriched frame from the pipeline; patch the
        # pipeline to return the SAME raw bars so both lanes see one snapshot.
        monkeypatch.setattr(
            "quant.data.pipeline.DataPipeline.get_enriched_data",
            lambda self, *a, **k: enriched,
        )

        quant_rec = recommend("GOLDEN", AS_OF, df=enriched)
        deep_rec = analyze("GOLDEN", AS_OF)

        expected = compute_snapshot_id("GOLDEN", AS_OF, enriched)
        assert quant_rec.snapshot_id == expected, f"{path.name}: quant id mismatch"
        assert deep_rec.snapshot_id == expected, f"{path.name}: deep id mismatch"
