"""Receptor de webhooks de Grafana.

Imprime cada notificacion en la salida del contenedor y la guarda en
``/app/data/notificaciones.log``. Sirve como canal de notificacion verificable
sin depender de un servicio externo (correo, Telegram, Slack...).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG_PATH = os.environ.get("NOTIFIER_LOG", "/app/data/notificaciones.log")


class Manejador(BaseHTTPRequestHandler):
    """Atiende los POST que envia Grafana."""

    def do_POST(self) -> None:
        if self.path != "/alert":
            self.send_error(404)
            return

        largo = int(self.headers.get("Content-Length", "0"))
        cuerpo = self.rfile.read(largo).decode("utf-8", errors="replace")

        try:
            datos = json.loads(cuerpo)
        except json.JSONDecodeError:
            datos = {"raw": cuerpo}

        estado = datos.get("status", "?")
        etiquetas = datos.get("commonLabels", {})
        titulo = datos.get("title") or etiquetas.get("alertname", "alerta")
        marca = datetime.now(UTC).isoformat()

        linea = f"{marca} [{estado}] {titulo}"
        print(linea, flush=True)

        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as archivo:
                archivo.write(linea + "\n")
                archivo.write(json.dumps(datos, ensure_ascii=False) + "\n")
        except OSError as error:  # pragma: no cover - solo si el volumen falla
            print(f"No se pudo escribir el log: {error}", flush=True)

        respuesta = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(respuesta)))
        self.end_headers()
        self.wfile.write(respuesta)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_error(404)

    def log_message(self, formato: str, *args: object) -> None:
        """Silencia el log por defecto de http.server."""


if __name__ == "__main__":
    print("Notificador escuchando en http://0.0.0.0:8080/alert", flush=True)
    HTTPServer(("0.0.0.0", 8080), Manejador).serve_forever()
