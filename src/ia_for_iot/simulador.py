"""Simulador del ESP32: envia lecturas al servidor como si fuera el dispositivo real.

Sirve para validar y demostrar todo el flujo (API, base de datos, CSV y panel)
sin tener la placa fisica conectada.
"""

from __future__ import annotations

import argparse
import http.client
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from .config import get_settings
from .generadores import GeneradorDHT11

# Respuestas que pueden resolverse solas si se reintenta.
CODIGOS_TRANSITORIOS = {408, 425, 429, 500, 502, 503, 504}


def espera_reintento(intento: int, maximo: float = 30.0) -> float:
    """Espera creciente entre reintentos: 1, 2, 4, 8... hasta ``maximo``."""
    return min(maximo, 2.0 ** max(0, intento - 1))


def no_negativo(texto: str) -> float:
    """Tipo de argparse: numero mayor o igual que cero."""
    valor = float(texto)
    if not valor >= 0 or valor == float("inf"):
        raise argparse.ArgumentTypeError("debe ser un numero mayor o igual que 0")
    return valor


def entero_no_negativo(texto: str) -> int:
    """Tipo de argparse: entero mayor o igual que cero."""
    valor = int(texto)
    if valor < 0:
        raise argparse.ArgumentTypeError("debe ser un entero mayor o igual que 0")
    return valor


def url_http(texto: str) -> str:
    """Tipo de argparse: URL http o https con host."""
    partes = urlsplit(texto)
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise argparse.ArgumentTypeError("debe empezar por http:// o https://")
    return texto


def url_visible(url: str) -> str:
    """URL apta para imprimir: sin usuario, clave ni parametros."""
    partes = urlsplit(url)
    puerto = f":{partes.port}" if partes.port else ""
    return f"{partes.scheme}://{partes.hostname}{puerto}{partes.path}"


def agregar_opciones_lectura(parser: argparse.ArgumentParser) -> None:
    """Opciones de las lecturas, comunes a los modos HTTP y MQTT."""
    parser.add_argument(
        "--interval", type=no_negativo, default=10.0, help="Segundos entre lecturas."
    )
    parser.add_argument(
        "--count",
        type=entero_no_negativo,
        default=0,
        help="Numero de lecturas; 0 significa infinito.",
    )
    parser.add_argument(
        "--temperatura",
        type=float,
        default=None,
        help="Fija la temperatura (C) para todas las lecturas (util para probar alertas).",
    )
    parser.add_argument(
        "--humedad",
        type=float,
        default=None,
        help="Fija la humedad (%%) para todas las lecturas (util para probar alertas).",
    )
    parser.add_argument("--dispositivo", default="", help="Identificador del dispositivo simulado.")
    parser.add_argument(
        "--semilla", type=int, default=None, help="Semilla para repetir la misma secuencia."
    )
    parser.add_argument(
        "--reintentos",
        type=entero_no_negativo,
        default=4,
        help="Reintentos ante fallos transitorios, con espera creciente.",
    )


def _enviar(
    url: str, payload: dict[str, Any], api_key: str, timeout: float = 10.0
) -> tuple[int, str]:
    """Envia la lectura al servidor y devuelve el codigo HTTP y el cuerpo."""
    datos = json.dumps(payload).encode("utf-8")
    peticion = urllib.request.Request(url, data=datos, method="POST")
    peticion.add_header("Content-Type", "application/json")
    if api_key:
        peticion.add_header("X-API-Key", api_key)
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            return respuesta.status, respuesta.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")


def enviar_con_reintentos(
    url: str,
    payload: dict[str, Any],
    api_key: str = "",
    *,
    reintentos: int = 4,
    timeout: float = 10.0,
    dormir: Callable[[float], None] = time.sleep,
) -> tuple[int, str]:
    """Envia una lectura y reintenta los fallos transitorios.

    Devuelve el codigo 0 si no hubo respuesta del servidor. Los errores 4xx
    (clave invalida, datos fuera de limites) no se reintentan.
    """
    for intento in range(1, reintentos + 2):
        try:
            codigo, cuerpo = _enviar(url, payload, api_key, timeout)
        except (OSError, http.client.HTTPException) as error:
            codigo, cuerpo = 0, str(getattr(error, "reason", error))

        if codigo != 0 and codigo not in CODIGOS_TRANSITORIOS:
            return codigo, cuerpo
        if intento <= reintentos:
            espera = espera_reintento(intento)
            motivo = f"HTTP {codigo}" if codigo else f"sin conexion ({cuerpo})"
            print(
                f"  {motivo}; reintento {intento}/{reintentos} en {espera:g} s",
                flush=True,
            )
            dormir(espera)
    return codigo, cuerpo


def crear_parser() -> argparse.ArgumentParser:
    """Opciones de ``simular-esp32``."""
    parser = argparse.ArgumentParser(description="Simulador de lecturas DHT11 (ESP32).")
    parser.add_argument(
        "--url",
        type=url_http,
        default="http://127.0.0.1:8000/api/lecturas",
        help="URL del endpoint de recepcion (por defecto, el servidor local).",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Valor de la cabecera X-API-Key (por defecto, DEVICE_API_KEY del entorno o .env).",
    )
    parser.add_argument(
        "--timeout", type=no_negativo, default=10.0, help="Segundos de espera por respuesta."
    )
    agregar_opciones_lectura(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada de ``simular-esp32``."""
    args = crear_parser().parse_args(argv)
    settings = get_settings()
    api_key = settings.device_api_key if args.api_key is None else args.api_key
    generador = GeneradorDHT11(
        settings,
        dispositivo=args.dispositivo or "esp32-dht11",
        temperatura=args.temperatura,
        humedad=args.humedad,
        semilla=args.semilla,
    )

    print(f"Simulador ESP32 -> {url_visible(args.url)} (intervalo {args.interval:g}s)", flush=True)
    intentadas = aceptadas = 0
    try:
        while args.count == 0 or intentadas < args.count:
            payload = generador.siguiente()
            codigo, cuerpo = enviar_con_reintentos(
                args.url,
                payload,
                api_key,
                reintentos=args.reintentos,
                timeout=args.timeout,
            )
            intentadas += 1
            marca = datetime.now(UTC).strftime("%H:%M:%S")
            resultado = f"HTTP {codigo}" if codigo else "ERROR sin conexion"
            print(f"[{marca}] {payload} -> {resultado}: {cuerpo[:160]}", flush=True)
            if codigo in (200, 201):
                aceptadas += 1
            elif codigo in (401, 403):
                print("  Revisa --api-key o DEVICE_API_KEY.", flush=True)
            if args.count == 0 or intentadas < args.count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nDetenido por el usuario.", flush=True)

    print(f"Simulador finalizado: {aceptadas} de {intentadas} lecturas aceptadas.", flush=True)
    return 1 if intentadas and not aceptadas else 0


if __name__ == "__main__":
    raise SystemExit(main())
