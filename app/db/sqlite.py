"""
app/db/sqlite.py — SQLAlchemy engine, session factory, declarative Base.

All SQLAlchemy models inherit from Base defined here.
Call init_db() once at startup to create all tables.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},  # required for SQLite + FastAPI threads
    echo=(settings.LOG_LEVEL == "DEBUG"),
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Shared metadata registry — every model must inherit from this."""
    pass


def get_db():
    """FastAPI dependency: yields a session, always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables. Safe to call multiple times (idempotent)."""
    # Side-effect imports register models on Base.metadata before create_all.
    import app.models.document   # noqa: F401
    import app.models.node       # noqa: F401
    import app.models.selection  # noqa: F401
    import app.models.generation # noqa: F401
    Base.metadata.create_all(bind=engine)
