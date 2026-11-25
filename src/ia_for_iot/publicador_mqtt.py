"""Simulador MQTT del ESP32.

Publica en el broker los mismos mensajes que el dispositivo real, con
excursiones ocasionales fuera de rango para poder demostrar las alertas.

    simular-esp32-mqtt --host localhost --prefix cuartobebe --count 20
    simular-esp32-mqtt --edge --clase-movimiento agitacion --count 5

Con ``--edge`` simula el ESP32 de la Actividad 4 (``clima`` y ``movimiento`` con
la clase del modelo). Con ``--aws`` usa el topico y el JSON de la Practica 3 y
exige los certificados del dispositivo; nunca se activa solo.
"""

from __future__ import annotations

import argparse
import json
import ssl
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

from .config import get_settings
from .contrato import CLASES
from .generadores import (
    GeneradorClases,
    GeneradorDHT11,
    prediccion_clima,
    prediccion_movimiento,
)
from .simulador import agregar_opciones_lectura, espera_reintento, no_negativo

# Rechazos del broker que pueden resolverse reintentando.
RECHAZOS_TRANSITORIOS = {"Server unavailable", "Server busy", "Unspecified error"}


@dataclass(frozen=True)
class OpcionesTls:
    """Archivos para la conexion TLS; solo se usan sus rutas."""

    ca: Path | None = None
    cert: Path | None = None
    key: Path | None = None


