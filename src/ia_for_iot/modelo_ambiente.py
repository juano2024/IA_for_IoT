"""Entrena el modelo de ambiente y lo exporta como C puro para el ESP32.

Es una red neuronal pequena (2 entradas, 8 neuronas ocultas, 3 clases). Se
exporta sin bibliotecas porque dos bibliotecas Arduino de Edge Impulse no
pueden convivir en el mismo firmware; asi Edge Impulse queda para el movimiento.

    entrenar-ambiente --datos datos_ambiente.csv

El CSV lleva ``temperatura_c``, ``humedad_pct`` y ``clase``. Sin ``--datos`` usa
datos sinteticos, que solo sirven para probar el firmware.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import get_settings
from .contrato import CLASES, clasificar_ambiente
from .metricas import calcular, escribir

CLASES_AMBIENTE = CLASES["clima"]
OCULTAS = 8
# Escala de las entradas: (valor - centro) / ancho.
CENTRO = (20.0, 50.0)
ANCHO = (8.0, 25.0)

Muestra = tuple[float, float, str]
NOTA_SINTETICA = (
    "Datos sintéticos etiquetados con los rangos de confort: prueban el flujo, "
    "no son el resultado del modelo. Hay que reentrenar con lecturas reales."
)


@dataclass
class Red:
    """Pesos de la red; la ultima columna de cada fila es el sesgo."""

    oculta: list[list[float]]
    salida: list[list[float]]

    def probabilidades(self, temperatura: float, humedad: float) -> list[float]:
        """Probabilidad de cada clase."""
        return self._propagar(temperatura, humedad)[1]

    def _propagar(self, temperatura: float, humedad: float) -> tuple[list[float], list[float]]:
        x = ((temperatura - CENTRO[0]) / ANCHO[0], (humedad - CENTRO[1]) / ANCHO[1])
        h = [math.tanh(f[0] * x[0] + f[1] * x[1] + f[2]) for f in self.oculta]
        z = [sum(p * v for p, v in zip(f, h, strict=False)) + f[-1] for f in self.salida]
        mayor = max(z)
        e = [math.exp(v - mayor) for v in z]
        total = sum(e)
        return h, [v / total for v in e]

    def clasificar(self, temperatura: float, humedad: float) -> tuple[str, float]:
        """Clase mas probable y su confianza."""
        p = self.probabilidades(temperatura, humedad)
        i = max(range(len(p)), key=p.__getitem__)
        return CLASES_AMBIENTE[i], p[i]


def entrenar(muestras: Sequence[Muestra], epocas: int = 150, semilla: int = 1) -> Red:
    """Descenso de gradiente estocastico con entropia cruzada."""
    azar = random.Random(semilla)
    red = Red(
        [[azar.uniform(-1.5, 1.5) for _ in range(3)] for _ in range(OCULTAS)],
        [[azar.uniform(-0.5, 0.5) for _ in range(OCULTAS + 1)] for _ in CLASES_AMBIENTE],
    )
    datos = list(muestras)
    # Cada clase pesa lo mismo aunque tenga menos muestras.
    cuenta = {clase: sum(m[2] == clase for m in datos) for clase in CLASES_AMBIENTE}
    peso = {c: len(datos) / (len(CLASES_AMBIENTE) * n) for c, n in cuenta.items() if n}
    for epoca in range(epocas):
        azar.shuffle(datos)
        tasa = 0.08 * (1 - epoca / epocas) + 0.005
        for temperatura, humedad, clase in datos:
            h, p = red._propagar(temperatura, humedad)
            error = [
                (pi - (CLASES_AMBIENTE[i] == clase)) * min(3.0, peso[clase])
                for i, pi in enumerate(p)
            ]
            x = ((temperatura - CENTRO[0]) / ANCHO[0], (humedad - CENTRO[1]) / ANCHO[1])
            atras = [
                (1 - h[j] ** 2) * sum(error[i] * red.salida[i][j] for i in range(len(error)))
                for j in range(OCULTAS)
            ]
            for i, fila in enumerate(red.salida):
                for j in range(OCULTAS):
                    fila[j] -= tasa * error[i] * h[j]
                fila[-1] -= tasa * error[i]
            for j, fila in enumerate(red.oculta):
                fila[0] -= tasa * atras[j] * x[0]
                fila[1] -= tasa * atras[j] * x[1]
                fila[2] -= tasa * atras[j]
    return red


def datos_sinteticos(cantidad: int = 1500, semilla: int = 1) -> list[Muestra]:
    """Puntos alrededor del confort con la etiqueta de los rangos configurados."""
    azar = random.Random(semilla)
    s = get_settings()
    muestras: list[Muestra] = []
    for _ in range(cantidad):
        t, h = azar.uniform(10, 34), azar.uniform(20, 85)
        if azar.random() < 0.5:
            t, h = azar.uniform(s.temp_min - 3, s.temp_max + 3), azar.uniform(30, 70)
        t, h = round(t, 1), round(h, 1)
        rangos = (s.temp_min, s.temp_max), (s.hum_min, s.hum_max)
        muestras.append((t, h, clasificar_ambiente(t, h, *rangos)))
    return muestras


def leer_datos(ruta: Path) -> list[Muestra]:
    """Lee el CSV etiquetado."""
    with ruta.open(encoding="utf-8-sig", newline="") as archivo:
        filas = list(csv.DictReader(archivo))
    muestras: list[Muestra] = []
    for numero, fila in enumerate(filas, start=2):
        try:
            t, h, clase = float(fila["temperatura_c"]), float(fila["humedad_pct"]), fila["clase"]
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"fila {numero}: faltan temperatura_c, humedad_pct o clase") from error
        if clase not in CLASES_AMBIENTE or not (math.isfinite(t) and math.isfinite(h)):
            raise ValueError(f"fila {numero}: clase o valor invalido")
        muestras.append((t, h, clase))
    for clase in CLASES_AMBIENTE:
        if sum(m[2] == clase for m in muestras) < 10:
            raise ValueError(f"se necesitan al menos 10 muestras de {clase}")
    return muestras


def dividir(
    muestras: Sequence[Muestra], fraccion: float = 0.25, semilla: int = 1
) -> tuple[list[Muestra], list[Muestra]]:
    """Separa entrenamiento y prueba conservando la proporcion de clases."""
    azar = random.Random(semilla)
    entrenamiento: list[Muestra] = []
    prueba: list[Muestra] = []
    for clase in CLASES_AMBIENTE:
        grupo = [m for m in muestras if m[2] == clase]
        azar.shuffle(grupo)
        corte = round(len(grupo) * fraccion)
        prueba += grupo[:corte]
        entrenamiento += grupo[corte:]
    return entrenamiento, prueba


def a_cabecera(red: Red, origen: str, exactitud: float) -> str:
    """Cabecera C con los pesos y la inferencia."""

    def filas(matriz: list[list[float]]) -> str:
        return ",\n".join("  {" + ", ".join(f"{v:.6f}f" for v in fila) + "}" for fila in matriz)

    clases = ", ".join(f'"{clase}"' for clase in CLASES_AMBIENTE)
    n = len(CLASES_AMBIENTE)
    return f"""// Modelo de ambiente de la Actividad 4. Archivo generado: no lo edites.
