"""Database engine and session factory.

Default: SQLite (no setup needed).
Override: set DATABASE_URL=postgresql+asyncpg://... in .env.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .project import Base

_engine = None
_session_factory = None


def get_engine(database_url: str):
    global _engine
    if _engine is None:
        connect_args = {"check_same_thread": False} if "sqlite" in database_url else {}
        _engine = create_async_engine(
            database_url,
            connect_args=connect_args,
            echo=False,
        )
    return _engine


def get_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        engine = get_engine(database_url)
        _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory


async def create_tables(database_url: str) -> None:
    engine = get_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
