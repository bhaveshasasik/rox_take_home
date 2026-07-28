from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()

engine = create_async_engine(_settings.database_url, echo=False, future=True)

SessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


async def init_db() -> None:
    """Create tables. No Alembic while the schema is still churning."""
    from app import models  # noqa: F401  — register mappers before create_all

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with SessionLocal() as session:
        yield session
