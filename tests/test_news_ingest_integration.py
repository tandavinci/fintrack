"""News ingest dedup integration test (design §9.2 #4).

Serve a saved RSS fixture over ``http://localhost:<port>`` from a tiny local
HTTP server, ingest it into ``news_items``, count rows, re-run the ingest, and
assert the row count is UNCHANGED — the ``dedup_hash`` ``ON CONFLICT DO NOTHING``
makes a re-poll of the same items add no duplicate.

Requires Postgres (the real table). The RSS source is the LOCAL fixture server
(no external feed), so there is no network flakiness; if Postgres is down the
test skips-with-reason.
"""

from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from sqlalchemy import delete

from backend.app.database import create_sync_session
from backend.app.models.news_item import NewsItem
from backend.app.services.news_service import NewsService

from tests.integration_helpers import (  # noqa: F401
    count_news_items,
    require_postgres,
    run_async,
)

pytestmark = pytest.mark.integration

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def rss_server():
    """Serve tests/fixtures/ over http://localhost:<port>; yield the feed URL."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(FIXTURES_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/sample_feed.xml"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def clean_fixture_news():
    """Remove the fixture rows (by their source) before and after the test."""
    source = "FinTrack Test Market Feed"

    def _clean():
        session = create_sync_session()
        try:
            session.execute(delete(NewsItem).where(NewsItem.source == source))
            session.commit()
        finally:
            session.close()

    _clean()
    yield
    _clean()


def test_ingest_is_idempotent_on_rerun(require_postgres, rss_server, clean_fixture_news):
    """First ingest adds the fixture items; a second ingest adds zero (dedup)."""
    service = NewsService()

    first = run_async(service.ingest_rss(feeds=[rss_server]))
    assert first["added"] == 3, f"expected 3 new rows, got {first}"
    after_first = count_news_items()

    second = run_async(service.ingest_rss(feeds=[rss_server]))
    assert second["added"] == 0, f"re-run must add no duplicates, got {second}"
    after_second = count_news_items()

    assert after_second == after_first, "row count changed on a dedup re-run"
