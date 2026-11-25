"""Copia en Python del servidor MCP que corre en el ESP32 (Actividad 4).

Responde igual que ``firmware/esp32_edge_ai/servidor_mcp.h`` para poder probar
los clientes (VS Code, Claude) sin la placa. Toma los datos del broker MQTT.

    mcp-esp32-simulado --host 127.0.0.1 --http-port 8090

Atiende dos generaciones del protocolo: la 2026-07-28, sin estado y con la
version en ``_meta``, y las anteriores, que empiezan con ``initialize``.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import threading
import time
from collections.abc import Callable, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import paho.mqtt.client as mqtt

from .config import get_settings
from .contrato import NIVELES, SIN_MODELO, TIPOS, ContratoInvalidoError, decodificar
from .publicador_mqtt import agregar_opciones_mqtt
from .simulador import entero_no_negativo

VERSION_MODERNA = "2026-07-28"
VERSIONES_ANTERIORES = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CAPACIDADES = "io.modelcontextprotocol/clientCapabilities"
META_SERVIDOR = "io.modelcontextprotocol/serverInfo"
SERVIDOR = {"name": "cuarto-bebe-esp32", "version": "1.0.0"}
INSTRUCCIONES = (
    "Estado del cuarto del bebe medido por un ESP32: ambiente, movimiento de la cuna y "
    "postura. Es un prototipo academico y no reemplaza la supervision de un adulto."
)
MAXIMO_CUERPO = 4096
MAXIMO_DESCARTE = 65_536
# Un dato mas viejo que esto (segundos) se informa como no vigente.
VIGENCIA_S = 60

HERRAMIENTAS: dict[str, tuple[str, str]] = {
    "leer_ambiente": ("Leer ambiente", "Temperatura, humedad y clase del ambiente del cuarto."),
    "leer_movimiento": ("Leer movimiento", "Ultima clase de movimiento de la cuna."),
    "leer_postura": ("Leer postura", "Ultima postura del bebe detectada por la camara."),
    "estado_general": ("Estado general", "Resumen de todo y si hay alguna alerta."),
}
TIPO_DE_HERRAMIENTA = {
    "leer_ambiente": "clima",
    "leer_movimiento": "movimiento",
    "leer_postura": "vision",
}
NOMBRES = {"clima": "Ambiente", "movimiento": "Movimiento", "vision": "Postura"}

PARSE_ERROR = -32700
PETICION_INVALIDA = -32600
METODO_NO_ENCONTRADO = -32601
PARAMETROS_INVALIDOS = -32602
CABECERA_DISTINTA = -32020
VERSION_NO_SOPORTADA = -32022


class EstadoCuna:
    """Ultima prediccion de cada tipo."""

    def __init__(self, reloj: Callable[[], float] = time.monotonic) -> None:
        self._reloj = reloj
        self._datos: dict[str, tuple[dict[str, Any], float]] = {}
        self._cerrojo = threading.Lock()

    def actualizar(self, tipo: str, prediccion: dict[str, Any]) -> None:
        """Guarda la prediccion mas reciente."""
        with self._cerrojo:
            self._datos[tipo] = (prediccion, self._reloj())

    def leer(self, tipo: str) -> dict[str, Any]:
        """Prediccion con su antiguedad; ``disponible`` es falso si no hay."""
        with self._cerrojo:
            guardado = self._datos.get(tipo)
            ahora = self._reloj()
        if guardado is None:
            return {"disponible": False, "vigente": False}
        prediccion, momento = guardado
        edad = round(ahora - momento)
        clase = prediccion["clase"]
        return {
            "disponible": True,
            "vigente": edad <= VIGENCIA_S,
            "hace_s": edad,
            "nivel": 0 if clase == SIN_MODELO else NIVELES[tipo][clase],
            **{k: v for k, v in prediccion.items() if k != "dispositivo"},
        }


def _describir(tipo: str, dato: dict[str, Any]) -> str:
    """Frase corta con el estado de un tipo."""
    nombre = NOMBRES[tipo]
    if not dato["disponible"]:
        return f"{nombre}: sin datos."
    partes = [f"{nombre}: {dato['clase'].replace('_', ' ')}"]
    if tipo == "clima":
        partes.append(f"{dato['temperatura_c']:.1f} C, {dato['humedad_pct']:.0f} % de humedad")
    partes.append(f"confianza {dato['confianza']:.0%}, hace {dato['hace_s']} s")
    texto = "; ".join(partes) + "."
    if not dato["vigente"]:
        texto += " Dato antiguo: el dispositivo dejo de publicar."
    return texto


class ServidorMcp:
    """Logica del protocolo, independiente de HTTP."""

    def __init__(self, estado: EstadoCuna) -> None:
        self._estado = estado

    def _llamar(self, parametros: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta una herramienta; todas son de solo lectura y sin argumentos."""
        nombre = parametros.get("name")
        if nombre == "estado_general":
            datos = {tipo: self._estado.leer(tipo) for tipo in TIPOS}
            # Un dato antiguo no puede confirmar que todo este bien.
            nivel = max(
                (d["nivel"] for d in datos.values() if d["disponible"] and d["vigente"]), default=0
            )
            completo = all(d["disponible"] and d["vigente"] for d in datos.values())
            resumen = {0: "Sin alertas", 1: "ADVERTENCIA", 2: "ALERTA CRITICA"}[nivel]
            if not completo:
                resumen += " (faltan datos recientes)"
            texto = "\n".join([f"{resumen}.", *(_describir(t, d) for t, d in datos.items())])
            contenido = {"nivel": nivel, "completo": completo, **datos}
        else:
            tipo = TIPO_DE_HERRAMIENTA[nombre]
            contenido = self._estado.leer(tipo)
            texto = _describir(tipo, contenido)
        return {
            "content": [{"type": "text", "text": texto}],
            "structuredContent": contenido,
            "isError": False,
        }

    @staticmethod
    def _herramientas() -> list[dict[str, Any]]:
        return [
            {
                "name": nombre,
                "title": titulo,
                "description": descripcion,
                "inputSchema": {"type": "object", "additionalProperties": False},
                "annotations": {"readOnlyHint": True},
            }
            for nombre, (titulo, descripcion) in HERRAMIENTAS.items()
        ]

    def manejar(
        self, mensaje: Any, cabeceras: dict[str, str] | None = None
    ) -> tuple[int, dict[str, Any] | None]:
        """Procesa un mensaje JSON-RPC y devuelve codigo HTTP y respuesta."""
        if (
            not isinstance(mensaje, dict)
            or mensaje.get("jsonrpc") != "2.0"
            or not isinstance(mensaje.get("method"), str)
        ):
            return 400, _error(None, PETICION_INVALIDA, "Peticion JSON-RPC invalida")

        metodo = mensaje["method"]
        if "id" not in mensaje:
            return 202, None
        identificador = mensaje["id"]
        if isinstance(identificador, bool) or not isinstance(identificador, str | int):
            return 400, _error(None, PETICION_INVALIDA, "id invalido")

        parametros = mensaje.get("params") or {}
        if not isinstance(parametros, dict):
            return 400, _error(identificador, PARAMETROS_INVALIDOS, "params debe ser un objeto")

        meta = parametros.get("_meta")
        version = meta.get(META_VERSION) if isinstance(meta, dict) else None
        if version is None and (cabeceras or {}).get("mcp-protocol-version") == VERSION_MODERNA:
            return 400, _error(
                identificador, CABECERA_DISTINTA, "La version de la cabecera no esta en _meta"
            )
        if version is not None:
            return self._moderno(identificador, metodo, parametros, version, cabeceras)
        return self._anterior(identificador, metodo, parametros)

    def _moderno(
        self,
        identificador: str | int,
        metodo: str,
        parametros: dict[str, Any],
        version: Any,
        cabeceras: dict[str, str] | None,
    ) -> tuple[int, dict[str, Any]]:
        """Protocolo 2026-07-28: sin estado y con cabeceras que repiten el cuerpo."""
        if cabeceras is not None:
            esperadas = {"mcp-protocol-version": version, "mcp-method": metodo}
            if metodo == "tools/call":
                esperadas["mcp-name"] = parametros.get("name")
            for nombre, valor in esperadas.items():
                if cabeceras.get(nombre) != valor:
                    return 400, _error(
                        identificador, CABECERA_DISTINTA, f"La cabecera {nombre} no coincide"
                    )
        if version != VERSION_MODERNA:
            datos = {"supported": [VERSION_MODERNA], "requested": version}
            return 400, _error(
                identificador, VERSION_NO_SOPORTADA, "Unsupported protocol version", datos
            )

        if not isinstance(parametros["_meta"].get(META_CAPACIDADES), dict):
            return 400, _error(
                identificador, PARAMETROS_INVALIDOS, "Falta clientCapabilities en _meta"
            )

        cache = {"ttlMs": 300_000, "cacheScope": "public"}
        if metodo == "server/discover":
            resultado: dict[str, Any] = {
                "supportedVersions": [VERSION_MODERNA],
                "capabilities": {"tools": {}},
                "instructions": INSTRUCCIONES,
                **cache,
            }
        elif metodo == "tools/list":
            resultado = {"tools": self._herramientas(), **cache}
        elif metodo == "tools/call":
            if parametros.get("name") not in HERRAMIENTAS:
                return 200, _error(identificador, PARAMETROS_INVALIDOS, "Herramienta desconocida")
            resultado = self._llamar(parametros)
        else:
            return 404, _error(identificador, METODO_NO_ENCONTRADO, "Method not found")
        resultado = {
            "resultType": "complete",
            **resultado,
            "_meta": {META_SERVIDOR: SERVIDOR},
        }
        return 200, {"jsonrpc": "2.0", "id": identificador, "result": resultado}

    def _anterior(
        self, identificador: str | int, metodo: str, parametros: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        """Versiones con ``initialize``; no usa sesiones."""
        if metodo == "initialize":
            pedida = parametros.get("protocolVersion")
            resultado: dict[str, Any] = {
                "protocolVersion": pedida
                if pedida in VERSIONES_ANTERIORES
                else VERSIONES_ANTERIORES[0],
                "capabilities": {"tools": {}},
                "serverInfo": SERVIDOR,
                "instructions": INSTRUCCIONES,
            }
        elif metodo == "ping":
            resultado = {}
        elif metodo == "tools/list":
            resultado = {"tools": self._herramientas()}
        elif metodo == "tools/call":
            if parametros.get("name") not in HERRAMIENTAS:
                return 200, _error(identificador, PARAMETROS_INVALIDOS, "Herramienta desconocida")
            resultado = self._llamar(parametros)
        else:
            return 200, _error(identificador, METODO_NO_ENCONTRADO, "Method not found")
        return 200, {"jsonrpc": "2.0", "id": identificador, "result": resultado}


def _error(
    identificador: str | int | None, codigo: int, mensaje: str, datos: Any = None
) -> dict[str, Any]:
    """Respuesta de error JSON-RPC."""
    error: dict[str, Any] = {"code": codigo, "message": mensaje}
    if datos is not None:
        error["data"] = datos
    return {"jsonrpc": "2.0", "id": identificador, "error": error}


def origen_permitido(origen: str | None) -> bool:
    """Evita que una pagina web use el servidor (DNS rebinding)."""
    if not origen:
        return True
    return urlsplit(origen).hostname in ("localhost", "127.0.0.1", "::1")


class _Manejador(BaseHTTPRequestHandler):
    """Punto de acceso ``/mcp``."""

    server: ServidorHttp
    protocol_version = "HTTP/1.1"

    def _responder(self, codigo: int, cuerpo: dict[str, Any] | None = None) -> None:
        datos = b"" if cuerpo is None else json.dumps(cuerpo, ensure_ascii=False).encode()
        self.send_response(codigo)
        if datos:
            self.send_header("Content-Type", "application/json")
        if codigo == 405:
            self.send_header("Allow", "POST")
        self.send_header("Content-Length", str(len(datos)))
        if codigo >= 400:
            # El cuerpo puede no haberse leido completo: se cierra la conexion.
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(datos)

    def do_POST(self) -> None:
        try:
            largo = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._responder(411)
        # Se lee antes de responder: si no, el sistema corta la conexion y el
        # cliente no llega a ver el codigo de error.
        cuerpo = self.rfile.read(min(max(largo, 0), MAXIMO_DESCARTE))
        if urlsplit(self.path).path != "/mcp":
            return self._responder(404)
        if not origen_permitido(self.headers.get("Origin")):
            return self._responder(403)
        if not self._autorizado():
            return self._responder(401)
        if not 0 <= largo <= MAXIMO_CUERPO:
            return self._responder(413)
        try:
            mensaje = json.loads(cuerpo)
        except ValueError:
            return self._responder(400, _error(None, PARSE_ERROR, "JSON invalido"))
        cabeceras = {nombre.lower(): valor for nombre, valor in self.headers.items()}
        return self._responder(*self.server.mcp.manejar(mensaje, cabeceras))

    def _autorizado(self) -> bool:
        """Comprueba ``Authorization: Bearer`` si hay token configurado."""
        if not self.server.token:
            return True
        recibido = self.headers.get("Authorization", "").encode()
        return hmac.compare_digest(recibido, f"Bearer {self.server.token}".encode())

    def _no_permitido(self) -> None:
        self._responder(405 if urlsplit(self.path).path == "/mcp" else 404)

    def do_GET(self) -> None:
        self._no_permitido()

    def do_DELETE(self) -> None:
        self._no_permitido()

    def log_message(self, formato: str, *args: object) -> None:
        """Silencia el log por defecto de http.server."""


class ServidorHttp(ThreadingHTTPServer):
    """Servidor HTTP del protocolo."""

    daemon_threads = True

    def __init__(self, direccion: tuple[str, int], mcp: ServidorMcp, token: str = "") -> None:
        self.mcp = mcp
        self.token = token
        super().__init__(direccion, _Manejador)


def suscribir(args: argparse.Namespace, estado: EstadoCuna) -> mqtt.Client:
    """Mantiene el estado al dia con los mensajes del broker."""
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mcp-esp32-simulado")
    clave = get_settings().mqtt_password if args.password is None else args.password
    if args.user:
        cliente.username_pw_set(args.user, clave)

    def on_connect(c: mqtt.Client, u: Any, f: Any, codigo: Any, p: Any) -> None:
        if codigo.is_failure:
            print(f"ERROR: el broker rechazo la conexion: {codigo}", flush=True)
            return
        c.subscribe([(f"{args.prefix}/{tipo}", 1) for tipo in TIPOS])
        print("Suscrito a las predicciones del broker", flush=True)

    def on_message(c: mqtt.Client, u: Any, mensaje: mqtt.MQTTMessage) -> None:
        tipo = mensaje.topic.rsplit("/", 1)[-1]
        try:
            estado.actualizar(tipo, decodificar(tipo, mensaje.payload))
        except ContratoInvalidoError as error:
            print(f"Mensaje descartado en {mensaje.topic}: {error}", flush=True)

    cliente.on_connect = on_connect
    cliente.on_message = on_message
    cliente.reconnect_delay_set(min_delay=1, max_delay=30)
    cliente.connect_async(args.host, args.port or 1883, keepalive=60)
    cliente.loop_start()
    return cliente


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada de ``mcp-esp32-simulado``."""
    parser = argparse.ArgumentParser(description="Servidor MCP simulado del ESP32.")
    agregar_opciones_mqtt(parser)
    parser.add_argument("--http-port", type=entero_no_negativo, default=8090)
    parser.add_argument(
        "--http-host", default="127.0.0.1", help="Interfaz del servidor (local por defecto)."
    )
    args = parser.parse_args(argv)
    if args.tls:
        parser.error("El servidor simulado solo se conecta al broker local, sin TLS.")

    estado = EstadoCuna()
    servidor = ServidorHttp(
        (args.http_host, args.http_port), ServidorMcp(estado), os.environ.get("MCP_TOKEN", "")
    )
    cliente = suscribir(args, estado)
    print(f"Servidor MCP en http://{args.http_host}:{args.http_port}/mcp", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nDetenido por el usuario.", flush=True)
    finally:
        cliente.disconnect()
        cliente.loop_stop()
        servidor.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
