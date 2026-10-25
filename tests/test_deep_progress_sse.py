"""Offline tests for the deep-progress SSE endpoint (Step 4 / G4, Part C).

No live Redis. ``get_redis`` is monkeypatched to return a fake client whose
pubsub yields a couple of canned ``deep_progress`` messages and then idles, so
we can assert the async generator formats each event as a ``data: <json>\\n\\n``
SSE frame and emits a keepalive comment when idle.

A structural test greps the router source to prove the web layer never imports
``deep.adapter`` / never references ``analyze`` — the deep lane is never run in a
web request (STEERING rule 6).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest

from backend.app.routers import deep_progress


# --------------------------------------------------------------------------- #
# Fake async Redis pubsub                                                      #
# --------------------------------------------------------------------------- #


class _FakePubSub:
    def __init__(self, messages):
        # Each entry is a dict (a real message) or None (idle tick).
        self._messages = list(messages)
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel):
        self.subscribed.append(channel)

    async def unsubscribe(self, channel):
        self.unsubscribed.append(channel)

    async def close(self):
        self.closed = True

    async def get_message(self, ignore_subscribe_messages=True, timeout=None):
        if self._messages:
            return self._messages.pop(0)
        # Nothing left: signal end-of-test by cancelling the consumer.
        raise asyncio.CancelledError()


class _FakeRedis:
    def __init__(self, pubsub):
        self._pubsub = pubsub

    def pubsub(self):
        return self._pubsub


def _install_fake_redis(monkeypatch, messages):
    pubsub = _FakePubSub(messages)

    async def fake_get_redis():
        return _FakeRedis(pubsub)

    monkeypatch.setattr(deep_progress, "get_redis", fake_get_redis)
    return pubsub


async def _drain(gen, max_frames=50):
    frames = []
    try:
        async for frame in gen:
            frames.append(frame)
            if len(frames) >= max_frames:
                break
    except asyncio.CancelledError:
        pass
    return frames


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #


def test_event_stream_formats_messages_as_sse_frames(monkeypatch):
    started = {"type": "message", "data": json.dumps({"type": "run_started", "total": 2})}
    done = {"type": "message", "data": json.dumps({"type": "ticker_done", "ticker": "AAA.NS", "action": "BUY"})}
    pubsub = _install_fake_redis(monkeypatch, [started, done])

    frames = asyncio.run(_drain(deep_progress._event_stream()))

    assert frames[0] == 'data: {"type": "run_started", "total": 2}\n\n'
    assert frames[1].startswith("data: ")
    assert '"ticker": "AAA.NS"' in frames[1]
    assert frames[1].endswith("\n\n")

    # pubsub subscribed to the channel and was cleaned up on exit.
    assert pubsub.subscribed == [deep_progress.DEEP_PROGRESS_CHANNEL]
    assert pubsub.unsubscribed == [deep_progress.DEEP_PROGRESS_CHANNEL]
    assert pubsub.closed is True


def test_event_stream_decodes_bytes_payload(monkeypatch):
    raw = {"type": "message", "data": b'{"type": "run_done", "processed": 1}'}
    _install_fake_redis(monkeypatch, [raw])

    frames = asyncio.run(_drain(deep_progress._event_stream()))
    assert frames[0] == 'data: {"type": "run_done", "processed": 1}\n\n'


def test_event_stream_emits_keepalive_on_idle(monkeypatch):
    # First an idle tick (None), then a real message.
    msg = {"type": "message", "data": json.dumps({"type": "run_done"})}
    _install_fake_redis(monkeypatch, [None, msg])

    frames = asyncio.run(_drain(deep_progress._event_stream()))
    assert frames[0] == ": keepalive\n\n"
    assert frames[1] == 'data: {"type": "run_done"}\n\n'


def test_endpoint_registered_as_text_event_stream():
    from backend.app.main import app

    paths = [r.path for r in app.routes]
    assert "/api/deep/progress" in paths


def test_router_source_never_imports_deep_lane():
    """The web layer must not import deep.adapter or reference analyze — the deep
    lane is never invoked from a web request (STEERING rule 6).

    Checked at the AST level so a prose mention in the module docstring (which
    legitimately explains the boundary) does not trip the structural assertion:
    we assert on real import statements and real ``analyze`` calls only.
    """
    import ast

    source = Path(inspect.getfile(deep_progress)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        # No `from deep.adapter import ...` / `from deep import adapter`.
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert "deep.adapter" not in mod
            if mod == "deep":
                assert all(alias.name != "adapter" for alias in node.names)
        # No `import deep.adapter`.
        if isinstance(node, ast.Import):
            assert all("deep.adapter" not in alias.name for alias in node.names)
        # No call to a bare `analyze(...)`.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "analyze"

    # And the module object never resolved the adapter into its namespace.
    assert not hasattr(deep_progress, "analyze")
    assert not hasattr(deep_progress, "adapter")
