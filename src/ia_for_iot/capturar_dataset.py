"""Descarga fotos de la ESP32-CAM (o del simulador) para armar el dataset.

    capturar-dataset --url http://192.168.1.50/capture --clase boca_abajo --cantidad 50

Guarda ``dataset/<clase>/<sesion>_<numero>.jpg``. Cambia de sesion cada vez que
cambien la luz, la ropa o la cobija: asi se puede dejar una sesion completa
para la prueba y la exactitud no sale inflada.
"""

from __future__ import annotations

import argparse
import http.client
import re
import time
import urllib.request
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from .contrato import CLASES
from .simulador import entero_no_negativo, espera_reintento, no_negativo, url_http, url_visible

MAXIMO_BYTES = 2_000_000


def es_jpeg(datos: bytes) -> bool:
    """Comprueba que la imagen este completa (marcas de inicio y fin)."""
    return datos[:2] == b"\xff\xd8" and datos.rstrip(b"\x00")[-2:] == b"\xff\xd9"


def descargar(url: str, reintentos: int = 3, timeout: float = 10.0) -> bytes | None:
    """Descarga una foto; ``None`` si la camara no respondio con un JPEG."""
    for intento in range(1, reintentos + 2):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as respuesta:
                datos = respuesta.read(MAXIMO_BYTES + 1)
            if len(datos) <= MAXIMO_BYTES and es_jpeg(datos):
                return datos
            motivo = "la respuesta no es un JPEG completo"
        except (OSError, http.client.HTTPException) as error:
            motivo = str(getattr(error, "reason", error))
        if intento <= reintentos:
            print(f"  {motivo}; reintento {intento}/{reintentos}", flush=True)
            time.sleep(espera_reintento(intento, maximo=5.0))
    print(f"  Foto omitida: {motivo}", flush=True)
    return None


def _sesion(texto: str) -> str:
    """Tipo de argparse: nombre apto para un archivo."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", texto):
        raise argparse.ArgumentTypeError("usa solo letras, numeros, guion y guion bajo")
    return texto


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada de ``capturar-dataset``."""
    parser = argparse.ArgumentParser(description="Captura fotos etiquetadas de la ESP32-CAM.")
    parser.add_argument("--url", type=url_http, required=True, help="Direccion de /capture.")
    parser.add_argument("--clase", choices=CLASES["vision"], required=True)
    parser.add_argument("--cantidad", type=entero_no_negativo, default=50)
    parser.add_argument("--intervalo", type=no_negativo, default=1.0, help="Segundos entre fotos.")
    parser.add_argument("--salida", type=Path, default=Path("dataset"))
    parser.add_argument("--sesion", type=_sesion, default=datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--reintentos", type=entero_no_negativo, default=3)
    args = parser.parse_args(argv)

    destino = args.salida / args.clase
    destino.mkdir(parents=True, exist_ok=True)
    print(f"Capturando {args.cantidad} fotos de {args.clase} desde {url_visible(args.url)}")
    guardadas = 0
    try:
        for numero in range(1, args.cantidad + 1):
            datos = descargar(args.url, args.reintentos)
            if datos is not None:
                ruta = destino / f"{args.sesion}_{numero:03d}.jpg"
                ruta.write_bytes(datos)
                guardadas += 1
                print(f"[{numero}/{args.cantidad}] {ruta}", flush=True)
            if numero < args.cantidad:
                time.sleep(args.intervalo)
    except KeyboardInterrupt:
        print("\nDetenido por el usuario.", flush=True)
    print(f"{guardadas} fotos guardadas en {destino}", flush=True)
    return 0 if guardadas else 1


if __name__ == "__main__":
    raise SystemExit(main())
