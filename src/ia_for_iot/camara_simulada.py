"""Simulador de la ESP32-CAM de la Actividad 4.

Recorre imagenes JPEG organizadas en una carpeta por clase (por ejemplo, la
exportacion "Folder Structure" de Roboflow), publica la clase en
``<prefijo>/vision`` y sirve ``/capture`` y ``/stream`` como la placa real.

    simular-esp32cam --clase boca_abajo --count 5
    simular-esp32cam --carpeta dataset --precision 0.9 --registro predicciones_vision.csv

No ejecuta ningun modelo: la clase publicada sale de la carpeta de la imagen y
``--precision`` decide cuantas veces acierta. Sirve para probar la plataforma,
no como resultado del modelo. Sin ``--carpeta`` usa dibujos de muestra.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import threading
import time
import unicodedata
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .contrato import CLASES
from .generadores import GeneradorClases
from .publicador_mqtt import agregar_opciones_mqtt, crear_conexion
from .simulador import entero_no_negativo, no_negativo

MUESTRAS = Path(__file__).parent / "static" / "camara"
PAGINA = b"""<!doctype html><html lang="es"><meta charset="utf-8"><title>ESP32-CAM simulada</title>
<body style="background:#111;color:#eee;font-family:sans-serif;text-align:center">
<h1>ESP32-CAM simulada</h1><img src="/stream" alt="Video simulado" width="640">
<p><a style="color:#8cf" href="/capture">Capturar una imagen</a></p></body></html>"""


def normalizar_clase(nombre: str) -> str:
    """Convierte ``Boca Abajo`` o ``boca-abajo`` en ``boca_abajo``."""
    sin_tildes = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    return sin_tildes.strip().lower().replace(" ", "_").replace("-", "_")


def cargar_dataset(carpeta: Path) -> dict[str, list[Path]]:
    """Agrupa los JPEG por la clase de su carpeta; ignora lo demas."""
    imagenes: dict[str, list[Path]] = {}
    for ruta in sorted(carpeta.rglob("*")):
        if ruta.suffix.lower() not in (".jpg", ".jpeg") or not ruta.is_file():
            continue
        clase = normalizar_clase(ruta.parent.name)
        if clase in CLASES["vision"]:
            imagenes.setdefault(clase, []).append(ruta)
    return imagenes


class Camara:
    """Imagen actual de la camara simulada."""

    def __init__(self) -> None:
        self._imagen = b""
        self._cambio = threading.Condition()

    def mostrar(self, imagen: bytes) -> None:
        """Cambia la imagen y avisa a quienes miran el video."""
        with self._cambio:
            self._imagen = imagen
            self._cambio.notify_all()

    def imagen(self, espera: float | None = None) -> bytes:
        """Imagen actual; con ``espera`` aguarda a que cambie ese tiempo."""
        with self._cambio:
            if espera is not None:
                self._cambio.wait(espera)
            return self._imagen


class _Manejador(BaseHTTPRequestHandler):
    """Mismas rutas que el modo de captura de la ESP32-CAM."""

    server: ServidorCamara

    def _responder(self, tipo: str, cuerpo: bytes, codigo: int = 200) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self) -> None:
        ruta = self.path.split("?", 1)[0]
        if ruta == "/":
            self._responder("text/html; charset=utf-8", PAGINA)
        elif ruta == "/capture":
            self._responder("image/jpeg", self.server.camara.imagen())
        elif ruta == "/stream":
            self._transmitir()
        else:
            self._responder("text/plain", b"no encontrado", 404)

    def _transmitir(self) -> None:
        """Video MJPEG, el formato que entrega la ESP32-CAM."""
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        imagen = self.server.camara.imagen()
        try:
            while not self.server.detener.is_set():
                self.wfile.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(imagen)).encode()
                    + b"\r\n\r\n"
                    + imagen
                    + b"\r\n"
                )
                imagen = self.server.camara.imagen(espera=0.5)
        except OSError:
            return  # El navegador cerro el video.

    def log_message(self, formato: str, *args: object) -> None:
        """Silencia el log por defecto de http.server."""


class ServidorCamara(ThreadingHTTPServer):
    """Servidor HTTP de la camara simulada."""

    daemon_threads = True

    def __init__(self, direccion: tuple[str, int], camara: Camara) -> None:
        self.camara = camara
        self.detener = threading.Event()
        super().__init__(direccion, _Manejador)

    def cerrar(self) -> None:
        """Termina los videos abiertos y el servidor."""
        self.detener.set()
        self.shutdown()
        self.server_close()


def simular_prediccion(real: str, precision: float, azar: random.Random) -> tuple[str, float]:
    """Clase y confianza que publicaria un modelo con esa precision."""
    if azar.random() < precision:
        return real, round(azar.uniform(0.82, 0.99), 2)
    otras = [clase for clase in CLASES["vision"] if clase != real]
    return azar.choice(otras), round(azar.uniform(0.45, 0.85), 2)


def _precision(texto: str) -> float:
    """Tipo de argparse: numero entre 0 y 1."""
    valor = float(texto)
    if not 0.0 <= valor <= 1.0:
        raise argparse.ArgumentTypeError("debe estar entre 0 y 1")
    return valor


def crear_parser() -> argparse.ArgumentParser:
    """Opciones de ``simular-esp32cam``."""
    parser = argparse.ArgumentParser(description="Simulador de la ESP32-CAM.")
    agregar_opciones_mqtt(parser)
    parser.add_argument(
        "--carpeta", type=Path, default=MUESTRAS, help="Imagenes JPEG en una carpeta por clase."
    )
    parser.add_argument(
        "--clase", choices=CLASES["vision"], default=None, help="Fija la clase mostrada."
    )
    parser.add_argument(
        "--precision", type=_precision, default=1.0, help="Aciertos simulados del modelo (0 a 1)."
    )
    parser.add_argument(
        "--registro",
        type=Path,
        default=None,
        help="CSV con la clase real y la simulada, para probar metricas-modelo.",
    )
    parser.add_argument(
        "--interval", type=no_negativo, default=3.0, help="Segundos entre imagenes."
    )
    parser.add_argument(
        "--count", type=entero_no_negativo, default=0, help="Numero de imagenes; 0 es infinito."
    )
    parser.add_argument("--semilla", type=int, default=None, help="Repite la misma secuencia.")
    parser.add_argument("--dispositivo", default="esp32-cam-sim", help="Identificador simulado.")
    parser.add_argument(
        "--reintentos", type=entero_no_negativo, default=4, help="Reintentos de conexion."
    )
    parser.add_argument(
        "--http-port", type=entero_no_negativo, default=8081, help="Puerto web; 0 lo desactiva."
    )
    parser.add_argument(
        "--http-host", default="127.0.0.1", help="Interfaz del servidor web (local por defecto)."
    )
    parser.add_argument(
        "--sin-mqtt", action="store_true", help="Solo sirve las imagenes, sin publicar."
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada de ``simular-esp32cam``."""
    args = crear_parser().parse_args(argv)
    if not args.carpeta.is_dir():
        raise SystemExit(f"No existe la carpeta: {args.carpeta}")
    dataset = cargar_dataset(args.carpeta)
    if not dataset:
        raise SystemExit(
            f"No hay JPEG en carpetas por clase ({', '.join(CLASES['vision'])}) en {args.carpeta}"
        )
    if args.clase and args.clase not in dataset:
        raise SystemExit(f"La carpeta no tiene imagenes de {args.clase}.")

    azar = random.Random(args.semilla)
    clases = GeneradorClases(
        "vision", clase=args.clase, semilla=args.semilla, clases=tuple(dataset)
    )
    print(
        "Dataset: " + ", ".join(f"{clase}={len(rutas)}" for clase, rutas in dataset.items()),
        flush=True,
    )

    camara = Camara()
    camara.mostrar(next(iter(dataset.values()))[0].read_bytes())
    servidor = None
    conexion = None
    registro = None
    publicadas = entregadas = 0
    try:
        if args.http_port:
            try:
                servidor = ServidorCamara((args.http_host, args.http_port), camara)
            except OSError as error:
                raise SystemExit(f"No se pudo abrir el puerto {args.http_port}: {error}") from error
            threading.Thread(target=servidor.serve_forever, daemon=True).start()
            print(f"Camara en http://{args.http_host}:{args.http_port}/", flush=True)
        if not args.sin_mqtt:
            conexion = crear_conexion(args, "simulador-esp32cam")
            conexion.conectar(args.reintentos)
        if args.registro:
            args.registro.parent.mkdir(parents=True, exist_ok=True)
            registro = args.registro.open("w", newline="", encoding="utf-8")
            escritor: Any = csv.writer(registro)
            escritor.writerow(["real", "prediccion", "confianza"])

        while args.count == 0 or publicadas < args.count:
            real = clases.siguiente()
            ruta = azar.choice(dataset[real])
            camara.mostrar(ruta.read_bytes())
            clase, confianza = simular_prediccion(real, args.precision, azar)
            entregado = True
            if conexion is not None:
                cuerpo = {"dispositivo": args.dispositivo, "clase": clase, "confianza": confianza}
                entregado = conexion.publicar(f"{args.prefix}/vision", json.dumps(cuerpo))
            publicadas += 1
            entregadas += entregado
            if registro is not None:
                escritor.writerow([real, clase, confianza])
                registro.flush()
            print(f"{ruta.parent.name}/{ruta.name} -> {clase} ({confianza})", flush=True)
            if args.count == 0 or publicadas < args.count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nDetenido por el usuario.", flush=True)
    finally:
        if registro is not None:
            registro.close()
        if conexion is not None:
            conexion.cerrar()
        if servidor is not None:
            servidor.cerrar()

    print(f"Simulador finalizado: {entregadas} de {publicadas} imagenes.", flush=True)
    return 1 if publicadas and not entregadas else 0


if __name__ == "__main__":
    raise SystemExit(main())
