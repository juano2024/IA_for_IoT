"""Logica de negocio: evaluar el confort y registrar las lecturas.

Este modulo es el corazon del laboratorio. Cada medicion que llega del ESP32
se evalua contra los rangos de confort del caso de uso y se guarda en dos
soportes: la base de datos SQLite y el archivo CSV.
"""

from __future__ import annotations

from datetime import UTC

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .models import Lectura, utcnow
from .schemas import (
    EstadisticasOut,
    LecturaCreate,
    LecturaOut,
    MetricaOut,
)
from .storage import append_csv

ESTADO_OK = "OK"
ESTADO_ALERTA = "ALERTA"


def evaluar(
    temperatura_c: float, humedad_pct: float, settings: Settings
) -> tuple[str, bool, str | None]:
    """Clasifica una lectura segun los rangos de confort configurados.

    Retorna ``(estado, alerta, motivo)``.
    """
    motivos: list[str] = []
    if temperatura_c < settings.temp_min:
        motivos.append(f"temperatura por debajo de {settings.temp_min:g} C")
    elif temperatura_c > settings.temp_max:
        motivos.append(f"temperatura por encima de {settings.temp_max:g} C")

    if humedad_pct < settings.hum_min:
        motivos.append(f"humedad por debajo de {settings.hum_min:g} %")
    elif humedad_pct > settings.hum_max:
        motivos.append(f"humedad por encima de {settings.hum_max:g} %")

    if motivos:
        return ESTADO_ALERTA, True, "; ".join(motivos)
    return ESTADO_OK, False, None


def registrar(db: Session, payload: LecturaCreate, settings: Settings) -> LecturaOut:
    """Registra una lectura en la base de datos y en el CSV."""
    timestamp = payload.timestamp or utcnow()
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)

    estado, alerta, motivo = evaluar(payload.temperatura_c, payload.humedad_pct, settings)

    lectura = Lectura(
        timestamp=timestamp,
        dispositivo=payload.dispositivo,
        temperatura_c=payload.temperatura_c,
        humedad_pct=payload.humedad_pct,
        estado=estado,
        alerta=alerta,
        motivo=motivo,
    )
    db.add(lectura)
    db.commit()
    db.refresh(lectura)

    append_csv(
        settings.csv_path,
        {
            "timestamp": timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "dispositivo": payload.dispositivo,
            "temperatura_c": payload.temperatura_c,
            "humedad_pct": payload.humedad_pct,
            "estado": estado,
            "alerta": alerta,
        },
    )

    return LecturaOut.from_model(lectura)


def listar(db: Session, limite: int = 100, dispositivo: str | None = None) -> list[LecturaOut]:
    """Devuelve las lecturas mas recientes primero."""
    consulta = select(Lectura)
    if dispositivo:
        consulta = consulta.where(Lectura.dispositivo == dispositivo)
    consulta = consulta.order_by(Lectura.timestamp.desc(), Lectura.id.desc()).limit(limite)
    return [LecturaOut.from_model(lectura) for lectura in db.scalars(consulta).all()]


def total_lecturas(db: Session) -> int:
    """Numero total de lecturas almacenadas."""
    return int(db.scalar(select(func.count()).select_from(Lectura)) or 0)


def estadisticas(
    db: Session, ventana: int = 100, settings: Settings | None = None
) -> EstadisticasOut:
    """Calcula estadisticas descriptivas sobre las ultimas lecturas."""
    settings = settings or Settings()
    lecturas = listar(db, limite=ventana)
    if not lecturas:
        vacio = MetricaOut(minimo=0.0, maximo=0.0, promedio=0.0)
        return EstadisticasOut(
            total=0,
            ventana=ventana,
            temperatura=vacio,
            humedad=vacio,
            fuera_de_rango_pct=0.0,
            ultima_lectura=None,
        )

    temperaturas = [lectura.temperatura_c for lectura in lecturas]
    humedades = [lectura.humedad_pct for lectura in lecturas]
    fuera = sum(1 for lectura in lecturas if lectura.alerta)

    return EstadisticasOut(
        total=total_lecturas(db),
        ventana=ventana,
        temperatura=MetricaOut(
            minimo=min(temperaturas),
            maximo=max(temperaturas),
            promedio=round(sum(temperaturas) / len(temperaturas), 2),
        ),
        humedad=MetricaOut(
            minimo=min(humedades),
            maximo=max(humedades),
            promedio=round(sum(humedades) / len(humedades), 2),
        ),
        fuera_de_rango_pct=round(fuera / len(lecturas) * 100, 2),
        ultima_lectura=lecturas[0],
    )
