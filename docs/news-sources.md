# News sources — terms of use & attribution (FEAT B, design §6.1)

This document is a **hard deliverable** of the news-ingest feature. The news
service (`backend/app/services/news_service.py`) only ingests sources listed and
cleared here. A source whose terms-of-use forbid automated access or caching is
**NOT** added.

**Allowed use (applies to every source below):** personal research,
**non-redistributed**. The ingested headlines are stored locally and surfaced
only inside point-in-time `DataSnapshot` payloads for the operator's own
analysis. Nothing scraped here is re-published, redistributed, or served to
third parties — matching `scope.md`'s no-redistribution stop-and-pivot rule.

## Shipped default is a non-resolvable PLACEHOLDER

The `NEWS_RSS_FEEDS` value shipped in `infra/.env.example`
(`https://example-business-daily.invalid/markets.rss`) is an **intentional,
non-resolvable placeholder** (`.invalid` TLD per RFC 2606). It is NOT a feed
meant to be polled and MUST be replaced with the ToU-cleared feed list below
**before any ingest run**. With the placeholder left in place, an ingest run
logs a single WARNING per failed feed and returns `{added: 0, ...}` (design
§6.5) rather than crashing.

## Configured sources

Populate the table below with each feed the operator adds to
`infra/.env`'s `NEWS_RSS_FEEDS`. For EACH source document: name, feed URL, its
terms-of-use / robots stance, the attribution requirement, and the allowed-use
note. Do not add a source until its row here is complete and its ToU permits
automated, non-redistributed personal use.

| Source name | Feed URL | ToU / robots stance | Attribution requirement | Allowed use |
| --- | --- | --- | --- | --- |
| _(placeholder)_ `example-business-daily` | `https://example-business-daily.invalid/markets.rss` | Non-resolvable placeholder — NOT polled. Replace before ingest. | — | personal research, non-redistributed |

### Adding a source — checklist

1. Confirm the publisher offers an RSS/Atom feed intended for syndication.
2. Read the site's `robots.txt` and terms of use. If automated access or local
   caching is prohibited, **do not add it**.
3. Record the attribution the publisher requires (headline + link back to the
   source article is the common minimum; keep the item's `url` so attribution
   is always reconstructable).
4. Add the row above, then add the feed URL to `NEWS_RSS_FEEDS` in `infra/.env`.

## BSE announcements (secondary, default OFF)

BSE corporate announcements via the `bsedata` library are a best-effort
secondary source, guarded by the default-off flag
`NEWS_BSE_ANNOUNCEMENTS_ENABLED` (design §6.1). It is **not enabled** because the
announcements endpoint's offline reachability and terms-of-service for automated
access are unverified (A6).

> TODO(step-2): verify bsedata announcements endpoint reachability & ToS, then
> enable.

When enabled, the same allowed-use rule (personal research, non-redistributed)
and attribution obligations apply, and this document must be updated with the
endpoint and its ToU stance before turning the flag on.