//   uv run entrenar-ambiente --datos <csv>
// Datos: {origen}. Exactitud en prueba: {exactitud:.1%}.
// Red neuronal 2-{OCULTAS}-{n} en C puro; no usa bibliotecas.
#pragma once
#include <math.h>

#define MODELO_AMBIENTE_ORIGEN "{origen}"

static const char *const AMBIENTE_CLASES[{n}] = {{{clases}}};
static const float AMBIENTE_OCULTA[{OCULTAS}][3] = {{
{filas(red.oculta)}
}};
static const float AMBIENTE_SALIDA[{n}][{OCULTAS + 1}] = {{
{filas(red.salida)}
}};

// Clasifica una lectura; devuelve la clase y escribe la confianza (0 a 1).
static const char *clasificarAmbiente(float temperatura, float humedad, float *confianza) {{
  const float x0 = (temperatura - {CENTRO[0]:.1f}f) / {ANCHO[0]:.1f}f;
  const float x1 = (humedad - {CENTRO[1]:.1f}f) / {ANCHO[1]:.1f}f;
  float h[{OCULTAS}], z[{n}];
  for (int j = 0; j < {OCULTAS}; j++) {{
    h[j] = tanhf(AMBIENTE_OCULTA[j][0] * x0 + AMBIENTE_OCULTA[j][1] * x1 + AMBIENTE_OCULTA[j][2]);
  }}
  int mejor = 0;
  for (int i = 0; i < {n}; i++) {{
    z[i] = AMBIENTE_SALIDA[i][{OCULTAS}];
    for (int j = 0; j < {OCULTAS}; j++) z[i] += AMBIENTE_SALIDA[i][j] * h[j];
    if (z[i] > z[mejor]) mejor = i;
  }}
  float total = 0;
  for (int i = 0; i < {n}; i++) total += expf(z[i] - z[mejor]);
  *confianza = 1.0f / total;
  return AMBIENTE_CLASES[mejor];
}}
"""


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada de ``entrenar-ambiente``."""
    parser = argparse.ArgumentParser(description="Entrena y exporta el modelo de ambiente.")
    parser.add_argument("--datos", type=Path, default=None, help="CSV etiquetado con datos reales.")
    parser.add_argument(
        "--cabecera", type=Path, default=Path("firmware/esp32_edge_ai/modelo_ambiente.h")
    )
    parser.add_argument("--salida", type=Path, default=Path("modelos/ambiente"))
    parser.add_argument("--epocas", type=int, default=150)
    parser.add_argument("--semilla", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        muestras = leer_datos(args.datos) if args.datos else datos_sinteticos(semilla=args.semilla)
    except (OSError, ValueError) as error:
        raise SystemExit(f"No se pudieron leer los datos: {error}") from error

    entrenamiento, prueba = dividir(muestras, semilla=args.semilla)
    red = entrenar(entrenamiento, args.epocas, args.semilla)
    pares = [(clase, red.clasificar(t, h)[0]) for t, h, clase in prueba]
    metricas = calcular(pares, CLASES_AMBIENTE)
    origen = "reales" if args.datos else "sinteticos"
    titulo = f"Modelo de ambiente (datos {origen if args.datos else 'sintéticos'})"
    escribir(metricas, args.salida, titulo, "" if args.datos else NOTA_SINTETICA)
    args.cabecera.parent.mkdir(parents=True, exist_ok=True)
    args.cabecera.write_text(
        a_cabecera(red, origen, metricas["exactitud"]), encoding="ascii", newline="\n"
    )
    print(
        f"{len(entrenamiento)} muestras de entrenamiento, {len(prueba)} de prueba ({origen}). "
        f"Exactitud {metricas['exactitud']:.1%}, F1 macro {metricas['f1_macro']:.3f}."
    )
    print(f"Modelo: {args.cabecera}. Metricas: {args.salida}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
