"""NewsItem ORM model (design §6.2).

One deduplicated row per ingested news/announcement item. ``dedup_hash`` is the
unique identity (sha256 over source|url-or-headline|published_at_iso, defined by
the FEAT-B ingest service). ``ticker`` is nullable for market-wide items.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database import Base


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)  # resolved symbol or NULL (market-wide)
    source: Mapped[str] = mapped_column(String(64), nullable=False)  # feed/source id
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    dedup_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("dedup_hash", name="uq_news_items_dedup"),
        Index("idx_news_ticker_published", "ticker", "published_at"),
    )
