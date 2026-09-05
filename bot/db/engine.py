"""Async SQLAlchemy engine и session factory."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

_command_session: ContextVar["CommandSession | None"] = ContextVar("command_session", default=None)
_command_owner: ContextVar[object | None] = ContextVar("command_owner", default=None)


class CommandSession(AsyncSession):
    """Legacy repositories flush inside an explicitly owned command transaction."""

    rollback_only = False

    async def commit(self) -> None:
        if _command_session.get() is self:
            await self.flush()
        else:
            await super().commit()

    async def rollback(self) -> None:
        if _command_session.get() is self:
            self.rollback_only = True
        await super().rollback()


session_factory = async_sessionmaker(
    engine,
    class_=CommandSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def async_session() -> AsyncGenerator[CommandSession, None]:
    borrowed = _command_session.get()
    if borrowed is not None:
        if _command_owner.get() is not asyncio.current_task():
            raise RuntimeError("Command session cannot be shared with a child task")
        yield borrowed
    else:
        async with session_factory() as session:
            yield session


@asynccontextmanager
async def command_transaction(session: CommandSession) -> AsyncGenerator[None, None]:
    """Bind nested repositories to one transaction; caller owns durable commit."""
    if _command_session.get() is not None:
        raise RuntimeError("Nested command transaction")
    token = _command_session.set(session)
    owner_token = _command_owner.set(asyncio.current_task())
    try:
        yield
        if session.rollback_only:
            raise RuntimeError("Command rolled back its transaction")
    finally:
        _command_session.reset(token)
        _command_owner.reset(owner_token)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Получить async-сессию для использования в контекстном менеджере."""
    async with async_session() as session:
        yield session
