"""SQLAlchemy engine / session plumbing."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from reelarr.config import get_config


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        cfg = get_config()
        if cfg.sqlalchemy_url.startswith("sqlite:///"):
            db_path = Path(cfg.sqlalchemy_url.removeprefix("sqlite:///"))
            if db_path.parent and str(db_path.parent) not in ("", "."):
                db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(cfg.sqlalchemy_url, connect_args={"check_same_thread": False})
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db() -> None:
    # Import models so metadata is populated before create_all.
    import reelarr.models  # noqa: F401

    Base.metadata.create_all(get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()
