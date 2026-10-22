"""News ingest service (design §6.1, §6.3, §6.5).

Two source families, both configurable (no hard-coded secret, no paid feed):

1. **RSS feeds** — the always-on PRIMARY path. Parsed with ``feedparser`` over
   ``settings.news_rss_feeds``. Deterministic and testable.
2. **BSE announcements** — a best-effort SECONDARY source via ``bsedata``,
   guarded by the default-off ``settings.news_bse_announcements_enabled`` flag.
   Reliability offline is unverified (A6); see the TODO below.

Dedup: ``dedup_hash = sha256(f"{source}|{url or headline}|{published_at_iso}")``
(full 64 hex). Re-seen items hash identically so the repository's
``ON CONFLICT (dedup_hash) DO NOTHING`` makes re-runs add no duplicate.

Error model (§6.5): a per-feed failure is NEVER fatal — one bad feed cannot
block the others. ``ingest_rss`` returns a summary dict
``{added, skipped, failed_feeds}``; it raises :class:`NewsIngestError` ONLY when
EVERY configured feed fails.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from backend.app.config import settings
from backend.app.repositories.news_repository import NewsRepository, SyncNewsRepository

logger = logging.getLogger(__name__)


class NewsIngestError(Exception):
    """Raised only when EVERY configured news source fails (design §6.5)."""


def compute_dedup_hash(
    source: str, url: str | None, headline: str, published_at: Optional[datetime]
) -> str:
    """Return the full 64-hex sha256 dedup identity for a news item (§6.3).

    Input is ``source``, then ``url`` if non-empty else ``headline``, then the
    ISO rendering of ``published_at`` (empty string when the feed omits it),
    joined by ``|``. Same inputs -> same hash (idempotent ingest); a differing
    url -> a different hash (distinct items do not collide).
    """
    identity = url if url else headline
    published_iso = published_at.isoformat() if published_at is not None else ""
    raw = f"{source}|{identity}|{published_iso}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_published(entry) -> Optional[datetime]:
    """Best-effort parse of a feedparser entry's published time -> aware UTC.

    Uses the struct_time ``published_parsed``/``updated_parsed`` feedparser
    already normalized. Returns ``None`` when the feed omits/garbles the date
    (the item is stored with a NULL ``published_at``, §6.5).
    """
    import time

    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _entry_to_row(entry, source: str, ticker: Optional[str]) -> Optional[dict]:
    """Validate + map one feed entry to an insert row, or None if invalid.

    Required (§6.5): ``headline`` (trimmed non-empty), ``url``, ``source``. An
    entry missing a required field is skipped, never inserted with placeholders.
    """
    headline = (entry.get("title") or "").strip()
    url = (entry.get("link") or "").strip()
    if not headline or not url or not source:
        return None
    published_at = _parse_published(entry)
    return {
        "ticker": ticker,
        "source": source,
        "headline": headline,
        "url": url,
        "published_at": published_at,
        "dedup_hash": compute_dedup_hash(source, url, headline, published_at),
    }


class NewsService:
    """RSS (primary) + BSE announcements (flagged secondary) ingest service."""

    def __init__(
        self,
        repository: Optional[NewsRepository] = None,
        sync_repository: Optional[SyncNewsRepository] = None,
    ):
        self._repo = repository
        self._sync_repo = sync_repository

    @property
    def repository(self) -> NewsRepository:
        if self._repo is None:
            self._repo = NewsRepository()
        return self._repo

    @property
    def sync_repository(self) -> SyncNewsRepository:
        if self._sync_repo is None:
            self._sync_repo = SyncNewsRepository()
        return self._sync_repo

    async def ingest_rss(
        self,
        feeds: Optional[list[str]] = None,
        ticker: Optional[str] = None,
    ) -> dict:
        """Ingest every configured RSS feed. Returns ``{added, skipped, failed_feeds}``.

        A per-feed network/parse failure is non-fatal (logged WARNING, counted in
        ``failed_feeds``). Raises :class:`NewsIngestError` only if EVERY feed
        fails. ``ticker`` tags the items (``None`` = market-wide).
        """
        feed_urls = feeds if feeds is not None else list(settings.news_rss_feeds)
        added = 0
        skipped = 0
        failed_feeds: list[str] = []

        for url in feed_urls:
            try:
                rows, feed_skipped = self._parse_feed(url, ticker)
            except Exception as exc:  # per-feed failure is non-fatal (§6.5)
                logger.warning("news RSS feed failed %s: %s", url, exc)
                failed_feeds.append(url)
                continue
            skipped += feed_skipped
            if rows:
                inserted = await self.repository.insert_items(rows)
                added += inserted
                skipped += len(rows) - inserted  # ON CONFLICT dedup collisions

        if feed_urls and len(failed_feeds) == len(feed_urls):
            raise NewsIngestError(
                f"all {len(feed_urls)} configured news feeds failed: {failed_feeds}"
            )

        # BSE announcements — best-effort secondary, default OFF (A6).
        # TODO(step-2): verify bsedata announcements endpoint reachability & ToS,
        # then enable (settings.news_bse_announcements_enabled).
        if settings.news_bse_announcements_enabled:
            logger.info("BSE announcements ingest is enabled but not yet implemented")

        return {"added": added, "skipped": skipped, "failed_feeds": failed_feeds}

    @staticmethod
    def _parse_feed(url: str, ticker: Optional[str]) -> tuple[list[dict], int]:
        """Parse one feed URL into insert rows + a skipped count.

        Raises on a hard parse failure (feedparser ``bozo`` with an exception) so
        the caller can mark the feed failed; malformed individual entries are
        skipped (counted), not raised.
        """
        import feedparser

        parsed = feedparser.parse(url)
        bozo_exc = parsed.get("bozo_exception")
        if bozo_exc is not None and not parsed.get("entries"):
            raise RuntimeError(f"feed parse error: {bozo_exc}")

        source = (parsed.feed.get("title") or url).strip() or url
        rows: list[dict] = []
        skipped = 0
        for entry in parsed.entries:
            row = _entry_to_row(entry, source, ticker)
            if row is None:
                skipped += 1
                continue
            rows.append(row)
        return rows, skipped
