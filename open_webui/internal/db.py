"""Minimal Open WebUI internal.db module for testing."""

from contextlib import asynccontextmanager, contextmanager
from typing import AsyncGenerator, Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""

    pass


# Create in-memory SQLite engine for testing
engine = create_engine("sqlite:///:memory:", echo=False)


@contextmanager
def get_db_context(db: Session | None = None) -> Generator[Session, None, None]:
    """Database context manager."""
    if db is not None:
        yield db
    else:
        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Get database session."""
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


async def get_async_session() -> AsyncGenerator[Session, None]:
    """Async session generator matching Open WebUI 0.9.x shape."""
    with get_db() as session:
        yield session


@asynccontextmanager
async def get_async_db_context(db: Session | None = None):
    """Async DB context manager matching Open WebUI 0.9.x shape."""
    if db is not None:
        yield db
        return

    with get_db() as session:
        yield session
