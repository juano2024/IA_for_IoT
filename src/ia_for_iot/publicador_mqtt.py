"""Simulador MQTT del ESP32 + DHT11.

Publica en el broker las mismas magnitudes que el dispositivo real, con
excursiones ocasionales fuera de rango para poder demostrar las alertas.

    simular-esp32-mqtt --host localhost --prefix invernadero1 --count 20
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from typing import Any

import paho.mqtt.client as mqtt

from .simulador import generar_lectura


def main() -> None:
    """Punto de entrada de ``simular-esp32-mqtt``."""
    parser = argparse.ArgumentParser(description="Simulador MQTT de lecturas DHT11.")
    parser.add_argument("--host", default="127.0.0.1", help="Host del broker MQTT.")
    parser.add_argument("--port", type=int, default=1883, help="Puerto del broker.")
    parser.add_argument("--user", default="", help="Usuario MQTT (opcional).")
    parser.add_argument("--password", default="", help="Contrasena MQTT (opcional).")
    parser.add_argument("--prefix", default="invernadero1", help="Prefijo de los topicos.")
    parser.add_argument(
        "--count", type=int, default=0, help="Numero de lecturas; 0 significa infinito."
    )
    parser.add_argument("--interval", type=float, default=10.0, help="Segundos entre lecturas.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Publicar un unico topico JSON (<prefijo>/sensores) en vez de dos topicos.",
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
    args = parser.parse_args()

    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="simulador-esp32")
    if args.user:
        cliente.username_pw_set(args.user, args.password)

    conectado = threading.Event()
    rechazo: dict[str, str] = {}

    def on_connect(
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        if reason_code.is_failure:
            rechazo["motivo"] = str(reason_code)
            print(f"ERROR: el broker rechazo la conexion: {reason_code}", flush=True)
            return
        conectado.set()
        print("Conectado al broker MQTT", flush=True)

    cliente.on_connect = on_connect

    cliente.connect(args.host, args.port, keepalive=60)
    cliente.loop_start()

    if not conectado.wait(timeout=10):
        cliente.loop_stop()
        cliente.disconnect()
        motivo = rechazo.get("motivo", "sin respuesta")
        raise SystemExit(
            f"No se pudo autenticar con el broker ({motivo}). Usa --user y --password."
        )

    topico_temperatura = f"{args.prefix}/temperatura"
    topico_humedad = f"{args.prefix}/humedad"
    topico_json = f"{args.prefix}/sensores"

    print(f"Simulador MQTT -> {args.host}:{args.port} (prefijo {args.prefix})")
    enviados = 0
    try:
        while args.count == 0 or enviados < args.count:
            lectura = generar_lectura(enviados)
            if args.temperatura is not None:
                lectura["temperatura_c"] = args.temperatura
            if args.humedad is not None:
                lectura["humedad_pct"] = args.humedad
            temperatura = lectura["temperatura_c"]
            humedad = lectura["humedad_pct"]

            if args.json:
                cuerpo = json.dumps(
                    {
                        "dispositivo": lectura["dispositivo"],
                        "temperatura_c": temperatura,
                        "humedad_pct": humedad,
                    }
                )
                cliente.publish(topico_json, cuerpo, qos=1)
                print(f"[{enviados + 1}] {topico_json} -> {cuerpo}")
            else:
                cliente.publish(topico_temperatura, f"{temperatura:.2f}", qos=1)
                cliente.publish(topico_humedad, f"{humedad:.2f}", qos=1)
                print(
                    f"[{enviados + 1}] {topico_temperatura}={temperatura:.2f} "
                    f"{topico_humedad}={humedad:.2f}"
                )

            enviados += 1
            if args.count == 0 or enviados < args.count:
                time.sleep(args.interval)
    finally:
        time.sleep(0.3)
        cliente.loop_stop()
        cliente.disconnect()

    print(f"Simulador finalizado: {enviados} lecturas publicadas.")


if __name__ == "__main__":
    main()
