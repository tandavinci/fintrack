"""SSE endpoint streaming deep-lane progress (Step 4 / G4, Part C).

``GET /api/deep/progress`` is a READ-ONLY Server-Sent Events stream. It
subscribes to the Redis ``deep_progress`` channel (published to by the overnight
``app.tasks.deep_recommender.generate_deep_signals`` task) and relays each JSON
event to the browser as an SSE ``data:`` frame.

This endpoint NEVER runs the deep lane — it does not import ``deep.adapter`` and
never calls ``analyze``. That is the structural boundary enforcing STEERING
rule 6 (the deep lane is a queued Celery job, never inside a web request); a
structural test greps this source to prove the boundary holds.

The Redis pub/sub usage mirrors ``routers/websocket.py`` (async ``get_redis``
from ``backend.app.cache``). A periodic ``: keepalive`` comment line keeps idle
connections open; client disconnect (``asyncio.CancelledError``) is handled and
the pubsub is unsubscribed + closed in a ``finally``.
"""

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from backend.app.cache import get_redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/deep", tags=["deep"])

# The Redis pub/sub channel the deep Celery task publishes progress events to.
# Duplicated as a plain string here (not imported from the task module) so the
# web layer never imports the deep task/adapter chain.
DEEP_PROGRESS_CHANNEL = "deep_progress"

# How long to block waiting for a message before emitting a keepalive comment.
_POLL_TIMEOUT = 15.0


async def _event_stream():
    """Async generator yielding SSE frames from the ``deep_progress`` channel.

    Each Redis message is relayed verbatim as a ``data: <json>\\n\\n`` frame.
    When no message arrives within ``_POLL_TIMEOUT`` a ``: keepalive`` comment is
    emitted so idle connections (and proxies) stay open. The pubsub is always
    unsubscribed + closed on exit, including on client disconnect.
    """
    redis = await get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(DEEP_PROGRESS_CHANNEL)
    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=_POLL_TIMEOUT
            )
            if message and message.get("type") == "message":
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                yield f"data: {data}\n\n"
            else:
                # Idle period: send a keepalive comment to hold the connection.
                yield ": keepalive\n\n"
    except asyncio.CancelledError:
        # Client disconnected; let the finally clean up.
        raise
    finally:
        try:
            await pubsub.unsubscribe(DEEP_PROGRESS_CHANNEL)
            await pubsub.close()
        except Exception as exc:  # noqa: BLE001 — cleanup is best-effort
            logger.warning("deep progress pubsub cleanup failed: %s", exc)


@router.get("/progress")
async def deep_progress() -> StreamingResponse:
    """Stream deep-lane progress events as Server-Sent Events.

    Returns a ``text/event-stream`` response. The client receives one
    ``data: <json>`` frame per published progress event (``run_started``,
    ``ticker_started``, ``ticker_done``, ``ticker_error``, ``run_done``) plus
    periodic ``: keepalive`` comments.
    """
    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
