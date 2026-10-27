"""Offline tests for the Model Status endpoint (Step 5 / G5 "Model Status").

The JSONL-summary computation is a PURE helper (:func:`summarize_calls`, a list
of parsed log dicts -> a summary) and is unit-tested here with no file/network:
counts by route, valid-rate, latency avg/max ignoring None, and empty-log ->
zeros. A small end-to-end test drives the route with a monkeypatched log read +
Ollama probe (no network) and asserts the response shape, including that a down
Ollama reports ``ollama_up: false`` instead of failing the endpoint.
"""

from __future__ import annotations

import anyio
import pytest

from backend.app.routers import model_status


# --------------------------------------------------------------------------- #
# Pure summary helper                                                          #
# --------------------------------------------------------------------------- #


def _rec(**kw):
    """A log record with sensible defaults; override any field via kwargs."""
    base = {
        "route": "quick",
        "model_id": "llama3.2",
        "latency_ms": 100,
        "valid": True,
    }
    base.update(kw)
    return base


def test_summarize_empty_log_is_all_zeros():
    s = model_status.summarize_calls([])
    assert s.total_calls == 0
    assert s.by_route == []
    assert s.valid_count == 0
    assert s.invalid_count == 0
    assert s.valid_rate is None
    assert s.avg_latency_ms is None
    assert s.max_latency_ms is None
    assert s.model_id is None


def test_summarize_counts_by_route():
    records = [
        _rec(route="quick"),
        _rec(route="quick"),
        _rec(route="deep"),
        _rec(route="embed"),
        _rec(route="chat"),
        _rec(route="mystery"),  # unknown -> "other"
    ]
    s = model_status.summarize_calls(records)
    assert s.total_calls == 6
    counts = {rc.route: rc.count for rc in s.by_route}
    assert counts == {"quick": 2, "deep": 1, "embed": 1, "chat": 1, "other": 1}
    # Known routes come first in declared order, extras (other) last.
    assert [rc.route for rc in s.by_route] == ["quick", "deep", "embed", "chat", "other"]


def test_summarize_valid_rate_over_classified_only():
    records = [
        _rec(valid=True),
        _rec(valid=True),
        _rec(valid=False),
        _rec(valid=None),  # unclassified -> ignored in the rate
    ]
    s = model_status.summarize_calls(records)
    assert s.valid_count == 2
    assert s.invalid_count == 1
    assert s.valid_rate == pytest.approx(2 / 3, rel=1e-3)


def test_summarize_latency_avg_max_ignore_none():
    records = [
        _rec(latency_ms=100),
        _rec(latency_ms=None),  # ignored
        _rec(latency_ms=300),
        _rec(latency_ms=200),
    ]
    s = model_status.summarize_calls(records)
    # avg over {100, 300, 200} = 200; max = 300
    assert s.avg_latency_ms == pytest.approx(200.0)
    assert s.max_latency_ms == 300


def test_summarize_latency_all_none_is_none():
    records = [_rec(latency_ms=None), _rec(latency_ms=None)]
    s = model_status.summarize_calls(records)
    assert s.avg_latency_ms is None
    assert s.max_latency_ms is None


def test_summarize_model_id_is_most_recent_non_null():
    records = [
        _rec(model_id="old-model"),
        _rec(model_id=None),  # skipped
        _rec(model_id="new-model"),
    ]
    s = model_status.summarize_calls(records)
    assert s.model_id == "new-model"


def test_summarize_bool_latency_is_ignored():
    # A bool is an int subclass; it must NOT be counted as a latency.
    records = [_rec(latency_ms=True), _rec(latency_ms=150)]
    s = model_status.summarize_calls(records)
    assert s.avg_latency_ms == pytest.approx(150.0)
    assert s.max_latency_ms == 150


# --------------------------------------------------------------------------- #
# Log tail reader (offline, tmp file)                                          #
# --------------------------------------------------------------------------- #


def test_read_log_tail_missing_file_is_empty(tmp_path):
    missing = str(tmp_path / "does_not_exist.jsonl")
    assert model_status._read_log_tail(missing) == []


def test_read_log_tail_parses_and_skips_bad_lines(tmp_path):
    path = tmp_path / "calls.jsonl"
    path.write_text(
        '{"route": "quick", "valid": true}\n'
        "not json at all\n"
        "\n"  # blank line skipped
        '{"route": "deep", "valid": false}\n',
        encoding="utf-8",
    )
    records = model_status._read_log_tail(str(path))
    assert len(records) == 2
    assert records[0]["route"] == "quick"
    assert records[1]["route"] == "deep"


def test_read_log_tail_respects_n(tmp_path):
    path = tmp_path / "calls.jsonl"
    path.write_text(
        "".join(f'{{"route": "quick", "i": {i}}}\n' for i in range(10)),
        encoding="utf-8",
    )
    records = model_status._read_log_tail(str(path), n=3)
    assert [r["i"] for r in records] == [7, 8, 9]  # the tail, in order


# --------------------------------------------------------------------------- #
# Endpoint (monkeypatched probe + log read; no network)                       #
# --------------------------------------------------------------------------- #


def test_endpoint_reports_ollama_down_without_failing(monkeypatch):
    # No real network: the probe is monkeypatched to "down".
    monkeypatch.setattr(model_status, "_probe_ollama_sync", lambda base_url: False)
    monkeypatch.setattr(
        model_status,
        "_read_log_tail",
        lambda log_path, n=200: [_rec(route="quick"), _rec(route="deep", valid=False)],
    )

    resp = anyio.run(model_status.get_model_status)

    assert resp.ollama_up is False  # down, but the endpoint still succeeded
    assert resp.num_ctx_cap == 8192
    assert resp.configured_model  # whatever the gateway settings say
    assert resp.summary.total_calls == 2
    assert resp.summary.valid_count == 1
    assert resp.summary.invalid_count == 1


def test_endpoint_reports_ollama_up(monkeypatch):
    monkeypatch.setattr(model_status, "_probe_ollama_sync", lambda base_url: True)
    monkeypatch.setattr(model_status, "_read_log_tail", lambda log_path, n=200: [])

    resp = anyio.run(model_status.get_model_status)

    assert resp.ollama_up is True
    assert resp.summary.total_calls == 0


def test_endpoint_registered():
    from backend.app.main import app

    assert "/api/model/status" in [r.path for r in app.routes]
