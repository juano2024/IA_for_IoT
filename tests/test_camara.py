"""Pruebas de la ESP32-CAM simulada y de la captura del dataset."""

from __future__ import annotations

import csv
import random
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ia_for_iot import camara_simulada, capturar_dataset
from ia_for_iot.contrato import CLASES

JPEG = b"\xff\xd8datos\xff\xd9"


@pytest.fixture()
def servidor() -> Iterator[camara_simulada.ServidorCamara]:
    camara = camara_simulada.Camara()
    camara.mostrar(JPEG)
    instancia = camara_simulada.ServidorCamara(("127.0.0.1", 0), camara)
    threading.Thread(target=instancia.serve_forever, daemon=True).start()
    yield instancia
    instancia.cerrar()


def _url(servidor: camara_simulada.ServidorCamara, ruta: str) -> str:
    return f"http://127.0.0.1:{servidor.server_port}{ruta}"


def test_muestras_incluyen_todas_las_clases() -> None:
    dataset = camara_simulada.cargar_dataset(camara_simulada.MUESTRAS)
    assert set(dataset) == set(CLASES["vision"])
    for rutas in dataset.values():
        assert capturar_dataset.es_jpeg(rutas[0].read_bytes())


def test_dataset_con_formato_de_roboflow(tmp_path: Path) -> None:
    for ruta in ("train/Boca Abajo/1.jpg", "valid/boca-abajo/2.JPEG", "test/vacia/3.jpg"):
        archivo = tmp_path / ruta
        archivo.parent.mkdir(parents=True)
        archivo.write_bytes(JPEG)
    (tmp_path / "train" / "README.roboflow.txt").write_text("x")
    (tmp_path / "train" / "Boca Abajo" / "nota.png").write_bytes(b"x")
    dataset = camara_simulada.cargar_dataset(tmp_path)
    assert {clase: len(rutas) for clase, rutas in dataset.items()} == {"boca_abajo": 2, "vacia": 1}


def test_capture_y_stream_entregan_jpeg(servidor: camara_simulada.ServidorCamara) -> None:
    with urllib.request.urlopen(_url(servidor, "/capture"), timeout=5) as respuesta:
        assert respuesta.headers["Content-Type"] == "image/jpeg"
        assert respuesta.read() == JPEG
    with urllib.request.urlopen(_url(servidor, "/stream"), timeout=5) as respuesta:
        assert "multipart/x-mixed-replace" in respuesta.headers["Content-Type"]
        assert JPEG in respuesta.read(90)
    with urllib.request.urlopen(_url(servidor, "/"), timeout=5) as respuesta:
        assert b"/stream" in respuesta.read()
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(_url(servidor, "/otra"), timeout=5)


def test_precision_simulada() -> None:
    azar = random.Random(1)
    seguras = [camara_simulada.simular_prediccion("vacia", 1.0, azar) for _ in range(50)]
    assert all(clase == "vacia" and confianza >= 0.8 for clase, confianza in seguras)
    erradas = [camara_simulada.simular_prediccion("vacia", 0.0, azar)[0] for _ in range(50)]
    assert "vacia" not in erradas


def test_simulador_publica_vision_y_registra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    publicados: list[tuple[str, str]] = []

    class ConexionFalsa:
        def conectar(self, reintentos: int) -> None:
            pass

        def publicar(self, topico: str, cuerpo: str) -> bool:
            publicados.append((topico, cuerpo))
            return True

        def cerrar(self) -> None:
            pass

    monkeypatch.setattr(camara_simulada, "crear_conexion", lambda *a: ConexionFalsa())
    registro = tmp_path / "registro.csv"
    codigo = camara_simulada.main(
        [
            "--prefix",
            "cuartobebe",
            "--clase",
            "boca_abajo",
            "--count",
            "3",
            "--interval",
            "0",
            "--http-port",
            "0",
            "--registro",
            str(registro),
        ]
    )
    assert codigo == 0
    assert [topico for topico, _ in publicados] == ["cuartobebe/vision"] * 3
    assert all('"clase": "boca_abajo"' in cuerpo for _, cuerpo in publicados)
    with registro.open(encoding="utf-8", newline="") as archivo:
        filas = list(csv.DictReader(archivo))
    assert len(filas) == 3 and filas[0]["real"] == "boca_abajo"


def test_simulador_rechaza_carpetas_sin_imagenes(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="No hay JPEG"):
        camara_simulada.main(["--carpeta", str(tmp_path), "--sin-mqtt", "--http-port", "0"])
    with pytest.raises(SystemExit, match="No existe"):
        camara_simulada.main(["--carpeta", str(tmp_path / "x"), "--sin-mqtt"])


def test_captura_guarda_fotos_etiquetadas(
    servidor: camara_simulada.ServidorCamara, tmp_path: Path
) -> None:
    codigo = capturar_dataset.main(
        [
            "--url",
            _url(servidor, "/capture"),
            "--clase",
            "de_lado",
            "--cantidad",
            "2",
            "--intervalo",
            "0",
            "--salida",
            str(tmp_path),
            "--sesion",
            "dia1",
        ]
    )
    assert codigo == 0
    nombres = sorted(ruta.name for ruta in (tmp_path / "de_lado").iterdir())
    assert nombres == ["dia1_001.jpg", "dia1_002.jpg"]


def test_captura_descarta_respuestas_que_no_son_jpeg(
    servidor: camara_simulada.ServidorCamara, tmp_path: Path, capsys: Any
) -> None:
    servidor.camara.mostrar(b"\xff\xd8cortada")
    codigo = capturar_dataset.main(
        [
            "--url",
            _url(servidor, "/capture"),
            "--clase",
            "vacia",
            "--cantidad",
            "1",
            "--salida",
            str(tmp_path),
            "--reintentos",
            "0",
        ]
    )
    assert codigo == 1
    assert list((tmp_path / "vacia").iterdir()) == []
    assert "Foto omitida" in capsys.readouterr().out
