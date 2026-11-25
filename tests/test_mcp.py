"""Pruebas del servidor MCP simulado y del puente stdio."""

from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from ia_for_iot.mcp_esp32 import (
    HERRAMIENTAS,
    META_CAPACIDADES,
    META_VERSION,
    VERSION_MODERNA,
    EstadoCuna,
    ServidorHttp,
    ServidorMcp,
)
from ia_for_iot.mcp_puente import Puente

CLIMA = {"clase": "confort", "confianza": 0.93, "temperatura_c": 21.0, "humedad_pct": 45.0}
ABAJO = {"clase": "boca_abajo", "confianza": 0.91}
QUIETO = {"clase": "quieto", "confianza": 0.9}


class Reloj:
    ahora = 0.0

    def __call__(self) -> float:
        return self.ahora


@pytest.fixture()
def reloj() -> Reloj:
    return Reloj()


@pytest.fixture()
def estado(reloj: Reloj) -> EstadoCuna:
    cuna = EstadoCuna(reloj)
    cuna.actualizar("clima", {"dispositivo": "x", **CLIMA})
    cuna.actualizar("movimiento", {"dispositivo": "x", **QUIETO})
    cuna.actualizar("vision", {"dispositivo": "y", **ABAJO})
    return cuna


@pytest.fixture()
def mcp(estado: EstadoCuna) -> ServidorMcp:
    return ServidorMcp(estado)


@pytest.fixture()
def servidor(mcp: ServidorMcp) -> Iterator[ServidorHttp]:
    instancia = ServidorHttp(("127.0.0.1", 0), mcp)
    threading.Thread(target=instancia.serve_forever, daemon=True).start()
    yield instancia
    instancia.shutdown()
    instancia.server_close()


