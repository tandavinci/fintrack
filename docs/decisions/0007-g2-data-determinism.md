# 0007 — Gate G2 data determinism

Date: 2026-10-21
Status: accepted
Gate: Step 2 / Gate G2

## Context

Gate G2 requires that for every golden ticker the tracker, quant core, and deep
engine read identical prices and indicators, so that
`compute_snapshot_id(ticker, as_of, df)` (the 16-char sha256 prefix over the last
`SNAPSHOT_TAIL_N=60` enriched rows, each cell rendered `float(value)` at 6 dp)
is equal across the three lanes AND identical whether a frame was served cold
from yfinance, from Postgres, or from Redis. Two sources of non-determinism had
to be eliminated to make that equality exact (never tolerance-based):

1. **Window boundary.** yfinance's daily `history(start=, end=)` treats `end` as
   an EXCLUSIVE upper bound. If the Postgres read or the Redis-keyed window used
   an INCLUSIVE boundary, a cold fetch and a DB/Redis read for the same
   `(ticker, as_of)` could carry a different final bar — one extra row — and the
   snapshot ids would diverge by construction.
2. **Float round-trip.** `compute_snapshot_id` renders OHLCV + indicator cells
   from the in-memory `float64` frame. If Postgres stored OHLCV at reduced
   precision (e.g. `Numeric(18,4)`) or Redis serialized via a lossy `round()`,
   the DB/Redis-served frame would not be bit-identical to the cold pandas
   `float64` frame and the ids would differ.

There is also a deliberate boundary asymmetry between bars and news that must be
recorded so it is never "corrected" by a later change.

## Decision

Two determinism decisions, both implemented in FEAT-002:

1. **EXCLUSIVE `end` / `as_of` on every path.** The service normalizes the
   window ONCE at the top of `get_ohlcv` and then treats `end`/`as_of` as an
   EXCLUSIVE upper bound everywhere — the yfinance leg
   (`history(start=start_norm, end=end_norm)`), the Postgres predicate
   (`ts >= start AND ts < end`), and the Redis key
   (`ohlcv:{symbol}:{interval}:{start_norm_iso}:{end_norm_iso}`) all consume the
   same normalized `[start, end)` window. This matches yfinance's own exclusive
   `end` so cold, DB, and Redis reads carry the identical row set. Chosen over
   normalizing everything to inclusive (which would have changed every existing
   `snapshot_id`).

2. **DOUBLE PRECISION storage + exact-float Redis serialization.** `price_bars`
   OHLCV columns are `Float(precision=53)` = `DOUBLE PRECISION` (IEEE-754
   binary64), and the Redis OHLCV serializer casts `np.float64 → Python float`
   (lossless, both binary64) before `json.dumps` with NO `round()`. A DB- or
   Redis-served frame is therefore bit-identical to the cold-fetch pandas
   `float64` frame, so `compute_snapshot_id` sees the same bytes on every path
   and cold/DB/Redis ids are equal. `volume` stays `BigInteger`, which is
   byte-faithful only because yfinance `.BO`/`.NS` volume is integral in
   practice; the documented mitigation if a fractional-volume source is ever
   added is to widen `volume` to `DOUBLE PRECISION`. The canonical
   `compute_snapshot_id` render is never forked to achieve this — determinism is
   bought by making storage lossless, not by changing the hash.

**Intentional news-vs-bars boundary asymmetry.** Bar reads are EXCLUSIVE on the
upper bound (`ts < as_of`, matching yfinance) while news reads are INCLUSIVE
(`published_at <= as_of`). This asymmetry is deliberate and point-in-time-safe
for each data type: a bar timestamped at the `as_of` instant belongs to the next
period and must be excluded, whereas a headline published at the `as_of` instant
was available to a reader at that moment and must be included. It must NOT be
"fixed" to make the two boundaries match.

## Consequences

- Cold, DB, and Redis reads for the same `(ticker, as_of, lookback, interval)`
  produce the identical enriched frame and the identical `snapshot_id`; the Gate
  G2 three-lane equality can be asserted exactly, with no per-lane tolerance.
  (The G2 test enriches the tracker lane's raw OHLCV through the ONE shared quant
  `TechnicalIndicators` + `dropna` path before comparing, since the service
  returns raw OHLCV only — see REQ-S2-1.)
- Historical as-of snapshots are stable. The one residual non-determinism — a
  very recent `as_of` whose bars yfinance later restates — is confined to the
  recent window by the two-regime coverage rule (a historical window,
  `end < today - FRESH_WINDOW_DAYS`, with ≥1 stored row is DB-authoritative and
  never refetched).
- The determinism guarantee is a quant/deep (enriched-frame) guarantee; the
  tracker lane's own guarantee is raw-bar equality against the other two lanes.
- Live verification requires the compose stack up and a reachable yfinance;
  where those are unavailable the integration + G2 tests skip-with-reason and the
  golden parquet fixtures are not materialized (see `docs/status.md` Week 3 open
  items). The per-source terms of use, attribution, and allowed-use note
  (personal research, non-redistributed) for the news lane are documented in
  `docs/news-sources.md`.
