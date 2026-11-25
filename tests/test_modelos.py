"""Pruebas de las metricas y del modelo de ambiente."""

from __future__ import annotations

import json
import re
from pathlib import Path
from xml.etree import ElementTree

import pytest

from ia_for_iot import metricas, modelo_ambiente
from ia_for_iot.contrato import CLASES


def test_metricas_conocidas() -> None:
    pares = [("a", "a")] * 8 + [("a", "b")] * 2 + [("b", "b")] * 5 + [("b", "a")] * 5
    m = metricas.calcular(pares)
    assert m["matriz"] == [[8, 2], [5, 5]]
    assert m["exactitud"] == pytest.approx(13 / 20)
    assert m["por_clase"]["a"]["precision"] == pytest.approx(8 / 13)
    assert m["por_clase"]["a"]["sensibilidad"] == pytest.approx(0.8)
    assert m["por_clase"]["b"]["f1"] == pytest.approx(2 * (5 / 7) * 0.5 / (5 / 7 + 0.5))


def test_metricas_sin_datos_o_sin_predicciones() -> None:
    with pytest.raises(ValueError, match="no hay resultados"):
        metricas.calcular([])
    m = metricas.calcular([("a", "b"), ("a", "b")])
    assert m["por_clase"]["a"]["f1"] == 0
    assert m["f1_macro"] == 0


def test_comando_de_metricas_escribe_los_tres_archivos(tmp_path: Path) -> None:
    entrada = tmp_path / "r.csv"
    entrada.write_text(
        "real,prediccion\nvacia,vacia\nboca_abajo,boca_abajo\nboca_arriba,boca_abajo\n",
        encoding="utf-8",
    )
    salida = tmp_path / "vision"
    argumentos = ["--entrada", str(entrada), "--salida", str(salida), "--tipo", "vision"]
    assert metricas.main([*argumentos, "--titulo", "Postura <1>"]) == 0
    datos = json.loads((salida / "metricas.json").read_text(encoding="utf-8"))
    # Las clases siguen el orden del contrato.
    assert datos["clases"] == ["boca_arriba", "boca_abajo", "vacia"]
    assert "| `boca_arriba` | 0 | 1 | 0 |" in (salida / "metricas.md").read_text(encoding="utf-8")
    ElementTree.fromstring((salida / "matriz.svg").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "contenido", ["a,b\n1,2\n", "real,prediccion\nvacia,\n", "real,prediccion\n"]
)
def test_comando_de_metricas_rechaza_csv_invalido(tmp_path: Path, contenido: str) -> None:
    entrada = tmp_path / "r.csv"
    entrada.write_text(contenido, encoding="utf-8")
    with pytest.raises(SystemExit, match="No se pudo evaluar"):
        metricas.main(["--entrada", str(entrada), "--salida", str(tmp_path / "s")])


@pytest.fixture(scope="module")
def red() -> modelo_ambiente.Red:
    return modelo_ambiente.entrenar(modelo_ambiente.datos_sinteticos(600), epocas=60)


def test_modelo_aprende_los_casos_claros(red: modelo_ambiente.Red) -> None:
    assert red.clasificar(20, 50)[0] == "confort"
    assert red.clasificar(32, 50)[0] == "riesgo"
    assert red.clasificar(20, 24)[0] == "riesgo"
    probabilidades = red.probabilidades(23, 55)
    assert sum(probabilidades) == pytest.approx(1)


def test_cabecera_c_contiene_los_mismos_pesos(red: modelo_ambiente.Red) -> None:
    texto = modelo_ambiente.a_cabecera(red, "sinteticos", 0.9)
    texto.encode("ascii")
    numeros = [float(n) for n in re.findall(r"(-?\d+\.\d{6})f", texto)]
    esperados = [v for matriz in (red.oculta, red.salida) for fila in matriz for v in fila]
    assert numeros == pytest.approx(esperados, abs=1e-6)
    assert texto.count("{") == texto.count("}")
    for clase in CLASES["clima"]:
        assert f'"{clase}"' in texto


def test_datos_reales_se_validan(tmp_path: Path) -> None:
    ruta = tmp_path / "d.csv"
    ruta.write_text("temperatura_c,humedad_pct,clase\n20,50,otra\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="fila 2"):
        modelo_ambiente.main(["--datos", str(ruta)])
    ruta.write_text("temperatura_c,humedad_pct,clase\n20,50,confort\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="al menos 10"):
        modelo_ambiente.main(["--datos", str(ruta)])


def test_division_conserva_las_clases() -> None:
    muestras = modelo_ambiente.datos_sinteticos(400)
    entrenamiento, prueba = modelo_ambiente.dividir(muestras)
    assert len(entrenamiento) + len(prueba) == 400
    assert {m[2] for m in prueba} == set(CLASES["clima"])
