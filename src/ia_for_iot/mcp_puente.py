"""Puente entre un cliente MCP por stdio y el servidor HTTP del ESP32.

Claude Desktop inicia servidores locales por entrada y salida estandar; este
puente reenvia cada mensaje a la placa, que esta en la misma red Wi-Fi.

    mcp-puente --url http://192.168.1.60/mcp

El token opcional se lee de la variable de entorno ``MCP_TOKEN``.
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any, TextIO

from .mcp_esp32 import META_VERSION
from .simulador import url_http, url_visible

SIN_CONEXION = -32000


class Puente:
    """Reenvia mensajes JSON-RPC por HTTP."""

    def __init__(self, url: str, token: str = "", timeout: float = 15.0) -> None:
        self._url = url
        self._token = token
        self._timeout = timeout
        self._version = ""

    def _cabeceras(self, mensaje: dict[str, Any]) -> dict[str, str]:
        """Cabeceras del transporte; las modernas repiten datos del cuerpo."""
        cabeceras = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._token:
            cabeceras["Authorization"] = f"Bearer {self._token}"
        parametros = mensaje.get("params")
        parametros = parametros if isinstance(parametros, dict) else {}
        meta = parametros.get("_meta")
        version = meta.get(META_VERSION) if isinstance(meta, dict) else None
        if version:
            cabeceras["MCP-Protocol-Version"] = str(version)
            cabeceras["Mcp-Method"] = str(mensaje.get("method", ""))
            if isinstance(parametros.get("name"), str):
                cabeceras["Mcp-Name"] = parametros["name"]
        elif self._version:
            cabeceras["MCP-Protocol-Version"] = self._version
        return cabeceras

    def _error(self, mensaje: dict[str, Any], texto: str) -> dict[str, Any] | None:
        """Error para el cliente; las notificaciones no llevan respuesta."""
        if "id" not in mensaje:
            return None
        error = {"code": SIN_CONEXION, "message": texto}
        return {"jsonrpc": "2.0", "id": mensaje["id"], "error": error}

    def reenviar(self, mensaje: Any) -> dict[str, Any] | None:
        """Envia un mensaje y devuelve la respuesta, si la hay."""
        if not isinstance(mensaje, dict):
            error = {"code": -32600, "message": "Peticion JSON-RPC invalida"}
            return {"jsonrpc": "2.0", "id": None, "error": error}
        if mensaje.get("method") == "notifications/cancelled":
            return None  # Solo existe en stdio.

        peticion = urllib.request.Request(
            self._url,
            data=json.dumps(mensaje).encode("utf-8"),
            headers=self._cabeceras(mensaje),
            method="POST",
        )
        try:
            with urllib.request.urlopen(peticion, timeout=self._timeout) as respuesta:
                cuerpo = respuesta.read()
        except urllib.error.HTTPError as error:
            cuerpo = error.read()
            if not cuerpo:
                return self._error(mensaje, f"El ESP32 respondio HTTP {error.code}")
        except (OSError, http.client.HTTPException) as error:
            return self._error(mensaje, f"No se pudo contactar al ESP32: {error}")

        if not cuerpo or "id" not in mensaje:
            return None
        try:
            datos = json.loads(cuerpo)
        except ValueError:
            return self._error(mensaje, "El ESP32 no respondio JSON")
        if mensaje.get("method") == "initialize" and "result" in datos:
            self._version = str(datos["result"].get("protocolVersion", ""))
        return datos

    def ejecutar(self, entrada: TextIO, salida: TextIO) -> None:
        """Procesa un mensaje por linea hasta que se cierre la entrada."""
        for linea in entrada:
            if not linea.strip():
                continue
            try:
                mensaje = json.loads(linea)
            except ValueError:
                error = {"code": -32700, "message": "JSON invalido"}
                respuesta: Any = {"jsonrpc": "2.0", "id": None, "error": error}
            else:
                respuesta = self.reenviar(mensaje)
            if respuesta is not None:
                salida.write(json.dumps(respuesta, ensure_ascii=False) + "\n")
                salida.flush()


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada de ``mcp-puente``."""
    parser = argparse.ArgumentParser(description="Puente MCP de stdio a HTTP.")
    parser.add_argument("--url", type=url_http, required=True, help="Direccion /mcp del ESP32.")
    args = parser.parse_args(argv)
    for flujo in (sys.stdin, sys.stdout):
        flujo.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    # stdout es del protocolo; los avisos van a stderr.
    print(f"Puente MCP hacia {url_visible(args.url)}", file=sys.stderr, flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        Puente(args.url, os.environ.get("MCP_TOKEN", "")).ejecutar(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
