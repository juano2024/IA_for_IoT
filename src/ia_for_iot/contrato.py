"""Contrato de mensajes de la Actividad 4: predicciones hechas en el dispositivo.

Lo comparten el puente MQTT, los simuladores y el servidor MCP para que los
nombres de clase y los campos se definan en un solo lugar. El contrato se
describe en ``ENTREGA_ACTIVIDAD_4.md`` (seccion 3).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

# Clase que publica una placa mientras su modelo no este cargado.
SIN_MODELO = "sin_modelo"

# Nivel de cada clase: 0 normal, 1 advertencia, 2 critica.
NIVELES: dict[str, dict[str, int]] = {
    "clima": {"confort": 0, "riesgo_leve": 1, "riesgo": 2},
    "movimiento": {"quieto": 0, "normal": 0, "agitacion": 1},
    "vision": {"boca_arriba": 0, "de_lado": 1, "boca_abajo": 2, "vacia": 0},
}

TIPOS = tuple(NIVELES)
CLASES: dict[str, tuple[str, ...]] = {tipo: tuple(niveles) for tipo, niveles in NIVELES.items()}

# Promedio de la aceleracion (g) y RMS del giroscopio (grados/s) de la ventana.
EJES_IMU = ("ax", "ay", "az", "gx", "gy", "gz")

# Campos numericos de cada tipo: nombre -> (minimo, maximo, obligatorio).
CAMPOS: dict[str, dict[str, tuple[float, float, bool]]] = {
    "clima": {
        "temperatura_c": (-40.0, 80.0, True),
        "humedad_pct": (0.0, 100.0, True),
    },
    "movimiento": {eje: (-2000.0, 2000.0, False) for eje in EJES_IMU},
    "vision": {},
}


class ContratoInvalidoError(ValueError):
    """El mensaje no cumple el contrato de la Actividad 4."""


def _numero(datos: dict[str, Any], campo: str, minimo: float, maximo: float) -> float:
    """Lee un numero finito dentro de los limites."""
    valor = datos[campo]
    if isinstance(valor, bool) or not isinstance(valor, int | float):
        raise ContratoInvalidoError(f"{campo} debe ser un numero")
    numero = float(valor)
    if not math.isfinite(numero) or not minimo <= numero <= maximo:
        raise ContratoInvalidoError(f"{campo} fuera de limites ({minimo:g} a {maximo:g})")
    return numero


def validar_prediccion(tipo: str, datos: Any) -> dict[str, Any]:
    """Valida y normaliza una prediccion; lanza ``ContratoInvalidoError``."""
    if tipo not in NIVELES:
        raise ContratoInvalidoError(f"tipo desconocido: {tipo}")
    if not isinstance(datos, dict):
        raise ContratoInvalidoError("el mensaje debe ser un objeto JSON")

    clase = datos.get("clase")
    if clase != SIN_MODELO and clase not in NIVELES[tipo]:
        raise ContratoInvalidoError(f"clase desconocida para {tipo}: {clase!r}")
    if "confianza" not in datos:
        raise ContratoInvalidoError("falta confianza")

    dispositivo = str(datos.get("dispositivo") or "esp32-edge")[:64]
    prediccion: dict[str, Any] = {
        "dispositivo": dispositivo,
        "clase": clase,
        "confianza": _numero(datos, "confianza", 0.0, 1.0),
    }
    for campo, (minimo, maximo, obligatorio) in CAMPOS[tipo].items():
        if datos.get(campo) is None:
            if obligatorio:
                raise ContratoInvalidoError(f"falta {campo}")
            continue
        prediccion[campo] = _numero(datos, campo, minimo, maximo)
    return prediccion


def decodificar(tipo: str, payload: bytes) -> dict[str, Any]:
    """Convierte el cuerpo MQTT en una prediccion valida."""
    try:
        datos = json.loads(payload.decode("utf-8"))
    except ValueError as error:
        raise ContratoInvalidoError("JSON invalido") from error
    return validar_prediccion(tipo, datos)


def clasificar_ambiente(
    temperatura_c: float,
    humedad_pct: float,
    rango_t: tuple[float, float],
    rango_h: tuple[float, float],
) -> str:
    """Etiqueta de referencia del ambiente segun la distancia al confort.

    Hasta 2 C o 10 puntos de humedad fuera de rango es ``riesgo_leve``; mas
    alla, ``riesgo``. La usan el simulador y el etiquetado del modelo.
    """
    exceso_t = max(rango_t[0] - temperatura_c, temperatura_c - rango_t[1], 0.0)
    exceso_h = max(rango_h[0] - humedad_pct, humedad_pct - rango_h[1], 0.0)
    if exceso_t == 0 and exceso_h == 0:
        return "confort"
    if exceso_t <= 2.0 and exceso_h <= 10.0:
        return "riesgo_leve"
    return "riesgo"


@dataclass
class FiltroAlertas:
    """Confirma el nivel de alerta solo si la clase se repite con confianza.

    Evita falsas alarmas por una imagen o ventana suelta: el nivel cambia
    cuando la misma clase aparece ``repeticiones`` veces seguidas con al menos
    ``confianza_min``. Mientras no se confirme otra cosa, conserva el anterior.
    """

    repeticiones: int = 3
    confianza_min: float = 0.8
    _racha: dict[tuple[str, str], tuple[str, int]] = field(default_factory=dict)
    _nivel: dict[tuple[str, str], int] = field(default_factory=dict)

    def nivel(self, tipo: str, dispositivo: str, clase: str, confianza: float) -> int:
        """Registra una prediccion y devuelve el nivel confirmado."""
        clave = (dispositivo, tipo)
        if clase == SIN_MODELO:
            self._racha.pop(clave, None)
            self._nivel[clave] = 0
            return 0

        if confianza < self.confianza_min:
            self._racha.pop(clave, None)
            return self._nivel.get(clave, 0)

        anterior, cuenta = self._racha.get(clave, (clase, 0))
        cuenta = cuenta + 1 if anterior == clase else 1
        self._racha[clave] = (clase, cuenta)
        if cuenta >= self.repeticiones:
            self._nivel[clave] = NIVELES[tipo][clase]
        return self._nivel.get(clave, 0)
