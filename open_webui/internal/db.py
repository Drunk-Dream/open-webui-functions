"""Minimal Open WebUI internal.db module for testing."""

from contextlib import contextmanager
from typing import Generator

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


def get_db() -> Generator[Session, None, None]:
    """Get database session."""
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
