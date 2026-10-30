"""OFFLINE unit tests for the G5 nightly-pipeline orchestrator (Step 5).

These exercise ``app.tasks.pipeline.run_nightly_pipeline`` with NO broker, NO
worker, NO database and NO real stage logic:

  * the three stage tasks are monkeypatched to fakes whose ``.run`` appends to a
    shared order log and returns a canned summary (one variant RAISES);
  * the ``SyncPipelineRunRepository`` is monkeypatched to a fake that captures
    every ``start`` / ``record_stage`` / ``finish`` call.

We then assert:
  * the stages run IN ORDER — data -> quant -> deep -> alerts;
  * each stage's summary is recorded into the run;
  * a RAISING stage is captured (recorded as an ``error``) while LATER stages
    STILL RUN, and the final status is ``"failed"`` (continue-but-mark-failed);
  * the happy path records every summary and marks ``"completed"``.

No DB, no network: part of the fast offline suite.
"""

from __future__ import annotations

import pytest

from backend.app.tasks import pipeline as pipeline_mod


class _FakeTask:
    """Stand-in for a bound Celery stage task.

    ``.run`` is what the orchestrator calls (the bound task body). It logs the
    stage name into a shared order list and either returns a canned summary or
    raises the configured exception.
    """

    def __init__(self, name: str, order_log: list[str], summary=None, exc=None):
        self._name = name
        self._order_log = order_log
        self._summary = summary
        self._exc = exc

    def run(self):
        self._order_log.append(self._name)
        if self._exc is not None:
            raise self._exc
        return self._summary


class _FakeRepo:
    """Captures orchestrator audit writes in memory (no DB)."""

    def __init__(self):
        self.started: list = []
        self.recorded: list[tuple[str, dict]] = []
        self.finished: list[tuple] = []

    def start(self, run_date):
        self.started.append(run_date)

    def record_stage(self, run_date, name, summary):
        self.recorded.append((name, summary))

    def finish(self, run_date, status):
        self.finished.append((run_date, status))


@pytest.fixture
def order_log() -> list[str]:
    return []


@pytest.fixture
def fake_repo(monkeypatch) -> _FakeRepo:
    """Patch the orchestrator's repo class to return our capturing fake."""
    repo = _FakeRepo()
    monkeypatch.setattr(
        pipeline_mod, "SyncPipelineRunRepository", lambda *a, **k: repo
    )
    return repo


def _patch_stages(monkeypatch, order_log, *, quant, deep, alerts, paper=None):
    """Monkeypatch the stage task objects on the pipeline module.

    ``paper`` defaults to a successful no-op summary so the tests that only care
    about the quant/deep/alerts behaviour don't have to spell it out; it is
    patched regardless so the real (DB-touching) ``mark_paper_books`` never runs.
    """
    paper = paper or {"summary": {"opened": 0, "marked": 0, "closed": 0}}
    monkeypatch.setattr(
        pipeline_mod, "generate_nightly_signals",
        _FakeTask("quant", order_log, summary=quant.get("summary"), exc=quant.get("exc")),
    )
    monkeypatch.setattr(
        pipeline_mod, "generate_deep_signals",
        _FakeTask("deep", order_log, summary=deep.get("summary"), exc=deep.get("exc")),
    )
    monkeypatch.setattr(
        pipeline_mod, "generate_signal_alerts",
        _FakeTask("alerts", order_log, summary=alerts.get("summary"), exc=alerts.get("exc")),
    )
    monkeypatch.setattr(
        pipeline_mod, "mark_paper_books",
        _FakeTask("paper", order_log, summary=paper.get("summary"), exc=paper.get("exc")),
    )


# --- happy path --------------------------------------------------------------


