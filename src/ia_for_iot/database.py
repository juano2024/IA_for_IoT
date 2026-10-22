"""Configuracion del motor SQLAlchemy y de la sesion de base de datos."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Clase base declarativa para los modelos ORM."""


def build_engine(url: str, **overrides: Any) -> Engine:
    """Crea un Engine con los parametros correctos segun el motor usado."""
    kwargs: dict[str, Any] = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        # FastAPI sirve las peticiones en hilos distintos; SQLite exige
        # desactivar la comprobacion de hilo y usar un timeout de escritura.
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    kwargs.update(overrides)
    return create_engine(url, **kwargs)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Crea la fabrica de sesiones ligada a un engine concreto."""
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Crea las tablas del modelo si todavia no existen."""
    from . import models  # noqa: F401  (registra las tablas en Base.metadata)

    Base.metadata.create_all(bind=engine)


def get_db(request: Request) -> Generator[Session]:
    """Dependencia de FastAPI que entrega una sesion de base de datos."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    db = factory()
    try:
        yield db
    finally:
        db.close()
