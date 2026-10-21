"""Database engine and session factory configuration.

This is the canonical module for SQLAlchemy async engine and session.
All other modules should import from here.
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.config import settings

# Async engine for PostgreSQL via asyncpg
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
)

# Session factory for creating async sessions
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# --- Shared SYNC engine + session factory (design §7.2/A2) ---------------------
# Lifted from SRC-T tasks/insight_calculator._get_sync_database_url /
# _create_sync_session. The sync engine backs the synchronous facade the quant
# and deep lanes call (they are plain synchronous callables and import no async
# web stack). Created lazily so a process that never touches the sync path
# (e.g. the pure-offline test suite) never opens a psycopg2 pool.


def get_sync_database_url() -> str:
    """Return the synchronous (psycopg2) database URL.

    Uses ``settings.database_url_sync`` when set, otherwise swaps the async
    driver in ``settings.database_url`` (``postgresql+asyncpg`` ->
    ``postgresql+psycopg2``), mirroring SRC-T's conversion.
    """
    if settings.database_url_sync:
        return settings.database_url_sync
    url = settings.database_url
    if "asyncpg" in url:
        return url.replace("postgresql+asyncpg", "postgresql+psycopg2")
    return url


_sync_engine = None
_sync_session_factory: sessionmaker | None = None


def get_sync_engine():
    """Get or lazily create the shared synchronous SQLAlchemy engine."""
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(
            get_sync_database_url(),
            echo=settings.debug,
            pool_size=5,
            max_overflow=2,
            pool_pre_ping=True,
        )
    return _sync_engine


def get_sync_session_factory() -> sessionmaker:
    """Get or lazily create the shared synchronous session factory."""
    global _sync_session_factory
    if _sync_session_factory is None:
        _sync_session_factory = sessionmaker(
            bind=get_sync_engine(),
            class_=Session,
            expire_on_commit=False,
        )
    return _sync_session_factory


def create_sync_session() -> Session:
    """Create a new synchronous SQLAlchemy session from the shared factory."""
    return get_sync_session_factory()()


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass
