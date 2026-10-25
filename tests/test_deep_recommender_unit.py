"""Offline unit tests for the deep persistence service + Celery task (Step 4 / G4).

No network / no Ollama / no real graph / no live Redis / no DB. The three
collaborators of ``persist_deep_recommendation`` (snapshot build, deep adapter
``analyze``, repo ``save``) are monkeypatched to record call order. The Celery
task body is driven with a monkeypatched universe, a monkeypatched
``persist_deep_recommendation`` (returning canned recs / raising for one
ticker), and a monkeypatched progress publisher that captures every event — so
we can assert summary counts, failure isolation, the published event sequence,
and the ``limit`` cap, all offline.

Also asserts (statically) that the deep task module routes to the ``deep``
queue via ``celery_app.conf.task_routes``.
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.app.schemas.recommendation import Action, Recommendation

AS_OF = date(2024, 1, 15)


def _canned_rec(ticker: str, action: Action = Action.BUY) -> Recommendation:
    return Recommendation(
        ticker=ticker,
        as_of=AS_OF,
        action=action,
        confidence=0.8,
        horizon_days=10,
        rationale="deep says so",
        evidence=["market_report figure 100.0"],
        source="deep",
        model_id="llama3.2",
        prompt_version="deep-v2",
        snapshot_id="deadbeefdeadbeef",
    )


# --------------------------------------------------------------------------- #
# persist_deep_recommendation: ordering + returns the analyze result           #
# --------------------------------------------------------------------------- #


def test_persist_deep_recommendation_order_and_return(monkeypatch):
    from backend.app.services import deep_service

    calls: list[str] = []
    rec = _canned_rec("RELIANCE.NS")

    def fake_snapshot(ticker, as_of, *a, **k):
        calls.append("snapshot")
        return ("deadbeefdeadbeef", {})

    def fake_analyze(ticker, as_of, portfolio=None):
        calls.append("analyze")
        return rec

    class FakeRepo:
        def save(self, r):
            calls.append("save")
            assert r is rec

    monkeypatch.setattr(deep_service, "build_and_store_snapshot", fake_snapshot)
    monkeypatch.setattr(deep_service, "analyze", fake_analyze)
    monkeypatch.setattr(deep_service, "SyncRecommendationRepository", lambda: FakeRepo())

    result = deep_service.persist_deep_recommendation("RELIANCE.NS", AS_OF)

    # snapshot MUST be built before analyze (FK safety), save last.
    assert calls == ["snapshot", "analyze", "save"]
    assert result is rec


def test_persist_deep_recommendation_propagates_errors(monkeypatch):
    """A NumberCheckError from analyze is NOT swallowed by the service."""
    from backend.app.services import deep_service
    from deep.adapter import NumberCheckError

    monkeypatch.setattr(deep_service, "build_and_store_snapshot", lambda *a, **k: ("x", {}))

    def boom(ticker, as_of, portfolio=None):
        raise NumberCheckError(offending=[999.0])

    monkeypatch.setattr(deep_service, "analyze", boom)
    monkeypatch.setattr(
        deep_service, "SyncRecommendationRepository", lambda: pytest.fail("save must not run")
    )

    with pytest.raises(NumberCheckError):
        deep_service.persist_deep_recommendation("RELIANCE.NS", AS_OF)


# --------------------------------------------------------------------------- #
# generate_deep_signals task body                                              #
# --------------------------------------------------------------------------- #


def _run_task(monkeypatch, universe, persist_fn, limit=None):
    """Drive the task body with monkeypatched universe + persist + publisher.

    Returns ``(summary, events)``.
    """
    from backend.app.tasks import deep_recommender

    monkeypatch.setattr(deep_recommender, "_nightly_universe", lambda session: list(universe))
    # Avoid opening a real sync session for the universe query.
    monkeypatch.setattr(
        deep_recommender, "create_sync_session", lambda: _NullSession()
    )
    monkeypatch.setattr(deep_recommender, "persist_deep_recommendation", persist_fn)

    events: list[dict] = []
    monkeypatch.setattr(deep_recommender, "_publish_progress", lambda e: events.append(e))

    # Call the underlying function directly (bypass Celery .delay); self=None.
    summary = deep_recommender.generate_deep_signals.run(limit=limit)
    return summary, events


class _NullSession:
    def close(self):
        pass


def test_task_processes_universe_and_isolates_failure(monkeypatch):
    from deep.adapter import DeepValidationError

    universe = ["AAA.NS", "BBB.NS", "CCC.NS"]

    def persist(ticker, as_of):
        if ticker == "BBB.NS":
            raise DeepValidationError("graph blew up")
        return _canned_rec(ticker, Action.BUY if ticker == "AAA.NS" else Action.HOLD)

    summary, events = _run_task(monkeypatch, universe, persist)

    # One failure in the middle does NOT abort the run.
    assert summary == {"processed": 2, "skipped": 0, "errors": 1, "total": 3}

    types = [e["type"] for e in events]
    assert types[0] == "run_started"
    assert types[-1] == "run_done"
    # Each ticker got a started event; the failing one an error, others done.
    assert types.count("ticker_started") == 3
    assert types.count("ticker_done") == 2
    assert types.count("ticker_error") == 1

    # Event ordering: started then done/error per ticker, in universe order.
    middle = [e for e in events if e["type"] != "run_started" and e["type"] != "run_done"]
    assert middle[0] == {**middle[0], "type": "ticker_started", "ticker": "AAA.NS", "index": 0}
    assert middle[1]["type"] == "ticker_done" and middle[1]["ticker"] == "AAA.NS"
    assert middle[1]["action"] == "BUY"
    assert middle[2]["type"] == "ticker_started" and middle[2]["ticker"] == "BBB.NS"
    assert middle[3]["type"] == "ticker_error" and middle[3]["ticker"] == "BBB.NS"
    assert "graph blew up" in middle[3]["error"]
    assert middle[5]["type"] == "ticker_done" and middle[5]["ticker"] == "CCC.NS"

    # run_started/run_done carry the total/summary.
    assert events[0]["total"] == 3
    assert events[-1]["processed"] == 2 and events[-1]["errors"] == 1

    # Every event carries a timestamp.
    assert all("ts" in e for e in events)


def test_task_number_check_error_counts_as_error(monkeypatch):
    from deep.adapter import NumberCheckError

    def persist(ticker, as_of):
        raise NumberCheckError(offending=[123.0])

    summary, events = _run_task(monkeypatch, ["AAA.NS"], persist)
    assert summary == {"processed": 0, "skipped": 0, "errors": 1, "total": 1}
    assert [e["type"] for e in events] == [
        "run_started",
        "ticker_started",
        "ticker_error",
        "run_done",
    ]


def test_task_limit_caps_universe(monkeypatch):
    universe = ["AAA.NS", "BBB.NS", "CCC.NS", "DDD.NS", "EEE.NS"]

    processed_tickers: list[str] = []

    def persist(ticker, as_of):
        processed_tickers.append(ticker)
        return _canned_rec(ticker)

    summary, events = _run_task(monkeypatch, universe, persist, limit=2)

    assert summary["total"] == 2
    assert summary["processed"] == 2
    assert processed_tickers == ["AAA.NS", "BBB.NS"]
    assert events[0]["total"] == 2


# --------------------------------------------------------------------------- #
# routing: the deep task maps to the `deep` queue                              #
# --------------------------------------------------------------------------- #


def test_deep_task_routes_to_deep_queue():
    from backend.app.celery_app import celery_app

    routes = celery_app.conf.task_routes
    assert routes["app.tasks.deep_recommender.*"] == {"queue": "deep"}
    assert "app.tasks.deep_recommender" in celery_app.conf.imports


def test_deep_beat_entry_is_overnight():
    from backend.app.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["generate-deep-signals"]
    assert entry["task"] == "app.tasks.deep_recommender.generate_deep_signals"
    assert entry["options"] == {"queue": "deep"}
