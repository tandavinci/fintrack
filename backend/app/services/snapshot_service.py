"""The persisted DataSnapshot builder (design §7.2, §7.3).

``build_and_store_snapshot(ticker, as_of) -> (snapshot_id, payload)`` is a
SYNCHRONOUS callable (it drives the sync quant ``DataPipeline`` and the sync DB
session, matching how ``quant.recommend`` / ``deep.analyze`` run — offline/Celery
callables, never inside a web request).

The enriched daily frame is built through the SAME path quant/deep use — the
``DataPipeline`` whose ``DataFetcher`` now reads THROUGH ``MarketDataService``
(FEAT A §4.6). The ``snapshot_id`` is the IMPORTED
``backend.app.schemas.snapshot.compute_snapshot_id`` — it is NOT reimplemented,
NOT forked, NOT wrapped with different column logic. That single invariant is the
heart of FEAT C.

Idempotency (§7.3): the PK IS the content hash. If a row with ``snapshot_id``
already exists the stored payload is returned without recompute; otherwise the
INSERT uses ``ON CONFLICT (snapshot_id) DO NOTHING`` so racing callers still
leave exactly one row.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.database import create_sync_session
from backend.app.models.data_snapshot import DataSnapshot
from backend.app.repositories.news_repository import SyncNewsRepository
from backend.app.schemas.snapshot import SNAPSHOT_TAIL_N, compute_snapshot_id
from quant.data.indicators import TechnicalIndicators
from quant.data.pipeline import DataPipeline

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL = "1d"
SNAPSHOT_LOOKBACK_DAYS = 200


def _bars_payload(df) -> list[dict]:
    """The last ``SNAPSHOT_TAIL_N`` enriched rows as JSON-safe records.

    Carries the canonical OHLCV + indicator columns so the payload is
    self-describing (§7.2). ``ts`` is the ISO rendering of the row index; every
    numeric cell is a plain float (NaN -> None so JSONB stays valid).
    """
    import math

    import pandas as pd

    tail = df.tail(SNAPSHOT_TAIL_N)
    records: list[dict] = []
    for ts, row in tail.iterrows():
        rec: dict = {"ts": pd.Timestamp(ts).isoformat()}
        for col in df.columns:
            value = row[col]
            try:
                f = float(value)
            except (TypeError, ValueError):
                rec[col] = None
                continue
            rec[col] = None if math.isnan(f) else f
        records.append(rec)
    return records


def _headlines_payload(ticker: str, as_of: date) -> list[dict]:
    """Recent headlines for the payload; ``[]`` if the news source is down (§7.5)."""
    from datetime import datetime, time, timezone

    as_of_dt = datetime.combine(as_of, time.max, tzinfo=timezone.utc)
    try:
        items = SyncNewsRepository().recent_for_ticker(ticker, as_of=as_of_dt)
    except Exception as exc:
        logger.warning("news fetch for snapshot payload failed (%s): %s", ticker, exc)
        return []
    return [
        {
            "source": item.source,
            "headline": item.headline,
            "url": item.url,
            "published_at": item.published_at.isoformat() if item.published_at else None,
        }
        for item in items
    ]


def build_and_store_snapshot(ticker: str, as_of: date) -> tuple[str, dict]:
    """Build and persist a point-in-time snapshot. Returns ``(snapshot_id, payload)``.

    Args:
        ticker: Non-empty symbol.
        as_of: The anchor ``date``.

    Raises:
        ValueError: On an empty ticker, a non-``date`` ``as_of``, or an
            empty/too-short enriched frame.
    """
    if not isinstance(ticker, str) or not ticker.strip():
        raise ValueError("ticker must be a non-empty string")
    if not isinstance(as_of, date):
        raise ValueError("as_of must be a date")

    # 1. Enriched daily frame via the SAME path quant/deep use (routed through
    #    MarketDataService by FEAT A). No cache: the snapshot is authoritative.
    pipeline = DataPipeline(ticker=ticker, interval=SNAPSHOT_INTERVAL)
    df = pipeline.get_enriched_data(
        end_date=as_of.isoformat(),
        interval=SNAPSHOT_INTERVAL,
        lookback_days=SNAPSHOT_LOOKBACK_DAYS,
        use_cache=False,
    )
    if df is None or getattr(df, "empty", True):
        raise ValueError("build_and_store_snapshot requires a non-empty data frame")

    # 2. snapshot_id via the IMPORTED canonical fn (NEVER reimplemented/forked).
    snapshot_id = compute_snapshot_id(ticker, as_of, df)

    session = create_sync_session()
    try:
        # 3. Idempotency: existing row -> return its stored payload unchanged.
        existing = session.execute(
            select(DataSnapshot).where(DataSnapshot.snapshot_id == snapshot_id)
        ).scalar_one_or_none()
        if existing is not None:
            return snapshot_id, existing.payload

        # Build the payload (§7.2).
        payload = {
            "ticker": ticker,
            "as_of": as_of.isoformat(),
            "interval": SNAPSHOT_INTERVAL,
            "snapshot_tail_n": SNAPSHOT_TAIL_N,
            "bars": _bars_payload(df),
            "indicator_summary": TechnicalIndicators.summarize(df),
            "headlines": _headlines_payload(ticker, as_of),
        }

        # 4. INSERT ... ON CONFLICT (snapshot_id) DO NOTHING (race-safe).
        stmt = pg_insert(DataSnapshot).values(
            snapshot_id=snapshot_id,
            ticker=ticker,
            as_of=as_of,
            payload=payload,
        ).on_conflict_do_nothing(index_elements=["snapshot_id"])
        session.execute(stmt)
        session.commit()
        return snapshot_id, payload
    finally:
        session.close()