def test_happy_path_runs_in_order_records_all_and_completes(
    monkeypatch, order_log, fake_repo
):
    _patch_stages(
        monkeypatch,
        order_log,
        quant={"summary": {"processed": 12, "errors": 0}},
        deep={"summary": {"processed": 5, "errors": 0, "total": 5}},
        alerts={"summary": {"ai": 7, "risk": 0, "news": 0, "inserted": 7}},
    )

    result = pipeline_mod.run_nightly_pipeline.run()

    # Stages ran IN ORDER (the fakes logged themselves as they ran).
    assert order_log == ["quant", "deep", "alerts", "paper"]

    # start() called once, finish() marked "completed".
    assert len(fake_repo.started) == 1
    assert fake_repo.finished == [(fake_repo.started[0], "completed")]

    # Every stage summary recorded, including the stage-0 data marker, in order.
    recorded_names = [name for name, _ in fake_repo.recorded]
    assert recorded_names == ["data", "quant", "deep", "alerts", "paper"]
    recorded = dict(fake_repo.recorded)
    assert recorded["data"] == {"note": "lanes fetch their own data"}
    assert recorded["quant"] == {"processed": 12, "errors": 0}
    assert recorded["deep"] == {"processed": 5, "errors": 0, "total": 5}
    assert recorded["alerts"] == {"ai": 7, "risk": 0, "news": 0, "inserted": 7}
    assert recorded["paper"] == {"opened": 0, "marked": 0, "closed": 0}

    # Returned summary.
    assert result["status"] == "completed"
    assert result["stages_run"] == ["data", "quant", "deep", "alerts", "paper"]
    assert result["failed_stages"] == []


# --- continue-but-mark-failed ------------------------------------------------


def test_raising_deep_stage_is_captured_later_stages_still_run_status_failed(
    monkeypatch, order_log, fake_repo
):
    """A deep-stage failure is recorded, alerts STILL run, final status 'failed'."""
    _patch_stages(
        monkeypatch,
        order_log,
        quant={"summary": {"processed": 3, "errors": 0}},
        deep={"exc": RuntimeError("deep blew up")},
        alerts={"summary": {"ai": 2, "inserted": 2}},
    )

    result = pipeline_mod.run_nightly_pipeline.run()

    # Order preserved AND the later stages STILL ran despite deep failing.
    assert order_log == ["quant", "deep", "alerts", "paper"]

    recorded = dict(fake_repo.recorded)
    # The failing stage is captured as an error entry...
    assert recorded["deep"] == {"error": "deep blew up"}
    # ...while the stages around it recorded their real summaries.
    assert recorded["quant"] == {"processed": 3, "errors": 0}
    assert recorded["alerts"] == {"ai": 2, "inserted": 2}

    # Final status is "failed" (continue-but-mark-failed), but later stages ran.
    assert fake_repo.finished == [(fake_repo.started[0], "failed")]
    assert result["status"] == "failed"
    assert result["failed_stages"] == ["deep"]
    assert result["stages_run"] == ["data", "quant", "deep", "alerts", "paper"]


def test_all_stages_run_even_if_quant_fails_first(monkeypatch, order_log, fake_repo):
    """Even an EARLY (quant) failure doesn't abort deep/alerts; status 'failed'."""
    _patch_stages(
        monkeypatch,
        order_log,
        quant={"exc": ValueError("quant boom")},
        deep={"summary": {"processed": 1, "total": 1}},
        alerts={"summary": {"inserted": 0}},
    )

    result = pipeline_mod.run_nightly_pipeline.run()

    assert order_log == ["quant", "deep", "alerts", "paper"]
    recorded = dict(fake_repo.recorded)
    assert recorded["quant"] == {"error": "quant boom"}
    assert recorded["deep"] == {"processed": 1, "total": 1}
    assert result["status"] == "failed"
    assert result["failed_stages"] == ["quant"]


# --- beat wiring (offline) ---------------------------------------------------


class TestBeatWiring:
    """Pin the G5 beat consolidation so the intent can't silently regress."""

    def test_pipeline_entry_scheduled_and_routed_to_deep(self):
        from backend.app.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        assert "run-nightly-pipeline" in schedule
        entry = schedule["run-nightly-pipeline"]
        assert entry["task"] == "app.tasks.pipeline.run_nightly_pipeline"
        # The orchestrator embeds the slow deep stage -> runs on the deep queue.
        assert entry["options"]["queue"] == "deep"

    def test_old_independent_nightly_entries_consolidated(self):
        """The three independent nightly BEAT entries are gone (no double-run).

        The underlying TASKS still exist and are still routed (ad-hoc use), but
        their standalone beat entries were consolidated into run-nightly-pipeline.
        """
        from backend.app.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        assert "generate-nightly-signals" not in schedule
        assert "generate-deep-signals" not in schedule
        assert "generate-signal-alerts" not in schedule

    def test_pipeline_task_route_and_import_present(self):
        from backend.app.celery_app import celery_app

        assert celery_app.conf.task_routes.get("app.tasks.pipeline.*") == {
            "queue": "deep"
        }
        assert "app.tasks.pipeline" in celery_app.conf.imports
