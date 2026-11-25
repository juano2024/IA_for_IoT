"""El firmware de la Actividad 4 debe usar el mismo contrato que Python."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ia_for_iot import mcp_esp32
from ia_for_iot.contrato import CLASES, NIVELES, SIN_MODELO

FIRMWARE = Path(__file__).parents[1] / "firmware"
MCP = (FIRMWARE / "esp32_edge_ai" / "servidor_mcp.h").read_text(encoding="utf-8")
EDGE = (FIRMWARE / "esp32_edge_ai" / "esp32_edge_ai.ino").read_text(encoding="utf-8")


def test_herramientas_y_versiones_iguales() -> None:
    for nombre, (titulo, descripcion) in mcp_esp32.HERRAMIENTAS.items():
        assert f'{{"{nombre}", "{titulo}", "{descripcion}"}}' in MCP
    assert f'#define MCP_VERSION_MODERNA "{mcp_esp32.VERSION_MODERNA}"' in MCP
    for version in mcp_esp32.VERSIONES_ANTERIORES:
        assert f'"{version}"' in MCP
    assert f"#define MCP_VIGENCIA_S {mcp_esp32.VIGENCIA_S}" in MCP
    assert f"#define MCP_MAXIMO_CUERPO {mcp_esp32.MAXIMO_CUERPO}" in MCP


def test_niveles_iguales() -> None:
    funcion = re.search(r"static int mcpNivel.*?\n}", MCP, re.DOTALL)
    assert funcion
    critica, advertencia = funcion.group().split("return 2;")
    for niveles in NIVELES.values():
        for clase, nivel in niveles.items():
            assert (f'"{clase}"' in critica) == (nivel == 2)
            assert (f'"{clase}"' in advertencia) == (nivel == 1)


def test_el_esp32_acepta_todas_las_posturas() -> None:
    for clase in (*CLASES["vision"], SIN_MODELO):
        assert f'"{clase}"' in EDGE


@pytest.mark.parametrize("carpeta", ["esp32_edge_ai", "esp32cam_vision"])
def test_sin_secretos_reales(carpeta: str) -> None:
    ejemplo = (FIRMWARE / carpeta / "secrets.h.example").read_text(encoding="utf-8")
    for nombre in ("WIFI_SSID", "WIFI_PASS", "MQTT_USER", "MQTT_PASS"):
        assert re.search(rf'#define {nombre} "TU_', ejemplo)
