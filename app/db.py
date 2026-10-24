"""Database engine and request-scoped sessions."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base


engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None


def initialize_database(database_url: str) -> None:
    global engine, SessionLocal

    engine = create_engine(database_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)


def dispose_database() -> None:
    global engine, SessionLocal

    if engine is not None:
        engine.dispose()
    engine = None
    SessionLocal = None


def get_db() -> Generator[Session, None, None]:
    if SessionLocal is None:
        raise RuntimeError("Database is not initialized")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
