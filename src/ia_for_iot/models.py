"""Modelo ORM de la tabla de lecturas del sensor."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    """Instante actual en UTC, con zona horaria explicita."""
    return datetime.now(UTC)


class Lectura(Base):
    """Una medicion de temperatura y humedad enviada por el dispositivo."""

    __tablename__ = "lecturas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    dispositivo: Mapped[str] = mapped_column(String(64), index=True)
    temperatura_c: Mapped[float] = mapped_column(Float)
    humedad_pct: Mapped[float] = mapped_column(Float)
    estado: Mapped[str] = mapped_column(String(16), default="OK")
    alerta: Mapped[bool] = mapped_column(Boolean, default=False)
    motivo: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (Index("ix_lecturas_ts_dispositivo", "timestamp", "dispositivo"),)

    def __repr__(self) -> str:  # pragma: no cover - ayuda en depuracion
        return (
            f"Lectura(id={self.id}, dispositivo={self.dispositivo!r}, "
            f"temperatura_c={self.temperatura_c}, humedad_pct={self.humedad_pct}, "
            f"estado={self.estado!r})"
        )
