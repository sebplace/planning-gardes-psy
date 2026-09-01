"""Accès base de données et primitives d'atomicité.

Les transitions d'état concurrentes sensibles (candidature, tirage, échange) passent
par des transitions **gardées côté serveur**, jamais par une protection navigateur
(cf. DECISIONS.md D-008).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models.base import Base

_connect_args = {}
if settings.database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False, "timeout": 30}

engine = create_engine(
    settings.database_url,
    future=True,
    connect_args=_connect_args,
    pool_pre_ping=True,
)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - configuration
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_session() -> Iterator[Session]:
    """Dépendance FastAPI."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def exclusive_transaction(session: Session) -> Iterator[Session]:
    """Transaction sérialisée pour les opérations critiques.

    * SQLite  : ``BEGIN IMMEDIATE`` — prend le verrou d'écriture dès l'ouverture.
    * PostgreSQL : la sérialisation repose sur ``SELECT ... FOR UPDATE`` posé par
      les services sur les lignes concernées.
    """
    dialect = session.get_bind().dialect.name
    if session.in_transaction():
        session.commit()
    if dialect == "sqlite":
        connection = session.connection()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise


def lock_row(session: Session, model, pk):
    """Verrou de ligne portable. Sous SQLite le verrou est déjà global à la transaction."""
    query = session.query(model).filter(model.id == pk)
    if session.get_bind().dialect.name != "sqlite":
        query = query.with_for_update()
    return query.one_or_none()


def create_all() -> None:
    Base.metadata.create_all(bind=engine)


def drop_all() -> None:
    Base.metadata.drop_all(bind=engine)
