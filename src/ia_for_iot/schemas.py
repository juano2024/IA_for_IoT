"""Esquemas Pydantic de entrada y salida de la API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from .models import Lectura


class LecturaCreate(BaseModel):
    """Cuerpo JSON que envia el ESP32."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    dispositivo: str = Field(default="esp32-dht11", min_length=1, max_length=64)
    temperatura_c: float = Field(ge=-40.0, le=80.0, description="Temperatura en grados Celsius")
    humedad_pct: float = Field(ge=0.0, le=100.0, description="Humedad relativa en porcentaje")
    timestamp: datetime | None = Field(
        default=None,
        description="Instante de la medicion; si se omite se usa la hora de recepcion del servidor",
    )


class LecturaOut(BaseModel):
    """Lectura almacenada, devuelta por la API."""

    id: int
    timestamp: datetime
    dispositivo: str
    temperatura_c: float
    humedad_pct: float
    estado: str
    alerta: bool
    motivo: str | None = None

    @classmethod
    def from_model(cls, lectura: Lectura) -> LecturaOut:
        """Construye la respuesta a partir de una instancia del modelo ORM."""
        ts: datetime = lectura.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return cls(
            id=lectura.id,
            timestamp=ts,
            dispositivo=lectura.dispositivo,
            temperatura_c=lectura.temperatura_c,
            humedad_pct=lectura.humedad_pct,
            estado=lectura.estado,
            alerta=lectura.alerta,
            motivo=lectura.motivo,
        )


class MetricaOut(BaseModel):
    """Resumen de una magnitud medida."""

    minimo: float
    maximo: float
    promedio: float


class EstadisticasOut(BaseModel):
    """Resumen estadistico de las lecturas recientes."""

    total: int
    ventana: int
    temperatura: MetricaOut
    humedad: MetricaOut
    fuera_de_rango_pct: float
    ultima_lectura: LecturaOut | None = None


class SaludOut(BaseModel):
    """Estado de salud del servicio."""

    estado: str
    version: str
    total_lecturas: int
    ultima_lectura: LecturaOut | None = None
    base_de_datos: str
    csv: str
