"""Pruebas de la Lambda de la Practica 3 (sin conexion a AWS)."""

from __future__ import annotations

import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

RUTA = Path(__file__).resolve().parents[1] / "aws" / "lambda" / "api.py"


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    especificacion = importlib.util.spec_from_file_location("aws_api", RUTA)
    assert especificacion and especificacion.loader
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    monkeypatch.setenv("API_TOKEN", "token-de-prueba")
    return modulo


def _evento(metodo: str, ruta: str, cuerpo: Any = None, cabeceras: dict[str, str] | None = None):
    return {
        "rawPath": ruta,
        "requestContext": {"http": {"method": metodo}},
        "headers": cabeceras or {},
        "queryStringParameters": None,
        "body": json.dumps(cuerpo) if cuerpo is not None else None,
    }


TOKEN = {"X-Api-Token": "token-de-prueba"}


def test_evaluar_rangos_del_cuarto_del_bebe(api: ModuleType) -> None:
    assert api.evaluar(20, 50) == "OK"
    assert api.evaluar(24, 50) == "ALERTA"  # calor
    assert api.evaluar(16, 50) == "ALERTA"  # frio
    assert api.evaluar(20, 70) == "ALERTA"  # humedad alta
    assert api.evaluar(20, 30) == "ALERTA"  # humedad baja


@pytest.mark.parametrize(
    ("temperatura", "esperado"),
    [
        (20, None),
        (22, None),
        (23.5, "ventilador"),
        (25, "ventilador"),
        (25.1, "aire"),
        (30, "aire"),
        (17.9, "calentador"),
        (10, "calentador"),
    ],
)
def test_actuador_sugerido_segun_temperatura(
    api: ModuleType, temperatura: float, esperado: str | None
) -> None:
    assert api.actuador_sugerido(temperatura) == esperado


def test_normalizar_convierte_decimal_y_sugiere_actuador(api: ModuleType) -> None:
    fila = api._normalizar(
        {
            "dispositivo": "esp32-dht11",
            "ts": Decimal("1767225600000"),
            "temperatura_c": Decimal("24.5"),
            "humedad_pct": Decimal("55"),
        }
    )
    assert fila["timestamp"] == "2026-01-01T00:00:00Z"
    assert fila["estado"] == "ALERTA"
    assert fila["temperatura_c"] == 24.5
    assert fila["actuador_sugerido"] == "ventilador"


def test_csv_tiene_cabecera_y_filas(api: ModuleType) -> None:
    texto = api.a_csv(
        [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "dispositivo": "esp32-dht11",
                "temperatura_c": 20.5,
                "humedad_pct": 50.0,
                "estado": "OK",
                "actuador_sugerido": "ninguno",
            }
        ]
    )
    lineas = texto.strip().split("\n")
    assert lineas[0] == "timestamp,dispositivo,temperatura_c,humedad_pct,estado,actuador_sugerido"
    assert lineas[1] == "2026-01-01T00:00:00Z,esp32-dht11,20.5,50.0,OK,ninguno"


def test_comando_sin_token_devuelve_401(api: ModuleType) -> None:
    respuesta = api.handler(_evento("POST", "/api/actuador", {"actuador": "aire", "estado": "ON"}))
    assert respuesta["statusCode"] == 401


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"actuador": "microondas", "estado": "ON"},
        {"actuador": "aire", "estado": "TAL VEZ"},
        {"estado": "ON"},
        {},
    ],
)
def test_comando_invalido_devuelve_422(api: ModuleType, cuerpo: dict[str, str]) -> None:
    respuesta = api.handler(_evento("POST", "/api/actuador", cuerpo, TOKEN))
    assert respuesta["statusCode"] == 422


@pytest.mark.parametrize(
    ("cuerpo", "mensaje"),
    [
        ({"actuador": "calentador", "estado": "on"}, {"actuador": "calentador", "estado": "ON"}),
        ({"actuador": "Aire", "estado": "OFF"}, {"actuador": "aire", "estado": "OFF"}),
        ({"modo": "auto"}, {"modo": "AUTO"}),
    ],
)
def test_comando_publica_mensaje_mqtt(
    api: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    cuerpo: dict[str, str],
    mensaje: dict[str, str],
) -> None:
    enviados: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(api, "_publicar", lambda topic, payload: enviados.append((topic, payload)))
    respuesta = api.handler(_evento("POST", "/api/actuador", cuerpo, TOKEN))
    assert respuesta["statusCode"] == 202
    assert enviados == [("cuartobebe/comandos", mensaje)]


def test_ruta_desconocida_devuelve_404(api: ModuleType) -> None:
    assert api.handler(_evento("GET", "/api/otra"))["statusCode"] == 404
