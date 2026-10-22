"""Simulador del ESP32: envia lecturas al servidor como si fuera el dispositivo real.

Sirve para validar y demostrar todo el flujo (API, base de datos, CSV y panel)
sin tener la placa fisica conectada.
"""

from __future__ import annotations

import argparse
import json
import random
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any


def _generar_lectura(dev_index: int) -> dict[str, Any]:
    """Genera una lectura plausible, con excursiones ocasionales fuera de rango."""
    base_t = 21.0
    base_h = 50.0
    # Un desplazamiento lento que hace que la sala se caliente o se enfoque.
    deriva = 2.5 * ((dev_index % 12) / 12)
    temperatura = base_t + deriva + random.uniform(-0.8, 0.8)
    humedad = base_h - random.uniform(-5.0, 5.0) + deriva * 1.5
    if random.random() < 0.12:
        temperatura += random.choice([-3.5, 3.5])
    if random.random() < 0.12:
        humedad += random.choice([-15.0, 15.0])
    return {
        "dispositivo": "esp32-dht11",
        "temperatura_c": round(temperatura, 2),
        "humedad_pct": round(min(100.0, max(0.0, humedad)), 2),
    }


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


def main() -> None:
    """Punto de entrada de ``simular-esp32``."""
    parser = argparse.ArgumentParser(description="Simulador de lecturas DHT11 (ESP32).")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000/api/lecturas",
        help="URL del endpoint de recepción (por defecto, el servidor local).",
    )
    parser.add_argument("--interval", type=float, default=10.0, help="Segundos entre lecturas.")
    parser.add_argument(
        "--count", type=int, default=0, help="Numero de lecturas; 0 significa infinito."
    )
    parser.add_argument("--api-key", default="", help="Valor de la cabecera X-API-Key.")
    args = parser.parse_args()

    print(f"Simulador ESP32 -> {args.url} (intervalo {args.interval}s)")
    enviados = 0
    while args.count == 0 or enviados < args.count:
        payload = _generar_lectura(enviados)
        codigo, cuerpo = _enviar(args.url, payload, args.api_key)
        marca = datetime.now(UTC).strftime("%H:%M:%S")
        if codigo in (200, 201):
            print(f"[{marca}] {payload} -> HTTP {codigo}")
        else:
            print(f"[{marca}] {payload} -> HTTP {codigo}: {cuerpo[:160]}")
        enviados += 1
        if args.count == 0 or enviados < args.count:
            time.sleep(args.interval)

    print(f"Simulador finalizado: {enviados} lecturas enviadas.")


if __name__ == "__main__":
    main()