def _peticion(metodo: str, **parametros: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": metodo, "params": parametros}


def _moderna(metodo: str, version: str = VERSION_MODERNA, **parametros: Any) -> dict[str, Any]:
    meta = {META_VERSION: version, META_CAPACIDADES: {}}
    return _peticion(metodo, _meta=meta, **parametros)


def _url(servidor: ServidorHttp, ruta: str = "/mcp") -> str:
    return f"http://127.0.0.1:{servidor.server_port}{ruta}"


def _post(
    servidor: ServidorHttp, cuerpo: bytes, cabeceras: dict[str, str] | None = None
) -> tuple[int, bytes]:
    peticion = urllib.request.Request(_url(servidor), data=cuerpo, headers=cabeceras or {})
    try:
        with urllib.request.urlopen(peticion, timeout=5) as respuesta:
            return respuesta.status, respuesta.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


# ------------------------------------------------------------ versiones previas
def test_initialize_negocia_version(mcp: ServidorMcp) -> None:
    _, conocida = mcp.manejar(_peticion("initialize", protocolVersion="2025-06-18"))
    assert conocida and conocida["result"]["protocolVersion"] == "2025-06-18"
    _, otra = mcp.manejar(_peticion("initialize", protocolVersion="1999-01-01"))
    assert otra and otra["result"]["protocolVersion"] == "2025-11-25"
    assert mcp.manejar({"jsonrpc": "2.0", "method": "notifications/initialized"}) == (202, None)


def test_lista_y_llamada_en_versiones_previas(mcp: ServidorMcp) -> None:
    _, lista = mcp.manejar(_peticion("tools/list"))
    assert lista and [h["name"] for h in lista["result"]["tools"]] == list(HERRAMIENTAS)
    codigo, respuesta = mcp.manejar(_peticion("tools/call", name="leer_ambiente", arguments={}))
    assert codigo == 200 and respuesta
    assert "21.0 C" in respuesta["result"]["content"][0]["text"]
    assert respuesta["result"]["structuredContent"]["clase"] == "confort"
    assert "resultType" not in respuesta["result"]
    codigo, error = mcp.manejar(_peticion("no/existe"))
    assert codigo == 200 and error and error["error"]["code"] == -32601


# ------------------------------------------------------------ version 2026-07-28
def test_discover_y_llamada_modernas(mcp: ServidorMcp) -> None:
    _, discover = mcp.manejar(_moderna("server/discover"))
    assert discover and discover["result"]["supportedVersions"] == [VERSION_MODERNA]
    assert discover["result"]["resultType"] == "complete"
    cabeceras = {
        "mcp-protocol-version": VERSION_MODERNA,
        "mcp-method": "tools/call",
        "mcp-name": "leer_postura",
    }
    codigo, respuesta = mcp.manejar(_moderna("tools/call", name="leer_postura"), cabeceras)
    assert codigo == 200 and respuesta and respuesta["result"]["isError"] is False
    assert "boca abajo" in respuesta["result"]["content"][0]["text"]


def test_errores_modernos(mcp: ServidorMcp) -> None:
    codigo, error = mcp.manejar(_moderna("tools/list", version="1900-01-01"))
    assert codigo == 400 and error and error["error"]["code"] == -32022
    assert error["error"]["data"]["supported"] == [VERSION_MODERNA]

    codigo, error = mcp.manejar(_moderna("ping"))
    assert codigo == 404 and error and error["error"]["code"] == -32601

    malas = {"mcp-protocol-version": VERSION_MODERNA, "mcp-method": "otro"}
    codigo, error = mcp.manejar(_moderna("tools/list"), malas)
    assert codigo == 400 and error and error["error"]["code"] == -32020

    codigo, error = mcp.manejar(_peticion("tools/list"), {"mcp-protocol-version": VERSION_MODERNA})
    assert codigo == 400 and error and error["error"]["code"] == -32020

    codigo, error = mcp.manejar(_peticion("tools/list", _meta={META_VERSION: VERSION_MODERNA}))
    assert codigo == 400 and error and error["error"]["code"] == -32602


# ----------------------------------------------------------------- herramientas
def test_estado_general_avisa_la_alerta(mcp: ServidorMcp) -> None:
    _, respuesta = mcp.manejar(_peticion("tools/call", name="estado_general"))
    assert respuesta
    texto = respuesta["result"]["content"][0]["text"]
    assert texto.startswith("ALERTA CRITICA.")
    assert respuesta["result"]["structuredContent"]["nivel"] == 2


def test_datos_antiguos_o_ausentes_no_se_dan_por_buenos(reloj: Reloj) -> None:
    estado = EstadoCuna(reloj)
    mcp = ServidorMcp(estado)
    _, vacio = mcp.manejar(_peticion("tools/call", name="leer_postura"))
    assert vacio and "sin datos" in vacio["result"]["content"][0]["text"]
    estado.actualizar("vision", {"dispositivo": "y", **ABAJO})
    reloj.ahora = 120
    _, respuesta = mcp.manejar(_peticion("tools/call", name="estado_general"))
    assert respuesta
    texto = respuesta["result"]["content"][0]["text"]
    assert "faltan datos recientes" in texto and "Dato antiguo" in texto
    assert respuesta["result"]["structuredContent"]["completo"] is False


def test_herramienta_desconocida(mcp: ServidorMcp) -> None:
    for mensaje in (
        _peticion("tools/call", name="apagar"),
        _moderna("tools/call", name="apagar"),
    ):
        _, error = mcp.manejar(mensaje)
        assert error and error["error"]["code"] == -32602


@pytest.mark.parametrize(
    "mensaje", [[], "x", {"method": "x"}, {"jsonrpc": "2.0", "id": True, "method": "ping"}]
)
def test_mensajes_mal_formados(mcp: ServidorMcp, mensaje: Any) -> None:
    codigo, error = mcp.manejar(mensaje)
    assert codigo == 400 and error and error["error"]["code"] == -32600


# ------------------------------------------------------------------------- HTTP
def test_http_acepta_y_rechaza(servidor: ServidorHttp) -> None:
    ping = json.dumps(_peticion("ping")).encode()
    assert _post(servidor, ping)[0] == 200
    assert _post(servidor, ping, {"Origin": "http://localhost:3000"})[0] == 200
    assert _post(servidor, ping, {"Origin": "https://sitio-malo.com"})[0] == 403
    assert _post(servidor, b"no es json")[0] == 400
    assert _post(servidor, b"x" * 5000)[0] == 413
    aviso = b'{"jsonrpc":"2.0","method":"notifications/initialized"}'
    assert _post(servidor, aviso) == (202, b"")
    with pytest.raises(urllib.error.HTTPError) as get:
        urllib.request.urlopen(_url(servidor), timeout=5)
    assert get.value.code == 405


def test_http_con_token(mcp: ServidorMcp) -> None:
    servidor = ServidorHttp(("127.0.0.1", 0), mcp, token="abc")
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    try:
        ping = json.dumps(_peticion("ping")).encode()
        assert _post(servidor, ping)[0] == 401
        assert _post(servidor, ping, {"Authorization": "Bearer otro"})[0] == 401
        assert _post(servidor, ping, {"Authorization": "Bearer abc"})[0] == 200
    finally:
        servidor.shutdown()
        servidor.server_close()


# ----------------------------------------------------------------------- puente
def test_puente_reenvia_las_dos_generaciones(servidor: ServidorHttp) -> None:
    mensajes = [
        _peticion("initialize", protocolVersion="2025-11-25"),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {**_peticion("tools/list"), "id": "b"},
        {**_moderna("tools/call", name="leer_movimiento"), "id": "c"},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
    ]
    entrada = io.StringIO("\n".join(json.dumps(m) for m in mensajes) + "\n\nroto\n")
    salida = io.StringIO()
    Puente(_url(servidor)).ejecutar(entrada, salida)
    respuestas = [json.loads(linea) for linea in salida.getvalue().splitlines()]
    assert [r["id"] for r in respuestas] == [1, "b", "c", None]
    assert "quieto" in respuestas[2]["result"]["content"][0]["text"]
    assert respuestas[3]["error"]["code"] == -32700


def test_puente_informa_si_la_placa_no_responde() -> None:
    puente = Puente("http://127.0.0.1:1/mcp", timeout=1)
    respuesta = puente.reenviar(_peticion("tools/list"))
    assert respuesta and "No se pudo contactar" in respuesta["error"]["message"]
    assert puente.reenviar({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
