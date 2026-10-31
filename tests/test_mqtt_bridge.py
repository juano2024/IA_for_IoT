"""Pruebas del puente MQTT -> InfluxDB + CSV (Practica 2)."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ia_for_iot.config import Settings
from ia_for_iot.mqtt_bridge import (
    Emparejador,
    construir_punto,
    interpretar_mensaje,
    procesar_lectura,
)


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """Configuracion aislada: CSV en un directorio temporal."""
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        csv_path=tmp_path / "lecturas.csv",
        influx_bucket="iot",
    )


def test_interpreta_topico_de_temperatura() -> None:
    assert interpretar_mensaje("invernadero1/temperatura", b"21.40", "invernadero1") == (
        "temperatura",
        21.40,
    )


def test_interpreta_topico_de_humedad() -> None:
    assert interpretar_mensaje("invernadero1/humedad", b"49", "invernadero1") == (
        "humedad",
        49.0,
    )


def test_ignora_topicos_ajenos() -> None:
    assert interpretar_mensaje("otro/temperatura", b"21", "invernadero1") is None
    assert interpretar_mensaje("invernadero1/estado", b"online", "invernadero1") is None


def test_rechaza_valor_no_numerico() -> None:
    assert interpretar_mensaje("invernadero1/temperatura", b"nan?", "invernadero1") is None


def test_interpreta_json_completo() -> None:
    payload = b'{"temperatura_c": 21.5, "humedad_pct": 48}'
    assert interpretar_mensaje("invernadero1/sensores", payload, "invernadero1") == (
        "lectura",
        {"temperatura_c": 21.5, "humedad_pct": 48.0},
    )


def test_emparejador_espera_ambas_magnitudes() -> None:
    emparejador = Emparejador(ttl=10)
    assert emparejador.agregar("temperatura", 21.0, ahora=100.0) is None
    assert emparejador.agregar("humedad", 50.0, ahora=101.0) == {
        "temperatura_c": 21.0,
        "humedad_pct": 50.0,
    }


def test_emparejador_descarta_valores_antiguos() -> None:
    emparejador = Emparejador(ttl=5)
    assert emparejador.agregar("temperatura", 21.0, ahora=100.0) is None
    assert emparejador.agregar("humedad", 50.0, ahora=106.0) is None


def test_punto_influx_tiene_tags_y_campos(settings: Settings) -> None:
    punto = construir_punto(
        {"temperatura_c": 25.0, "humedad_pct": 70.0},
        settings,
        datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
    )
    linea = punto.to_line_protocol()
    assert linea.startswith("clima,")
    assert "sala=invernadero1" in linea
    assert "estado=ALERTA" in linea
    assert "temperatura_c=25 " in linea
    assert "humedad_pct=70," in linea
    assert "alerta=1i" in linea


def test_procesar_lectura_escribe_csv(settings: Settings) -> None:
    procesar_lectura(
        {"temperatura_c": 20.0, "humedad_pct": 50.0},
        settings,
        write_api=None,
    )
    with settings.csv_path.open(encoding="utf-8", newline="") as archivo:
        filas = list(csv.DictReader(archivo))
    assert len(filas) == 1
    assert filas[0]["estado"] == "OK"
    assert filas[0]["alerta"] == "false"


def test_procesar_lectura_envia_a_influx(settings: Settings) -> None:
    class WriteApiFalso:
        def __init__(self) -> None:
            self.registros: list[tuple[str, Any]] = []

        def write(self, bucket: str, record: Any) -> None:
            self.registros.append((bucket, record))

    falso = WriteApiFalso()
    procesar_lectura(
        {"temperatura_c": 25.0, "humedad_pct": 50.0},
        settings,
        write_api=falso,
    )
    assert len(falso.registros) == 1
    bucket, punto = falso.registros[0]
    assert bucket == settings.influx_bucket
    assert "alerta=1i" in punto.to_line_protocol()


def test_procesar_lectura_guarda_csv_aunque_falle_influx(settings: Settings) -> None:
    class WriteApiLento:
        def write(self, bucket: str, record: Any) -> None:
            raise TimeoutError("timed out")

    with pytest.raises(TimeoutError):
        procesar_lectura(
            {"temperatura_c": 20.0, "humedad_pct": 50.0},
            settings,
            write_api=WriteApiLento(),
        )
    with settings.csv_path.open(encoding="utf-8", newline="") as archivo:
        filas = list(csv.DictReader(archivo))
    assert len(filas) == 1
