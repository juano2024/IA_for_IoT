"""Persistencia de las lecturas en un archivo CSV."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CSV_HEADERS = [
    "timestamp",
    "dispositivo",
    "temperatura_c",
    "humedad_pct",
    "estado",
    "alerta",
]


def _iso(valor: datetime) -> str:
    """Serializa un instante en ISO 8601 con sufijo UTC."""
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=UTC)
    return valor.astimezone(UTC).isoformat().replace("+00:00", "Z")


def append_csv(path: Path, registro: dict[str, Any]) -> None:
    """Agrega una lectura al final del CSV, creando la cabecera si hace falta."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existe = path.exists() and path.stat().st_size > 0
    with path.open("a", newline="", encoding="utf-8") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=CSV_HEADERS)
        if not existe:
            escritor.writeheader()
        escritor.writerow(fila_csv(registro))


def fila_csv(registro: dict[str, Any]) -> dict[str, str]:
    """Normaliza un registro al formato de columnas del CSV."""
    return {
        "timestamp": registro["timestamp"],
        "dispositivo": registro["dispositivo"],
        "temperatura_c": f"{float(registro['temperatura_c']):.2f}",
        "humedad_pct": f"{float(registro['humedad_pct']):.2f}",
        "estado": registro["estado"],
        "alerta": "true" if registro["alerta"] else "false",
    }


def iso_utc(valor: datetime) -> str:
    """Utilidad publica para serializar instantes en ISO 8601 UTC."""
    return _iso(valor)
