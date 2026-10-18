"""Database engine/session management and transactional retry helper."""

import time
from collections.abc import Callable, Iterator
from typing import TypeVar

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

T = TypeVar("T")

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        s = get_settings()
        _engine = create_engine(
            s.database_url,
            # No pre-ping: it costs a network round trip on every checkout. Connections are
            # recycled before hosted poolers drop them, and TCP keepalives catch dead ones.
            pool_pre_ping=False,
            pool_use_lifo=True,  # reuse the warmest connection; spares can expire
            pool_size=s.db_pool_size,
            max_overflow=s.db_max_overflow,
            pool_recycle=120,
            connect_args={"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10,
                          "keepalives_count": 3},
        )
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def reset_engine() -> None:
    """Dispose the pool (used by tests and after DB restarts)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()


# PostgreSQL SQLSTATEs that are safe to retry: serialization_failure, deadlock_detected.
_RETRYABLE = {"40001", "40P01"}


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, DBAPIError) and exc.orig is not None:
        return getattr(exc.orig, "sqlstate", None) in _RETRYABLE
    return False


def run_in_transaction(db: Session, fn: Callable[[Session], T], attempts: int = 5) -> T:
    """Run fn inside a transaction; commit on success, retry on deadlock/serialization errors."""
    for attempt in range(attempts):
        try:
            result = fn(db)
            db.commit()
            return result
        except (OperationalError, DBAPIError) as exc:
            db.rollback()
            if _is_retryable(exc) and attempt < attempts - 1:
                time.sleep(0.02 * (2**attempt))
                continue
            raise
        except Exception:
            db.rollback()
            raise
    raise RuntimeError("unreachable")
