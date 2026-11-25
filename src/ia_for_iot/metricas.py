"""Matriz de confusion y metricas de un modelo de clasificacion.

Lee un CSV con las columnas ``real`` y ``prediccion`` (por ejemplo, los
resultados de prueba de Edge Impulse) y escribe un reporte en Markdown, un JSON
y la matriz como SVG.

    metricas-modelo --entrada resultados.csv --salida modelos/vision --titulo "Postura"
"""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Sequence
from html import escape
from pathlib import Path
from typing import Any

from .contrato import CLASES


def calcular(pares: Sequence[tuple[str, str]], clases: Sequence[str] = ()) -> dict[str, Any]:
    """Metricas a partir de pares ``(real, prediccion)``."""
    if not pares:
        raise ValueError("no hay resultados para evaluar")
    presentes = {clase for par in pares for clase in par}
    orden = [clase for clase in clases if clase in presentes]
    orden += sorted(presentes - set(orden))
    indice = {clase: i for i, clase in enumerate(orden)}
    matriz = [[0] * len(orden) for _ in orden]
    for real, prediccion in pares:
        matriz[indice[real]][indice[prediccion]] += 1

    por_clase: dict[str, dict[str, float]] = {}
    for clase, i in indice.items():
        aciertos = matriz[i][i]
        soporte = sum(matriz[i])
        predichas = sum(fila[i] for fila in matriz)
        precision = aciertos / predichas if predichas else 0.0
        sensibilidad = aciertos / soporte if soporte else 0.0
        suma = precision + sensibilidad
        por_clase[clase] = {
            "precision": precision,
            "sensibilidad": sensibilidad,
            "f1": 2 * precision * sensibilidad / suma if suma else 0.0,
            "soporte": soporte,
        }
    con_datos = [valores for valores in por_clase.values() if valores["soporte"]]
    return {
        "muestras": len(pares),
        "exactitud": sum(matriz[i][i] for i in range(len(orden))) / len(pares),
        "f1_macro": sum(valores["f1"] for valores in con_datos) / len(con_datos),
        "clases": orden,
        "matriz": matriz,
        "por_clase": por_clase,
    }


def leer_resultados(
    ruta: Path, columna_real: str = "real", columna_prediccion: str = "prediccion"
) -> list[tuple[str, str]]:
    """Lee los pares del CSV; avisa si faltan columnas o valores."""
    with ruta.open(encoding="utf-8-sig", newline="") as archivo:
        lector = csv.DictReader(archivo)
        faltan = {columna_real, columna_prediccion} - set(lector.fieldnames or [])
        if faltan:
            raise ValueError(f"faltan columnas en el CSV: {', '.join(sorted(faltan))}")
        pares = [
            ((fila[columna_real] or "").strip(), (fila[columna_prediccion] or "").strip())
            for fila in lector
        ]
    for numero, par in enumerate(pares, start=2):
        if not all(par):
            raise ValueError(f"la fila {numero} tiene una clase vacia")
    return pares


def a_markdown(metricas: dict[str, Any], titulo: str) -> str:
    """Reporte listo para pegar en la entrega."""
    clases = metricas["clases"]
    lineas = [
        f"## {titulo}",
        "",
        f"- Muestras de prueba: {metricas['muestras']}",
        f"- Exactitud: {metricas['exactitud']:.1%}",
        f"- F1 macro: {metricas['f1_macro']:.3f}",
        "",
        "Matriz de confusión (filas: clase real; columnas: predicción).",
        "",
        "| Real \\ Predicción | " + " | ".join(f"`{clase}`" for clase in clases) + " |",
        "|---|" + "---:|" * len(clases),
    ]
    for clase, fila in zip(clases, metricas["matriz"], strict=True):
        lineas.append(f"| `{clase}` | " + " | ".join(str(valor) for valor in fila) + " |")
    lineas += [
        "",
        "| Clase | Precisión | Sensibilidad | F1 | Muestras |",
        "|---|---:|---:|---:|---:|",
    ]
    for clase in clases:
        valores = metricas["por_clase"][clase]
        lineas.append(
            f"| `{clase}` | {valores['precision']:.3f} | {valores['sensibilidad']:.3f} "
            f"| {valores['f1']:.3f} | {valores['soporte']} |"
        )
    return "\n".join(lineas) + "\n"


