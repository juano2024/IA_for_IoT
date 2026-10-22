"""Pruebas de la API del servidor IA for IoT."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ia_for_iot.api import create_app
from ia_for_iot.config import Settings


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """Configuracion aislada: base de datos y CSV en un directorio temporal."""
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        csv_path=tmp_path / "lecturas.csv",
    )


@pytest.fixture()
def client(settings: Settings) -> Iterator[TestClient]:
    """Cliente HTTP sobre una instancia de la aplicacion con datos de prueba."""
    with TestClient(create_app(settings)) as cliente:
        yield cliente


def _payload(temperatura: float, humedad: float) -> dict[str, float]:
    return {
        "dispositivo": "esp32-dht11",
        "temperatura_c": temperatura,
        "humedad_pct": humedad,
    }


def test_registra_lectura_dentro_del_rango(client: TestClient) -> None:
    respuesta = client.post("/api/lecturas", json=_payload(20.5, 50.0))
    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "OK"
    assert cuerpo["alerta"] is False
    assert cuerpo["motivo"] is None
    assert cuerpo["temperatura_c"] == 20.5


def test_registra_lectura_fuera_de_rango(client: TestClient) -> None:
    respuesta = client.post("/api/lecturas", json=_payload(27.5, 72.0))
    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "ALERTA"
    assert cuerpo["alerta"] is True
    assert "temperatura" in cuerpo["motivo"]
    assert "humedad" in cuerpo["motivo"]


def test_rechaza_payload_invalido(client: TestClient) -> None:
    respuesta = client.post("/api/lecturas", json=_payload(150.0, 50.0))
    assert respuesta.status_code == 422


def test_historico_devuelve_las_lecturas(client: TestClient) -> None:
    client.post("/api/lecturas", json=_payload(19.0, 45.0))
    client.post("/api/lecturas", json=_payload(21.0, 55.0))
    respuesta = client.get("/api/lecturas")
    assert respuesta.status_code == 200
    assert len(respuesta.json()) == 2


def test_guarda_la_lectura_en_el_csv(client: TestClient, settings: Settings) -> None:
    client.post("/api/lecturas", json=_payload(19.0, 45.0))
    assert settings.csv_path.is_file()
    with settings.csv_path.open(encoding="utf-8", newline="") as archivo:
        filas = list(csv.DictReader(archivo))
    assert len(filas) == 1
    assert filas[0]["temperatura_c"] == "19.00"
    assert filas[0]["humedad_pct"] == "45.00"
    assert filas[0]["estado"] == "OK"
    assert filas[0]["alerta"] == "false"


def test_descarga_del_csv(client: TestClient) -> None:
    client.post("/api/lecturas", json=_payload(19.0, 45.0))
    respuesta = client.get("/api/lecturas.csv")
    assert respuesta.status_code == 200
    assert "text/csv" in respuesta.headers["content-type"]
    assert "temperatura_c" in respuesta.text


def test_health_reporta_el_estado(client: TestClient) -> None:
    client.post("/api/lecturas", json=_payload(20.0, 50.0))
    respuesta = client.get("/api/health")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["estado"] == "ok"
    assert cuerpo["total_lecturas"] == 1
    assert cuerpo["ultima_lectura"]["temperatura_c"] == 20.0


def test_estadisticas_calculan_promedios(client: TestClient) -> None:
    client.post("/api/lecturas", json=_payload(18.0, 40.0))
    client.post("/api/lecturas", json=_payload(22.0, 60.0))
    client.post("/api/lecturas", json=_payload(25.0, 50.0))
    respuesta = client.get("/api/estadisticas?ventana=100")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["total"] == 3
    assert cuerpo["temperatura"]["minimo"] == 18.0
    assert cuerpo["temperatura"]["maximo"] == 25.0
    assert cuerpo["temperatura"]["promedio"] == 21.67
    assert cuerpo["fuera_de_rango_pct"] == 33.33


def test_panel_web_se_sirve(client: TestClient) -> None:
    respuesta = client.get("/")
    assert respuesta.status_code == 200
    assert "IA for IoT" in respuesta.text


def test_umbrales_expuestos(client: TestClient) -> None:
    respuesta = client.get("/api/umbrales")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["temperatura_c"] == {"min": 18.0, "max": 22.0}
    assert cuerpo["humedad_pct"] == {"min": 40.0, "max": 60.0}


def test_api_key_se_exige_cuando_esta_configurada(tmp_path: Path) -> None:
    configuracion = Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        csv_path=tmp_path / "lecturas.csv",
        device_api_key="secreto",
    )
    with TestClient(create_app(configuracion)) as cliente:
        assert cliente.post("/api/lecturas", json=_payload(20.0, 50.0)).status_code == 401
        correcto = cliente.post(
            "/api/lecturas", json=_payload(20.0, 50.0), headers={"X-API-Key": "secreto"}
        )
        assert correcto.status_code == 201