class ConexionMqtt:
    """Conexion MQTT con reintentos, reconexion y confirmacion de entrega."""

    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        usuario: str = "",
        clave: str = "",
        tls: OpcionesTls | None = None,
        *,
        cliente: mqtt.Client | None = None,
        dormir: Callable[[float], None] = time.sleep,
        espera: float = 10.0,
    ) -> None:
        self._host = host
        self._port = port
        self._dormir = dormir
        self._espera = espera
        self._listo = threading.Event()
        self._respuesta = threading.Event()
        self._rechazo = ""
        self._cerrando = False
        self._cliente = cliente or mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=client_id
        )
        if usuario:
            self._cliente.username_pw_set(usuario, clave)
        if tls is not None:
            self._cliente.tls_set(
                ca_certs=str(tls.ca) if tls.ca else None,
                certfile=str(tls.cert) if tls.cert else None,
                keyfile=str(tls.key) if tls.key else None,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
        self._cliente.on_connect = self._al_conectar
        self._cliente.on_disconnect = self._al_desconectar
        self._cliente.reconnect_delay_set(min_delay=1, max_delay=30)

    def _al_conectar(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        if reason_code.is_failure:
            self._rechazo = str(reason_code)
            print(f"ERROR: el broker rechazo la conexion: {reason_code}", flush=True)
        else:
            self._rechazo = ""
            self._listo.set()
            print("Conectado al broker MQTT", flush=True)
        self._respuesta.set()

    def _al_desconectar(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any,
    ) -> None:
        estaba_conectado = self._listo.is_set()
        self._listo.clear()
        if estaba_conectado and not self._cerrando:
            print(f"Desconectado del broker ({reason_code}); reconectando...", flush=True)

    def conectar(self, reintentos: int = 4) -> None:
        """Conecta o termina con un mensaje claro si no es posible."""
        motivo = "sin respuesta"
        for intento in range(1, reintentos + 2):
            self._respuesta.clear()
            self._rechazo = ""
            try:
                self._cliente.connect(self._host, self._port, keepalive=60)
            except ssl.SSLCertVerificationError as error:
                raise SystemExit(
                    f"No se pudo verificar el certificado del broker: {error.verify_message}"
                ) from error
            except OSError as error:
                motivo = str(error)
            else:
                self._cliente.loop_start()
                self._respuesta.wait(self._espera)
                if self._listo.is_set():
                    return
                self._cliente.loop_stop()
                self._cliente.disconnect()
                if self._rechazo and self._rechazo not in RECHAZOS_TRANSITORIOS:
                    raise SystemExit(
                        f"No se pudo autenticar con el broker ({self._rechazo}). "
                        "Revisa --user y --password."
                    )
                motivo = self._rechazo or "sin respuesta"
            if intento <= reintentos:
                espera = espera_reintento(intento)
                print(
                    f"Broker no disponible ({motivo}); "
                    f"reintento {intento}/{reintentos} en {espera:g} s",
                    flush=True,
                )
                self._dormir(espera)
        raise SystemExit(f"No se pudo conectar a {self._host}:{self._port} ({motivo}).")

    def publicar(self, topico: str, cuerpo: str) -> bool:
        """Publica con QoS 1 y confirma la entrega; ``False`` si no se pudo."""
        if not self._listo.wait(self._espera):
            print(f"Sin conexion con el broker: no se publico {topico}", flush=True)
            return False
        info = self._cliente.publish(topico, cuerpo, qos=1)
        try:
            info.wait_for_publish(self._espera)
        except RuntimeError, ValueError:
            return False
        if not info.is_published():
            print(f"El broker no confirmo {topico}", flush=True)
            return False
        return True

    def cerrar(self) -> None:
        """Cierra la conexion de forma ordenada."""
        self._cerrando = True
        self._cliente.disconnect()
        self._cliente.loop_stop()


def agregar_opciones_mqtt(parser: argparse.ArgumentParser) -> None:
    """Opciones de conexion; por defecto usan MQTT_* del entorno o .env."""
    settings = get_settings()
    parser.add_argument("--host", default=settings.mqtt_host, help="Host del broker MQTT.")
    parser.add_argument("--port", type=int, default=None, help="Puerto (1883, o 8883 con TLS).")
    parser.add_argument("--user", default=settings.mqtt_username, help="Usuario MQTT (opcional).")
    parser.add_argument("--password", default=None, help="Contrasena MQTT (o MQTT_PASSWORD).")
    parser.add_argument(
        "--prefix", default=settings.mqtt_topic_prefix, help="Prefijo de los topicos."
    )
    parser.add_argument("--client-id", default="", help="Identificador del cliente MQTT.")
    parser.add_argument("--tls", action="store_true", help="Cifra la conexion con TLS.")
    parser.add_argument("--ca", type=Path, default=None, help="Certificado de la autoridad (PEM).")
    parser.add_argument("--cert", type=Path, default=None, help="Certificado del cliente (PEM).")
    parser.add_argument("--key", type=Path, default=None, help="Clave privada del cliente (PEM).")


def crear_conexion(args: argparse.Namespace, client_id: str) -> ConexionMqtt:
    """Construye la conexion a partir de las opciones de linea de comandos."""
    tls = None
    if args.tls:
        for nombre in ("ca", "cert", "key"):
            ruta = getattr(args, nombre)
            if ruta is not None and not ruta.is_file():
                raise SystemExit(f"No existe el archivo de --{nombre}: {ruta}")
        tls = OpcionesTls(args.ca, args.cert, args.key)
    elif args.ca or args.cert or args.key:
        raise SystemExit("--ca, --cert y --key requieren --tls.")
    clave = get_settings().mqtt_password if args.password is None else args.password
    return ConexionMqtt(
        args.host,
        args.port or (8883 if tls else 1883),
        args.client_id or client_id,
        args.user,
        clave,
        tls,
    )


def mensajes_dht11(lectura: dict[str, Any], prefijo: str, formato: str) -> list[tuple[str, str]]:
    """Mensajes de una lectura en el formato ``separado``, ``json`` o ``aws``."""
    if formato == "separado":
        return [
            (f"{prefijo}/temperatura", f"{lectura['temperatura_c']:.2f}"),
            (f"{prefijo}/humedad", f"{lectura['humedad_pct']:.2f}"),
        ]
    sufijo = "telemetria" if formato == "aws" else "sensores"
    cuerpo = {campo: lectura[campo] for campo in ("dispositivo", "temperatura_c", "humedad_pct")}
    return [(f"{prefijo}/{sufijo}", json.dumps(cuerpo))]


def crear_parser() -> argparse.ArgumentParser:
    """Opciones de ``simular-esp32-mqtt``."""
    parser = argparse.ArgumentParser(description="Simulador MQTT del ESP32.")
    agregar_opciones_mqtt(parser)
    agregar_opciones_lectura(parser)
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument(
        "--json",
        action="store_true",
        help="Publicar un unico topico JSON (<prefijo>/sensores) en vez de dos topicos.",
    )
    modo.add_argument(
        "--edge",
        action="store_true",
        help="Actividad 4: publicar clima y movimiento con la clase del modelo.",
    )
    modo.add_argument(
        "--aws",
        action="store_true",
        help="Practica 3: <prefijo>/telemetria en AWS IoT Core (exige --ca, --cert y --key).",
    )
    parser.add_argument(
        "--clase-movimiento",
        choices=CLASES["movimiento"],
        default=None,
        help="Con --edge, fija la clase de movimiento.",
    )
    parser.add_argument(
        "--intervalo-movimiento",
        type=no_negativo,
        default=2.0,
        help="Con --edge, segundos entre mensajes de movimiento.",
    )
    return parser


def _ciclo_dht11(
    args: argparse.Namespace, conexion: ConexionMqtt, generador: GeneradorDHT11
) -> bool:
    """Publica una lectura del DHT11."""
    formato = "aws" if args.aws else "json" if args.json else "separado"
    mensajes = mensajes_dht11(generador.siguiente(), args.prefix, formato)
    entregado = all([conexion.publicar(topico, cuerpo) for topico, cuerpo in mensajes])
    if entregado:
        print("  ".join(f"{topico}={cuerpo}" for topico, cuerpo in mensajes), flush=True)
    return entregado


def _veces_movimiento(args: argparse.Namespace) -> int:
    """Mensajes de movimiento que caben en un intervalo de lectura."""
    if not args.edge or args.intervalo_movimiento <= 0:
        return 1
    return max(1, round(args.interval / args.intervalo_movimiento))


def _ciclo_edge(
    args: argparse.Namespace,
    conexion: ConexionMqtt,
    generador: GeneradorDHT11,
    clima: GeneradorClases,
    movimiento: GeneradorClases,
    dispositivo: str,
) -> bool:
    """Publica un mensaje de clima y los de movimiento del mismo intervalo."""
    datos = prediccion_clima(generador.siguiente(), get_settings(), clima)
    entregado = conexion.publicar(f"{args.prefix}/clima", json.dumps(datos))
    print(
        f"clima={datos['clase']} ({datos['temperatura_c']} C, {datos['humedad_pct']} %)",
        flush=True,
    )
    veces = _veces_movimiento(args)
    for indice in range(veces):
        datos = prediccion_movimiento(dispositivo, movimiento)
        entregado &= conexion.publicar(f"{args.prefix}/movimiento", json.dumps(datos))
        print(f"  movimiento={datos['clase']} (confianza {datos['confianza']})", flush=True)
        if indice < veces - 1:
            time.sleep(args.interval / veces)
    return entregado


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada de ``simular-esp32-mqtt``."""
    parser = crear_parser()
    args = parser.parse_args(argv)
    if args.aws:
        args.tls = True
        if not (args.ca and args.cert and args.key):
            parser.error("--aws exige --ca, --cert y --key del dispositivo.")

    settings = get_settings()
    dispositivo = args.dispositivo or ("esp32-cuna-sim" if args.edge else "esp32-dht11")
    generador = GeneradorDHT11(
        settings,
        dispositivo=dispositivo,
        temperatura=args.temperatura,
        humedad=args.humedad,
        semilla=args.semilla,
    )
    clima = GeneradorClases("clima", semilla=args.semilla)
    movimiento = GeneradorClases(
        "movimiento", clase=args.clase_movimiento, semilla=args.semilla, pesos=(6, 3, 1)
    )

    # La politica de la Practica 3 solo acepta el client id de la placa.
    conexion = crear_conexion(args, "esp32-dht11" if args.aws else "simulador-esp32")
    hechas = entregadas = 0
    try:
        conexion.conectar(args.reintentos)
        print(f"Simulador MQTT -> {args.host} (prefijo {args.prefix})", flush=True)
        while args.count == 0 or hechas < args.count:
            if args.edge:
                entregado = _ciclo_edge(args, conexion, generador, clima, movimiento, dispositivo)
            else:
                entregado = _ciclo_dht11(args, conexion, generador)
            hechas += 1
            entregadas += entregado
            if args.count == 0 or hechas < args.count:
                # En modo edge el ciclo ya consumio el resto del intervalo.
                time.sleep(args.interval / _veces_movimiento(args))
    except KeyboardInterrupt:
        print("\nDetenido por el usuario.", flush=True)
    finally:
        conexion.cerrar()

    print(f"Simulador finalizado: {entregadas} de {hechas} lecturas publicadas.", flush=True)
    return 1 if hechas and not entregadas else 0


if __name__ == "__main__":
    raise SystemExit(main())