def a_svg(metricas: dict[str, Any], titulo: str) -> str:
    """Matriz de confusion como imagen SVG."""
    clases, matriz = metricas["clases"], metricas["matriz"]
    celda, izquierda, arriba = 88, 130, 96
    ancho = izquierda + celda * len(clases) + 24
    alto = arriba + celda * len(clases) + 24
    partes = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {ancho} {alto}" '
        f'width="{ancho}" height="{alto}" font-family="sans-serif" font-size="13" role="img">',
        f"<title>Matriz de confusión: {escape(titulo)}</title>",
        f'<rect width="{ancho}" height="{alto}" fill="#ffffff"/>',
        f'<text x="16" y="28" font-size="16" font-weight="bold">{escape(titulo)}</text>',
        f'<text x="16" y="50" fill="#555555">Exactitud {metricas["exactitud"]:.1%}, '
        f"{metricas['muestras']} muestras. Filas: real.</text>",
    ]
    for j, clase in enumerate(clases):
        x = izquierda + celda * j + celda / 2
        partes.append(
            f'<text x="{x}" y="{arriba - 12}" text-anchor="middle">{escape(clase)}</text>'
        )
    for i, (clase, fila) in enumerate(zip(clases, matriz, strict=True)):
        y = arriba + celda * i
        total = sum(fila) or 1
        partes.append(
            f'<text x="{izquierda - 10}" y="{y + celda / 2 + 4}" text-anchor="end">'
            f"{escape(clase)}</text>"
        )
        for j, valor in enumerate(fila):
            proporcion = valor / total
            x = izquierda + celda * j
            color = "#1f6f43" if i == j else "#b3261e"
            texto = "#ffffff" if proporcion > 0.5 else "#111111"
            partes.append(
                f'<rect x="{x}" y="{y}" width="{celda - 2}" height="{celda - 2}" fill="{color}" '
                f'fill-opacity="{0.06 + 0.94 * proporcion:.2f}" stroke="#cccccc"/>'
            )
            partes.append(
                f'<text x="{x + celda / 2}" y="{y + celda / 2}" text-anchor="middle" '
                f'fill="{texto}" font-size="18">{valor}</text>'
                f'<text x="{x + celda / 2}" y="{y + celda / 2 + 20}" text-anchor="middle" '
                f'fill="{texto}">{proporcion:.0%}</text>'
            )
    return "\n".join([*partes, "</svg>"]) + "\n"


def escribir(metricas: dict[str, Any], carpeta: Path, titulo: str, nota: str = "") -> None:
    """Guarda ``metricas.md``, ``metricas.json`` y ``matriz.svg``."""
    carpeta.mkdir(parents=True, exist_ok=True)
    reporte = a_markdown(metricas, titulo)
    if nota:
        reporte += f"\n> {nota}\n"
    (carpeta / "metricas.md").write_text(reporte, encoding="utf-8", newline="\n")
    (carpeta / "metricas.json").write_text(
        json.dumps(metricas, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (carpeta / "matriz.svg").write_text(a_svg(metricas, titulo), encoding="utf-8", newline="\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada de ``metricas-modelo``."""
    parser = argparse.ArgumentParser(description="Metricas y matriz de confusion de un modelo.")
    parser.add_argument("--entrada", type=Path, required=True, help="CSV con real y prediccion.")
    parser.add_argument("--salida", type=Path, required=True, help="Carpeta de los resultados.")
    parser.add_argument("--titulo", default="Resultados del modelo")
    parser.add_argument("--tipo", choices=tuple(CLASES), help="Ordena las clases del contrato.")
    parser.add_argument("--columna-real", default="real")
    parser.add_argument("--columna-prediccion", default="prediccion")
    parser.add_argument("--nota", default="", help="Aclaracion sobre el origen de los datos.")
    args = parser.parse_args(argv)
    try:
        pares = leer_resultados(args.entrada, args.columna_real, args.columna_prediccion)
        metricas = calcular(pares, CLASES.get(args.tipo or "", ()))
    except (OSError, ValueError) as error:
        raise SystemExit(f"No se pudo evaluar {args.entrada}: {error}") from error
    escribir(metricas, args.salida, args.titulo, args.nota)
    print(a_markdown(metricas, args.titulo))
    print(f"Archivos en {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
