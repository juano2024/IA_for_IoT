"""Receptor de webhooks de Grafana.

Imprime cada notificacion en la salida del contenedor y la guarda en
``/app/data/notificaciones.log``. Si se definen ``TELEGRAM_BOT_TOKEN`` y
``TELEGRAM_CHAT_ID``, tambien envia la alerta a Telegram; sin ellos solo
escribe el log.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

LOG_PATH = os.environ.get("NOTIFIER_LOG", "/app/data/notificaciones.log")
TELEGRAM_API = "https://api.telegram.org"
LIMITE_TEXTO = 4000  # Telegram admite 4096 caracteres por mensaje.

_cerrojo_log = threading.Lock()


def formatear_mensaje(datos: dict[str, Any]) -> str:
    """Texto legible de una notificacion de Grafana."""
    lineas: list[str] = []
    for alerta in datos.get("alerts") or []:
        etiquetas = alerta.get("labels") or {}
        notas = alerta.get("annotations") or {}
        if alerta.get("status") == "resolved":
            encabezado = "✅ RESUELTA"
        elif etiquetas.get("severity") == "critical":
            encabezado = "🔴 CRÍTICA"
        else:
            encabezado = "🟡 ADVERTENCIA"
        lineas.append(f"{encabezado}: {etiquetas.get('alertname', 'alerta')}")
        if notas.get("summary"):
            lineas.append(notas["summary"])
        if etiquetas.get("dispositivo"):
            lineas.append(f"Dispositivo: {etiquetas['dispositivo']}")
        lineas.append("")
    texto = "\n".join(lineas).strip() or str(datos.get("title") or "Alerta de Grafana")
    return texto[:LIMITE_TEXTO]


class Repetidas:
    """Evita reenviar la misma alerta en cada repeticion de Grafana."""

    def __init__(self, ventana: float, reloj: Callable[[], float] = time.monotonic) -> None:
        self._ventana = ventana
        self._reloj = reloj
        self._enviadas: dict[str, tuple[str, float]] = {}
        self._cerrojo = threading.Lock()

    def debe_enviar(self, datos: dict[str, Any]) -> bool:
        """``True`` si el estado cambio o ya paso la ventana."""
        alertas = datos.get("alerts") or []
        clave = str(datos.get("groupKey") or datos.get("title") or "")
        estado = ",".join(
            sorted(f"{a.get('fingerprint') or a.get('labels')}={a.get('status')}" for a in alertas)
        )
        ahora = self._reloj()
        with self._cerrojo:
            anterior = self._enviadas.get(clave)
            if anterior and anterior[0] == estado and ahora - anterior[1] < self._ventana:
                return False
            self._enviadas[clave] = (estado, ahora)
            return True


class Telegram:
    """Envia mensajes con la API de bots; nunca imprime el token."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        api: str = TELEGRAM_API,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = f"{api}/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._dormir = dormir

    def enviar(self, texto: str, intentos: int = 3) -> bool:
        """Envia el texto y reintenta los fallos transitorios."""
        cuerpo = urllib.parse.urlencode({"chat_id": self._chat_id, "text": texto}).encode()
        for intento in range(1, intentos + 1):
            espera = 2.0**intento
            try:
                with urllib.request.urlopen(self._url, data=cuerpo, timeout=10):
                    return True
            except urllib.error.HTTPError as error:
                detalle = error.read().decode("utf-8", errors="replace")[:200]
                print(f"Telegram respondio HTTP {error.code}: {detalle}", flush=True)
                if error.code != 429 and error.code < 500:
                    return False
                espera = _espera_telegram(detalle, espera)
            except OSError as error:
                # Solo el tipo: el texto podria incluir la direccion con el token.
                print(f"Telegram no disponible ({type(error).__name__})", flush=True)
            if intento < intentos:
                self._dormir(espera)
        return False


def _espera_telegram(detalle: str, por_defecto: float) -> float:
    """Segundos que pide Telegram al limitar los envios (``retry_after``)."""
    try:
        pedido = json.loads(detalle)["parameters"]["retry_after"]
        return min(60.0, float(pedido))
    except ValueError, KeyError, TypeError:
        return por_defecto


def crear_telegram() -> Telegram | None:
    """Canal de Telegram si hay credenciales en el entorno."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return None
    return Telegram(token, chat_id, os.environ.get("TELEGRAM_API_URL") or TELEGRAM_API)


class Notificador(ThreadingHTTPServer):
    """Servidor con el canal de Telegram y el control de repetidas."""

    daemon_threads = True

    def __init__(
        self,
        direccion: tuple[str, int],
        telegram: Telegram | None = None,
        repetidas: Repetidas | None = None,
    ) -> None:
        super().__init__(direccion, Manejador)
        self.telegram = telegram
        self.repetidas = repetidas or Repetidas(float(os.environ.get("TELEGRAM_REPETIR_S") or 300))

    def avisar(self, datos: dict[str, Any]) -> None:
        """Envia a Telegram sin demorar la respuesta a Grafana."""
        if self.telegram is None or not self.repetidas.debe_enviar(datos):
            return
        threading.Thread(
            target=self.telegram.enviar, args=(formatear_mensaje(datos),), daemon=True
        ).start()


class Manejador(BaseHTTPRequestHandler):
    """Atiende los POST que envia Grafana."""

    server: Notificador

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
        if not isinstance(datos, dict):
            datos = {"raw": cuerpo}

        estado = datos.get("status", "?")
        etiquetas = datos.get("commonLabels", {})
        titulo = datos.get("title") or etiquetas.get("alertname", "alerta")
        marca = datetime.now(UTC).isoformat()

        linea = f"{marca} [{estado}] {titulo}"
        print(linea, flush=True)

        try:
            with _cerrojo_log:
                os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
                with open(LOG_PATH, "a", encoding="utf-8") as archivo:
                    archivo.write(linea + "\n")
                    archivo.write(json.dumps(datos, ensure_ascii=False) + "\n")
        except OSError as error:  # pragma: no cover - solo si el volumen falla
            print(f"No se pudo escribir el log: {error}", flush=True)

        self.server.avisar(datos)

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
    canal = crear_telegram()
    print("Notificador escuchando en http://0.0.0.0:8080/alert", flush=True)
    print(f"Telegram: {'activo' if canal else 'desactivado (solo log)'}", flush=True)
    Notificador(("0.0.0.0", 8080), canal).serve_forever()
