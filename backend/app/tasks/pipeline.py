"""Celery task: the G5 "unattended day" nightly pipeline orchestrator (Step 5).

WHAT THIS IS
------------
A single ``@shared_task`` — ``run_nightly_pipeline`` — that chains the nightly
stages into an explicit SEQUENCE and records a COMPLETION SUMMARY so we can tell
whether the pipeline finished:

    stage 0: data sync -> stage 1: quant -> stage 2: deep -> stage 3: alerts
    -> stage 4: paper (mark-to-market the quant + deep paper books)

Before this, ``celery_app`` scheduled the quant / deep / alert tasks by
INDEPENDENT timers (16:30 / 01:00 / 07:00); nothing guaranteed deep waited for
quant, or alerts for deep. This task makes the order explicit and auditable.

WHY APPROACH (A), CALL THE STAGE FUNCTIONS IN-PROCESS
-----------------------------------------------------
Two shapes were possible: (A) one task that calls the three existing stage
FUNCTIONS synchronously in order, or (B) a Celery ``chain(...)`` of the three
tasks. We chose (A) because:
  * the SEQUENCE is guaranteed — the next stage simply can't start until the
    previous call returns (a ``chain`` across queues is ordered too, but it is
    harder to unit-test and spreads the per-stage DB recording across tasks);
  * the per-stage PipelineRun recording is clean and inline — one function sees
    every stage's summary and writes the audit record as it goes;
  * it is trivially testable offline — monkeypatch the three stage functions and
    assert the order + the recorded summaries, no broker, no worker, no DB.

Because this task EMBEDS the slow deep stage, it is ROUTED TO THE ``deep`` QUEUE
(``task_routes`` in ``celery_app``), whose worker runs at concurrency 1. So the
deep lane keeps its single-worker guarantee even though it now runs inside the
orchestrator (STEERING rule 6: the deep lane is never in a web request — it is a
queued Celery job; that still holds).

We call the stage logic via the task objects' ``.run`` method
(``generate_nightly_signals.run()``) — for a ``bind=True`` task, ``.run`` is the
bound body, so this executes the SAME code the independent beat entry would, in
this process, synchronously (NOT ``.delay`` — we need the sequence, not a
dispatch). The stage objects are module-level names so a test can monkeypatch
them.

FAILURE POLICY: CONTINUE-BUT-MARK-FAILED
----------------------------------------
Each stage is wrapped in try/except. A stage that raises is recorded in the
PipelineRun ``stages`` map as ``{"error": "..."}`` and flips the run's final
status to ``"failed"`` — but LATER STAGES STILL RUN. Rationale: a deep-lane
failure must not cost us the alerts pass (alerts read whatever quant/prior runs
stored). So one bad stage degrades the night; it does not abort it.

STAGE 0 "DATA SYNC"
-------------------
The quant and deep lanes fetch their own data through the data service, so there
is no heavy per-run sync to do here. Stage 0 is a logged no-op MARKER recorded as
``{"note": "lanes fetch their own data"}`` — it keeps the sequence honest (the
record shows data-sync ran first) without inventing data-sync logic.

HONEST SCOPING NOTE (also in docs/status.md)
--------------------------------------------
The SEQUENCING and the completion RECORD are fully built and tested here. The
Gate-G5 "finishes before 09:00 IST" is a TIMED guarantee that depends on real
deep-run speed — provable only on capable hardware (the same blocker as the G4
acceptance). On this machine we verify the chain runs IN ORDER and records a
completion summary; the wall-clock deadline is a TK-hardware check.
"""

import logging
from datetime import date

from celery import shared_task

# Stage task objects (module-level so tests can monkeypatch them). We invoke
# their ``.run`` bodies synchronously, in order — never ``.delay``.
from backend.app.repositories.pipeline_run_repository import SyncPipelineRunRepository
from backend.app.tasks.deep_recommender import generate_deep_signals
from backend.app.tasks.paper_trader import mark_paper_books
from backend.app.tasks.quant_recommender import generate_nightly_signals
from backend.app.tasks.signal_alerter import generate_signal_alerts

logger = logging.getLogger(__name__)


def _run_stage(repo: SyncPipelineRunRepository, run_date: date, name: str, fn) -> bool:
    """Run one stage, record its summary, and report whether it SUCCEEDED.

    ``fn`` is a zero-arg callable returning a summary dict. On success the
    summary is recorded under ``name`` and ``True`` is returned. On failure the
    exception is logged, recorded as ``{"error": str(exc)}`` under ``name``, and
    ``False`` is returned — the caller keeps going (continue-but-mark-failed).
    """
    logger.info("pipeline stage '%s' starting (run_date=%s)", name, run_date.isoformat())
    try:
        summary = fn()
        if not isinstance(summary, dict):
            summary = {"result": summary}
        repo.record_stage(run_date, name, summary)
        logger.info("pipeline stage '%s' done: %s", name, summary)
        return True
    except Exception as exc:  # noqa: BLE001 — isolate a stage; later stages still run
        logger.exception("pipeline stage '%s' failed", name)
        repo.record_stage(run_date, name, {"error": str(exc)})
        return False


@shared_task(
    bind=True,
    name="app.tasks.pipeline.run_nightly_pipeline",
)
def run_nightly_pipeline(self) -> dict:
    """Run the nightly stages IN ORDER and record an auditable completion summary.

    Order: data sync (no-op marker) -> quant -> deep -> alerts. Each stage is
    recorded into the day's ``PipelineRun`` as it completes; a stage failure is
    captured and flips the final status to ``"failed"`` while later stages still
    run. Returns a summary dict ``{run_date, status, stages_run, failed_stages}``.
    """
    run_date = date.today()
    repo = SyncPipelineRunRepository()
    logger.info("nightly pipeline starting (run_date=%s)", run_date.isoformat())

    repo.start(run_date)

    failed: list[str] = []
    stages_run: list[str] = []

    # Stage 0: data sync — a logged no-op marker (the lanes fetch their own data).
    stages_run.append("data")
    if not _run_stage(
        repo,
        run_date,
        "data",
        lambda: {"note": "lanes fetch their own data"},
    ):
        failed.append("data")

    # Stage 1: quant fast-lane.
    stages_run.append("quant")
    if not _run_stage(repo, run_date, "quant", generate_nightly_signals.run):
        failed.append("quant")

    # Stage 2: deep lane (slow; this task is routed to the `deep` queue, c=1).
    stages_run.append("deep")
    if not _run_stage(repo, run_date, "deep", generate_deep_signals.run):
        failed.append("deep")

    # Stage 3: system-generated alerts (reads stored pipeline output).
    stages_run.append("alerts")
    if not _run_stage(repo, run_date, "alerts", generate_signal_alerts.run):
        failed.append("alerts")

    # Stage 4: paper-trading mark-to-market (opens/marks/closes the quant+deep
    # paper books from the recommendations the earlier stages just stored).
    stages_run.append("paper")
    if not _run_stage(repo, run_date, "paper", mark_paper_books.run):
        failed.append("paper")

    status = "failed" if failed else "completed"
    repo.finish(run_date, status)

    summary = {
        "run_date": run_date.isoformat(),
        "status": status,
        "stages_run": stages_run,
        "failed_stages": failed,
    }
    logger.info("nightly pipeline complete: %s", summary)
    return summary
