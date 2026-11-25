"""Pruebas del notificador: log y envio a Telegram."""

from __future__ import annotations

import importlib.util
import json
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

RUTA = Path(__file__).parents[1] / "mqtt" / "notifier" / "notifier.py"
TOKEN = "123456:token-secreto"

ALERTA = {
    "status": "firing",
    "groupKey": "grupo-1",
    "title": "[FIRING:1] Bebe boca abajo",
    "alerts": [
        {
            "status": "firing",
            "fingerprint": "abc",
            "labels": {
                "alertname": "Bebe boca abajo",
                "severity": "critical",
                "dispositivo": "esp32-cam",
            },
            "annotations": {"summary": "La camara detecto la postura boca abajo"},
        }
    ],
}


@pytest.fixture()
def notifier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("NOTIFIER_LOG", str(tmp_path / "log" / "notificaciones.log"))
    spec = importlib.util.spec_from_file_location("notifier_prueba", RUTA)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class TelegramFalso(HTTPServer):
    """Imita la API de bots y guarda lo recibido."""

    def __init__(self, codigos: list[int]) -> None:
        super().__init__(("127.0.0.1", 0), _Bot)
        self.codigos = codigos
        self.recibidos: list[tuple[str, dict[str, list[str]]]] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"


class _Bot(BaseHTTPRequestHandler):
    server: TelegramFalso

    def do_POST(self) -> None:
        cuerpo = self.rfile.read(int(self.headers["Content-Length"])).decode()
        self.server.recibidos.append((self.path, urllib.parse.parse_qs(cuerpo)))
        codigos = self.server.codigos
        self.send_response(codigos.pop(0) if len(codigos) > 1 else codigos[0])
        self.end_headers()
        self.wfile.write(b'{"ok":false,"parameters":{"retry_after":7}}')

    def log_message(self, formato: str, *args: object) -> None:
        pass


@pytest.fixture()
def bot() -> Iterator[TelegramFalso]:
    instancia = TelegramFalso([200])
    threading.Thread(target=instancia.serve_forever, daemon=True).start()
    yield instancia
    instancia.shutdown()
    instancia.server_close()


def _esperar(condicion: Any, segundos: float = 5.0) -> bool:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.02)
    return False


def test_mensaje_legible(notifier: ModuleType) -> None:
    texto = notifier.formatear_mensaje(ALERTA)
    assert texto.splitlines() == [
        "🔴 CRÍTICA: Bebe boca abajo",
        "La camara detecto la postura boca abajo",
        "Dispositivo: esp32-cam",
    ]
    assert notifier.formatear_mensaje({"title": "Prueba"}) == "Prueba"
    resuelta = {"alerts": [{**ALERTA["alerts"][0], "status": "resolved"}]}
    assert notifier.formatear_mensaje(resuelta).startswith("✅ RESUELTA")


def test_envia_a_telegram(notifier: ModuleType, bot: TelegramFalso) -> None:
    assert notifier.Telegram(TOKEN, "-100", bot.url).enviar("hola ñ")
    ruta, cuerpo = bot.recibidos[0]
    assert ruta == f"/bot{TOKEN}/sendMessage"
    assert cuerpo == {"chat_id": ["-100"], "text": ["hola ñ"]}


def test_respeta_el_limite_de_telegram(notifier: ModuleType, bot: TelegramFalso) -> None:
    bot.codigos = [429, 200]
    esperas: list[float] = []
    assert notifier.Telegram(TOKEN, "1", bot.url, esperas.append).enviar("x")
    assert esperas == [7.0]


def test_errores_no_muestran_el_token(
    notifier: ModuleType, bot: TelegramFalso, capsys: Any
) -> None:
    bot.codigos = [401]
    assert not notifier.Telegram(TOKEN, "1", bot.url).enviar("x")
    assert len(bot.recibidos) == 1
    caido = notifier.Telegram(TOKEN, "1", "http://127.0.0.1:1", lambda _: None)
    assert not caido.enviar("x", intentos=2)
    salida = capsys.readouterr().out
    assert "HTTP 401" in salida and "no disponible" in salida
    assert "token-secreto" not in salida


def test_no_repite_la_misma_alerta(notifier: ModuleType) -> None:
    ahora = [0.0]
    repetidas = notifier.Repetidas(300, lambda: ahora[0])
    assert repetidas.debe_enviar(ALERTA)
    ahora[0] = 60
    assert not repetidas.debe_enviar(ALERTA)
    resuelta = {**ALERTA, "alerts": [{**ALERTA["alerts"][0], "status": "resolved"}]}
    assert repetidas.debe_enviar(resuelta)
    assert repetidas.debe_enviar(ALERTA)
    ahora[0] = 400
    assert repetidas.debe_enviar(ALERTA)


def test_webhook_escribe_log_y_avisa(
    notifier: ModuleType, bot: TelegramFalso, tmp_path: Path
) -> None:
    servidor = notifier.Notificador(
        ("127.0.0.1", 0), notifier.Telegram(TOKEN, "1", bot.url), notifier.Repetidas(300)
    )
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{servidor.server_port}/alert"
    try:
        for cuerpo in (json.dumps(ALERTA).encode(), json.dumps(ALERTA).encode(), b"[1]", b"roto"):
            with urllib.request.urlopen(url, data=cuerpo, timeout=5) as respuesta:
                assert respuesta.status == 200
        assert _esperar(lambda: len(bot.recibidos) >= 2)
    finally:
        servidor.shutdown()
        servidor.server_close()
    textos = [cuerpo["text"][0] for _, cuerpo in bot.recibidos]
    assert sum("boca abajo" in texto for texto in textos) == 1
    log = (tmp_path / "log" / "notificaciones.log").read_text(encoding="utf-8")
    assert log.count("[firing]") == 2


def test_sin_credenciales_solo_log(notifier: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notifier.crear_telegram() is None
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    assert notifier.crear_telegram() is None
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    assert notifier.crear_telegram() is not None
