"""WebSocket endpoint for real-time stock price updates.

Provides a `/ws/prices` WebSocket route that accepts stock_ids as query params.
Subscribes to Redis pub/sub channel `price_updates` and broadcasts price changes
to connected clients for their subscribed stocks.

Usage:
    ws://host/ws/prices?stock_ids=1,2,3
"""

import asyncio
import json
import logging
from typing import Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from backend.app.cache import get_redis

logger = logging.getLogger(__name__)

router = APIRouter()

# Redis pub/sub channel name for price updates
PRICE_UPDATES_CHANNEL = "price_updates"


class ConnectionManager:
    """Manages active WebSocket connections and their stock subscriptions."""

    def __init__(self):
        # Map of WebSocket -> set of subscribed stock IDs
        self.active_connections: dict[WebSocket, Set[int]] = {}

    async def connect(self, websocket: WebSocket, stock_ids: Set[int]) -> None:
        """Accept a WebSocket connection and register its subscriptions."""
        await websocket.accept()
        self.active_connections[websocket] = stock_ids
        logger.info(
            "WebSocket connected, subscribed to %d stocks", len(stock_ids)
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        self.active_connections.pop(websocket, None)
        logger.info("WebSocket disconnected")

    def update_subscriptions(self, websocket: WebSocket, stock_ids: Set[int]) -> None:
        """Update subscriptions for an existing connection."""
        if websocket in self.active_connections:
            self.active_connections[websocket] = stock_ids

    async def broadcast_price_update(self, stock_id: int, price_data: dict) -> None:
        """Send price update to all clients subscribed to the given stock."""
        disconnected = []
        for websocket, subscribed_ids in self.active_connections.items():
            if stock_id in subscribed_ids:
                try:
                    await websocket.send_json(
                        {
                            "type": "price_update",
                            "stock_id": stock_id,
                            "data": price_data,
                        }
                    )
                except Exception:
                    disconnected.append(websocket)

        # Clean up disconnected clients
        for ws in disconnected:
            self.disconnect(ws)


# Singleton connection manager
manager = ConnectionManager()


async def _listen_to_redis_pubsub() -> None:
    """Background task that listens to Redis pub/sub and broadcasts updates."""
    redis = await get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(PRICE_UPDATES_CHANNEL)

    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message and message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    stock_id = data.get("stock_id")
                    price_data = data.get("price_data")
                    if stock_id is not None and price_data is not None:
                        await manager.broadcast_price_update(stock_id, price_data)
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    logger.warning("Invalid pub/sub message: %s", exc)
            else:
                # Small sleep to avoid busy-waiting when no messages
                await asyncio.sleep(0.01)
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe(PRICE_UPDATES_CHANNEL)
        await pubsub.close()


# Background listener task reference
_listener_task: asyncio.Task | None = None


def _ensure_listener_running() -> None:
    """Ensure the Redis pub/sub listener background task is running."""
    global _listener_task
    if _listener_task is None or _listener_task.done():
        _listener_task = asyncio.create_task(_listen_to_redis_pubsub())


@router.websocket("/ws/prices")
async def websocket_prices(
    websocket: WebSocket,
    stock_ids: str = Query(default=""),
) -> None:
    """WebSocket endpoint for real-time price updates.

    Clients connect with a comma-separated list of stock IDs they want to
    subscribe to. The server pushes price updates whenever stocks are refreshed.

    Query Params:
        stock_ids: Comma-separated stock IDs, e.g. "1,2,3"

    Messages sent to client:
        {"type": "price_update", "stock_id": <int>, "data": {...}}

    Messages accepted from client:
        {"action": "subscribe", "stock_ids": [1, 2, 3]}
        {"action": "unsubscribe", "stock_ids": [1, 2]}
    """
    # Parse initial stock_ids from query params
    subscribed_ids: Set[int] = set()
    if stock_ids:
        try:
            subscribed_ids = {
                int(sid.strip())
                for sid in stock_ids.split(",")
                if sid.strip().isdigit()
            }
        except (ValueError, TypeError):
            pass

    # Accept connection and register
    await manager.connect(websocket, subscribed_ids)

    # Ensure Redis pub/sub listener is running
    _ensure_listener_running()

    try:
        while True:
            # Listen for client messages (subscription updates)
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                action = message.get("action")

                if action == "subscribe":
                    new_ids = set(message.get("stock_ids", []))
                    subscribed_ids.update(new_ids)
                    manager.update_subscriptions(websocket, subscribed_ids)
                    await websocket.send_json(
                        {
                            "type": "subscribed",
                            "stock_ids": sorted(subscribed_ids),
                        }
                    )

                elif action == "unsubscribe":
                    remove_ids = set(message.get("stock_ids", []))
                    subscribed_ids -= remove_ids
                    manager.update_subscriptions(websocket, subscribed_ids)
                    await websocket.send_json(
                        {
                            "type": "unsubscribed",
                            "stock_ids": sorted(subscribed_ids),
                        }
                    )

            except (json.JSONDecodeError, TypeError, KeyError):
                await websocket.send_json(
                    {"type": "error", "message": "Invalid message format"}
                )

    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def publish_price_update(stock_id: int, price_data: dict) -> None:
    """Publish a price update to Redis pub/sub channel.

    Called from the price updater task after a successful price update.

    Args:
        stock_id: The stock's primary key.
        price_data: Dict with current_price, day_change_pct, volume, last_updated.
    """
    try:
        redis = await get_redis()
        message = json.dumps(
            {
                "stock_id": stock_id,
                "price_data": price_data,
            }
        )
        await redis.publish(PRICE_UPDATES_CHANNEL, message)
    except Exception as exc:
        logger.warning("Failed to publish price update for stock %d: %s", stock_id, exc)
