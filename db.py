"""
db.py
─────
SQLAlchemy engine, session factory, and declarative Base.

Rules enforced here
───────────────────
• Each request/thread creates its own session via get_session().
• Sessions are NEVER shared between threads.
• Callers must close sessions in a finally block (or use the context manager).

Usage
-----
    # Pattern A — context manager (preferred)
    from db import get_session
    with get_session() as session:
        students = session.query(Student).all()

    # Pattern B — manual (use inside QThread workers)
    from db import get_session
    session = get_session()
    try:
        ...
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import DATABASE_URL


# ── Engine ────────────────────────────────────────────────────────────────────
# pool_pre_ping=True  — drops stale connections automatically
# echo=False          — set True during development to log all SQL
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    echo=False,
    pool_size=5,
    max_overflow=10,
)


# ── Session factory ───────────────────────────────────────────────────────────
# autocommit=False, autoflush=False — explicit control (recommended)
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,   # rows stay accessible after commit
)


# ── Declarative Base (all models inherit from this) ───────────────────────────
class Base(DeclarativeBase):
    pass


# ── Convenience helpers ───────────────────────────────────────────────────────

@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Context-manager that opens a session, yields it, and always closes it.

    On exception the transaction is rolled back before closing.
    """
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """
    Create all tables that don't exist yet.
    Call once at application startup (after all models are imported).
    """
    # Import models so their metadata is registered on Base before create_all
    from models import student, geo_cache, feature_store  # noqa: F401
    Base.metadata.create_all(bind=engine)
    print("[DB] Tables created / verified.")
