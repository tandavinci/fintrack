"""Celery task: overnight deep-lane signal generation (Step 4 / G4).

``generate_deep_signals`` runs overnight (queue ``deep``, concurrency 1 — the
deep lane is SLOW, minutes per ticker, and must never be parallelized or run in
a web request; STEERING rule 6) over the SAME nightly universe as the quant
fast-lane (``_nightly_universe`` — distinct Holdings + WatchlistItem tickers)
and persists a deep ``Recommendation`` for each via the sync
``persist_deep_recommendation`` service.

Operational note: start the dedicated worker at concurrency 1, e.g.::

    celery -A backend.app.celery_app worker -Q deep --concurrency=1

Each ticker is wrapped in its own try/except so one failure (a provenance
mismatch raised as an error, a number-check failure, a dead symbol) never aborts
the whole overnight run; it is logged, counted, and published as a failure
event. The task returns a summary ``{processed, skipped, errors, total}``.

Progress is streamed by publishing JSON events to the Redis ``deep_progress``
channel using a SYNCHRONOUS redis client (the task runs sync, like
``quant_recommender`` / ``insight_calculator``). The SSE endpoint
(``routers/deep_progress.py``) subscribes to this channel and relays events to
the browser — it only READS; it never runs the deep lane. Publishing is wrapped
in try/except so a Redis hiccup never breaks the run.

Steering scope is "holdings + ~5 watchlist top picks"; for now the full universe
runs, but an optional ``limit`` task arg lets an operator cap a slow overnight
run.
"""

import json
import logging
from datetime import date, datetime, timezone

import redis as sync_redis
from celery import shared_task

from backend.app.config import settings
from backend.app.database import create_sync_session
from backend.app.services.deep_service import persist_deep_recommendation
from backend.app.tasks.quant_recommender import _nightly_universe
from deep.adapter import DeepValidationError, NumberCheckError

logger = logging.getLogger(__name__)

# Redis pub/sub channel the SSE endpoint subscribes to for deep progress.
DEEP_PROGRESS_CHANNEL = "deep_progress"


def _publish_progress(event: dict) -> None:
    """Publish one JSON progress event to the ``deep_progress`` Redis channel.

    Uses a SYNCHRONOUS redis client (the task is sync). Any Redis failure is
    swallowed — progress streaming is best-effort and must never abort the run.
    """
    try:
        client = sync_redis.from_url(settings.redis_url)
        try:
            client.publish(DEEP_PROGRESS_CHANNEL, json.dumps(event))
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001 — progress is best-effort
        logger.warning("failed to publish deep progress event: %s", exc)


def _event(event_type: str, **fields) -> dict:
    """Build a progress event carrying a type, a UTC timestamp, and extra fields."""
    event = {"type": event_type, "ts": datetime.now(timezone.utc).isoformat()}
    event.update(fields)
    return event


@shared_task(
    bind=True,
    name="app.tasks.deep_recommender.generate_deep_signals",
    max_retries=3,
    default_retry_delay=120,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
)
def generate_deep_signals(self, limit: int | None = None) -> dict:
    """Generate + persist deep recommendations for the nightly universe.

    For each distinct holdings/watchlist ticker, publish a ``ticker_started``
    event, call ``persist_deep_recommendation``, then publish a ``ticker_done``
    event (with the action). Per-ticker failures are isolated (logged, counted,
    published as ``ticker_error``) so the overnight run always completes.

    Args:
        limit: Optional cap on the number of tickers to process (so an operator
            can bound a slow overnight run — steering scope is holdings + a few
            watchlist picks). ``None`` runs the full universe.

    Returns:
        Summary dict ``{processed, skipped, errors, total}``.
    """
    as_of = date.today()
    logger.info("Starting overnight deep signal generation (as_of=%s)", as_of.isoformat())

    session = create_sync_session()
    try:
        universe = _nightly_universe(session)
    finally:
        session.close()

    if limit is not None:
        universe = universe[:limit]

    total = len(universe)
    logger.info("Deep universe: %d tickers (limit=%s)", total, limit)

    _publish_progress(_event("run_started", total=total, as_of=as_of.isoformat()))

    processed = 0
    skipped = 0
    errors = 0

    for index, ticker in enumerate(universe):
        _publish_progress(
            _event("ticker_started", ticker=ticker, index=index, total=total)
        )
        try:
            rec = persist_deep_recommendation(ticker, as_of=as_of)
            processed += 1
            _publish_progress(
                _event(
                    "ticker_done",
                    ticker=ticker,
                    index=index,
                    total=total,
                    action=rec.action.value,
                )
            )
        except (DeepValidationError, NumberCheckError) as exc:
            errors += 1
            logger.exception("deep recommendation validation failed for %s", ticker)
            _publish_progress(
                _event(
                    "ticker_error",
                    ticker=ticker,
                    index=index,
                    total=total,
                    error=str(exc),
                )
            )
        except Exception as exc:  # noqa: BLE001 — isolate any per-ticker failure
            errors += 1
            logger.exception("deep recommendation failed for %s", ticker)
            _publish_progress(
                _event(
                    "ticker_error",
                    ticker=ticker,
                    index=index,
                    total=total,
                    error=str(exc),
                )
            )

    summary = {"processed": processed, "skipped": skipped, "errors": errors, "total": total}
    _publish_progress(_event("run_done", **summary))
    logger.info("Overnight deep signal generation complete: %s", summary)
    return summary
