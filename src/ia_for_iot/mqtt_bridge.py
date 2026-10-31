"""Puente MQTT -> InfluxDB + CSV (Practica 2).

Se suscribe a los topicos del dispositivo (``invernadero1/temperatura`` y
``invernadero1/humedad``), empareja ambas magnitudes de cada ciclo, evalua el
confort y guarda la lectura en InfluxDB (base de datos de series de tiempo) y
en el archivo CSV.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from .config import Settings, get_settings
from .service import evaluar
from .storage import append_csv

LOGGER = logging.getLogger("ia_for_iot.mqtt_bridge")

# Sufijo del topico -> campo del modelo de lectura.
MAGNITUDES = {"temperatura": "temperatura_c", "humedad": "humedad_pct"}

# Topicos que transportan ambas magnitudes en un solo JSON.
TOPICOS_JSON = {"sensores", "lectura", "json"}


@dataclass
class Emparejador:
    """Combina temperatura y humedad del mismo ciclo de medicion.

    El ESP32 publica las dos magnitudes en mensajes separados; el emparejador
    las une y descarta valores demasiado antiguos para no mezclar ciclos.
    """

    ttl: float = 60.0
    _valores: dict[str, tuple[float, float]] = field(default_factory=dict)

    def agregar(
        self, magnitud: str, valor: float, ahora: float | None = None
    ) -> dict[str, float] | None:
        """Registra una magnitud y devuelve la lectura completa cuando hay par."""
        instante = time.monotonic() if ahora is None else ahora
        self._valores[magnitud] = (valor, instante)
        if set(self._valores) != set(MAGNITUDES):
            return None

        for _, momento in self._valores.values():
            if instante - momento > self.ttl:
                self._valores.clear()
                return None

        lectura = {MAGNITUDES[nombre]: valor for nombre, (valor, _) in self._valores.items()}
        self._valores.clear()
        return lectura


def interpretar_mensaje(
    topico: str, payload: bytes, prefijo: str
) -> tuple[str, float] | tuple[str, dict[str, float]] | None:
    """Traduce un mensaje MQTT a ``(magnitud, valor)`` o ``("lectura", datos)``.

    Devuelve ``None`` si el topico no pertenece al prefijo o el cuerpo no es
    valido.
    """
    partes = topico.split("/")
    if len(partes) < 2 or partes[0] != prefijo:
        return None

    sufijo = "/".join(partes[1:])
    texto = payload.decode("utf-8", errors="replace").strip()

    if sufijo in MAGNITUDES:
        try:
            return sufijo, float(texto)
        except ValueError:
            LOGGER.warning("Valor no numerico en %s: %r", topico, texto)
            return None

    if sufijo in TOPICOS_JSON:
        try:
            datos = json.loads(texto)
            return "lectura", {
                "temperatura_c": float(datos["temperatura_c"]),
                "humedad_pct": float(datos["humedad_pct"]),
            }
        except KeyError, TypeError, ValueError:
            LOGGER.warning("JSON invalido en %s: %r", topico, texto)
            return None

    return None


def construir_punto(
    lectura: dict[str, Any], settings: Settings, momento: datetime | None = None
) -> Point:
    """Construye el punto de InfluxDB para una lectura completa."""
    estado, alerta, _ = evaluar(lectura["temperatura_c"], lectura["humedad_pct"], settings)
    instante = momento or datetime.now(UTC)
    return (
        Point(settings.influx_measurement)
        .tag("dispositivo", str(lectura.get("dispositivo", "esp32-dht11")))
        .tag("sala", settings.mqtt_topic_prefix)
        .tag("estado", estado)
        .field("temperatura_c", float(lectura["temperatura_c"]))
        .field("humedad_pct", float(lectura["humedad_pct"]))
        .field("alerta", 1 if alerta else 0)
        .time(instante, WritePrecision.NS)
    )


def procesar_lectura(
    lectura: dict[str, Any],
    settings: Settings,
    write_api: Any | None = None,
    momento: datetime | None = None,
) -> dict[str, Any]:
    """Evalua, guarda en InfluxDB y agrega la fila al CSV.

    ``write_api`` se inyecta para poder probar sin una instancia real de
    InfluxDB (se pasa ``None`` para escribir solo el CSV).
    """
    estado, alerta, motivo = evaluar(lectura["temperatura_c"], lectura["humedad_pct"], settings)
    instante = momento or datetime.now(UTC)
    dispositivo = str(lectura.get("dispositivo", "esp32-dht11"))

    if write_api is not None:
        punto = construir_punto(lectura, settings, instante)
        write_api.write(bucket=settings.influx_bucket, record=punto)

    append_csv(
        settings.csv_path,
        {
            "timestamp": instante.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "dispositivo": dispositivo,
            "temperatura_c": float(lectura["temperatura_c"]),
            "humedad_pct": float(lectura["humedad_pct"]),
            "estado": estado,
            "alerta": alerta,
        },
    )

    LOGGER.info(
        "Lectura %s: %.2f C / %.2f %% -> %s",
        dispositivo,
        lectura["temperatura_c"],
        lectura["humedad_pct"],
        estado,
    )
    return {
        "timestamp": instante,
        "dispositivo": dispositivo,
        "temperatura_c": float(lectura["temperatura_c"]),
        "humedad_pct": float(lectura["humedad_pct"]),
        "estado": estado,
        "alerta": alerta,
        "motivo": motivo,
    }


def _crear_write_api(settings: Settings) -> tuple[InfluxDBClient | None, Any | None]:
    """Crea el cliente de InfluxDB si hay token configurado."""
    if not settings.influx_token:
        LOGGER.warning("INFLUX_TOKEN vacio: las lecturas solo se guardaran en el CSV")
        return None, None

    cliente = InfluxDBClient(
        url=settings.influx_url,
        token=settings.influx_token,
        org=settings.influx_org,
        timeout=10_000,
    )
    LOGGER.info(
        "InfluxDB configurado en %s (org=%s, bucket=%s)",
        settings.influx_url,
        settings.influx_org,
        settings.influx_bucket,
    )
    return cliente, cliente.write_api(write_options=SYNCHRONOUS)


def main() -> None:
    """Punto de entrada de ``mqtt-bridge``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    settings.ensure_directories()

    cliente_influx, write_api = _crear_write_api(settings)
    emparejador = Emparejador(ttl=settings.mqtt_pair_ttl)
    detener = threading.Event()

    def on_connect(
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        if reason_code.is_failure:
            LOGGER.error("Fallo de conexion MQTT: %s", reason_code)
            return
        topico = f"{settings.mqtt_topic_prefix}/#"
        client.subscribe(topico, qos=settings.mqtt_qos)
        LOGGER.info(
            "Conectado a MQTT %s:%s, suscrito a %s",
            settings.mqtt_host,
            settings.mqtt_port,
            topico,
        )

    def on_disconnect(
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        if not reason_code.is_failure:
            return
        LOGGER.warning("Desconectado del broker (%s); reintentando...", reason_code)

    def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        resultado = interpretar_mensaje(msg.topic, msg.payload, settings.mqtt_topic_prefix)
        if resultado is None:
            return

        tipo, valor = resultado
        if tipo == "lectura":
            lectura = dict(valor)  # type: ignore[arg-type]
        else:
            lectura = emparejador.agregar(tipo, valor)  # type: ignore[arg-type]
            if lectura is None:
                return

        lectura.setdefault("dispositivo", "esp32-dht11")
        try:
            procesar_lectura(lectura, settings, write_api)
        except Exception:
            LOGGER.exception("No se pudo registrar la lectura")

    cliente_mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=settings.mqtt_client_id)
    if settings.mqtt_username:
        cliente_mqtt.username_pw_set(settings.mqtt_username, settings.mqtt_password)
    cliente_mqtt.on_connect = on_connect
    cliente_mqtt.on_disconnect = on_disconnect
    cliente_mqtt.on_message = on_message
    cliente_mqtt.reconnect_delay_set(min_delay=1, max_delay=30)

    for intento in range(1, 31):
        try:
            cliente_mqtt.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
            break
        except OSError as error:
            LOGGER.warning("Broker no disponible (%s). Reintento %s/30", error, intento)
            time.sleep(2)
    else:
        raise SystemExit("No se pudo conectar al broker MQTT")

    cliente_mqtt.loop_start()

    def _parar(signum: int, frame: Any) -> None:
        LOGGER.info("Senal %s recibida; cerrando el puente", signum)
        detener.set()

    signal.signal(signal.SIGINT, _parar)
    signal.signal(signal.SIGTERM, _parar)

    try:
        while not detener.is_set():
            detener.wait(1)
    finally:
        cliente_mqtt.loop_stop()
        cliente_mqtt.disconnect()
        if cliente_influx is not None:
            cliente_influx.close()
        LOGGER.info("Puente detenido")


if __name__ == "__main__":
    main()
