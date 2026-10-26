"""Celery task: nightly system-generated signal-alert pass (Step 5 / G5).

``generate_signal_alerts`` runs AFTER the overnight deep lane (see the beat
schedule in ``celery_app``) and turns the stored pipeline output into AI / RISK /
NEWS alerts via the sync ``generate_and_store`` service. It is light and fast, so
it routes to the DEFAULT queue (NOT the slow ``deep`` queue).

The decorator shape mirrors ``app.tasks.quant_recommender.generate_nightly_signals``.
The service already isolates per-source failures internally, so the task simply
delegates and returns its summary.
"""

import logging
from datetime import date

from celery import shared_task

from backend.app.services.signal_alert_service import generate_and_store

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="app.tasks.signal_alerter.generate_signal_alerts",
    max_retries=3,
    default_retry_delay=120,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
)
def generate_signal_alerts(self) -> dict:
    """Generate + upsert system alerts for today's pipeline output.

    Returns:
        Summary dict ``{ai, risk, news, inserted}`` from ``generate_and_store``.
    """
    as_of = date.today()
    logger.info("Starting signal-alert generation (as_of=%s)", as_of.isoformat())
    summary = generate_and_store(as_of=as_of)
    logger.info("Signal-alert generation complete: %s", summary)
    return summary
