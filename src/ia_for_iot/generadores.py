"""Generadores de datos simulados para probar el sistema sin hardware."""

from __future__ import annotations

import math
import random
from typing import Any

from .config import Settings
from .contrato import CLASES, clasificar_ambiente


class GeneradorDHT11:
    """Lecturas graduales de temperatura y humedad.

    Los valores siguen un objetivo que oscila dentro del confort; de vez en
    cuando el objetivo sale de los rangos configurados durante algunas lecturas
    para poder probar las alertas.
    """

    PASO_T = 0.8
    PASO_H = 3.0

    def __init__(
        self,
        settings: Settings,
        *,
        dispositivo: str = "esp32-dht11",
        temperatura: float | None = None,
        humedad: float | None = None,
        semilla: int | None = None,
        prob_excursion: float = 0.08,
    ) -> None:
        self._azar = random.Random(semilla)
        self._dispositivo = dispositivo
        self._fija_t = temperatura
        self._fija_h = humedad
        self._rango_t = (settings.temp_min, settings.temp_max)
        self._rango_h = (settings.hum_min, settings.hum_max)
        self._prob = prob_excursion
        self._n = 0
        self._restantes = 0
        self._desvio = (0.0, 0.0)
        self._t = sum(self._rango_t) / 2
        self._h = sum(self._rango_h) / 2

    def _nuevo_desvio(self) -> tuple[float, float]:
        """Elige que magnitud sale de rango y cuanto."""
        medio_t = (self._rango_t[1] - self._rango_t[0]) / 2
        medio_h = (self._rango_h[1] - self._rango_h[0]) / 2
        signo = self._azar.choice([-1, 1])
        if self._azar.random() < 0.5:
            return signo * (medio_t + self._azar.uniform(1.5, 4.0)), 0.0
        return 0.0, signo * (medio_h + self._azar.uniform(5.0, 18.0))

    @staticmethod
    def _acercar(actual: float, objetivo: float, paso: float, ruido: float) -> float:
        """Avanza hacia el objetivo sin saltos mayores que ``paso``."""
        cambio = (objetivo - actual) * 0.35 + ruido
        return actual + max(-paso, min(paso, cambio))

    def siguiente(self) -> dict[str, Any]:
        """Devuelve la siguiente lectura con el formato de la API."""
        if self._restantes > 0:
            self._restantes -= 1
        elif self._azar.random() < self._prob:
            self._restantes = self._azar.randint(6, 12)
            self._desvio = self._nuevo_desvio()
        else:
            self._desvio = (0.0, 0.0)

        onda = math.sin(2 * math.pi * self._n / 60)
        centro_t = sum(self._rango_t) / 2
        centro_h = sum(self._rango_h) / 2
        objetivo_t = centro_t + self._desvio[0] + onda * (self._rango_t[1] - self._rango_t[0]) / 4
        objetivo_h = centro_h + self._desvio[1] - onda * (self._rango_h[1] - self._rango_h[0]) / 4
        self._t = self._acercar(self._t, objetivo_t, self.PASO_T, self._azar.uniform(-0.15, 0.15))
        self._h = self._acercar(self._h, objetivo_h, self.PASO_H, self._azar.uniform(-0.6, 0.6))
        # Limites fisicos del DHT11.
        self._t = min(50.0, max(0.0, self._t))
        self._h = min(90.0, max(20.0, self._h))
        self._n += 1

        temperatura = self._t if self._fija_t is None else self._fija_t
        humedad = self._h if self._fija_h is None else self._fija_h
        return {
            "dispositivo": self._dispositivo,
            "temperatura_c": round(temperatura, 2),
            "humedad_pct": round(humedad, 2),
        }


class GeneradorClases:
    """Secuencia de clases que cambia cada varias lecturas, como un caso real."""

    def __init__(
        self,
        tipo: str,
        *,
        clase: str | None = None,
        semilla: int | None = None,
        pesos: tuple[float, ...] | None = None,
        clases: tuple[str, ...] | None = None,
    ) -> None:
        self.azar = random.Random(semilla)
        self._clases = clases or CLASES[tipo]
        self._pesos = pesos
        self._fija = clase
        self._actual = self._clases[0]
        self._restantes = 0

    def confianza(self) -> float:
        """Confianza plausible de un modelo seguro."""
        return round(self.azar.uniform(0.82, 0.99), 2)

    def siguiente(self) -> str:
        """Devuelve la clase actual, que dura entre 5 y 12 lecturas."""
        if self._fija is not None:
            return self._fija
        if self._restantes == 0:
            self._actual = self.azar.choices(self._clases, weights=self._pesos)[0]
            self._restantes = self.azar.randint(5, 12)
        self._restantes -= 1
        return self._actual


def prediccion_clima(
    lectura: dict[str, Any], settings: Settings, generador: GeneradorClases
) -> dict[str, Any]:
    """Mensaje ``clima``: la lectura mas la clase del ambiente."""
    clase = clasificar_ambiente(
        lectura["temperatura_c"],
        lectura["humedad_pct"],
        (settings.temp_min, settings.temp_max),
        (settings.hum_min, settings.hum_max),
    )
    return {**lectura, "clase": clase, "confianza": generador.confianza()}


# Desviacion de la aceleracion (g) y RMS del giroscopio (grados/s) por clase.
_INTENSIDAD = {
    "quieto": (0.01, 1.0),
    "normal": (0.06, 25.0),
    "leve": (0.06, 25.0),
    "agitacion": (0.25, 140.0),
    "brusco": (0.25, 140.0),
}
# Clases del modelo de movimiento del equipo (Edge Impulse).
CLASES_MOVIMIENTO_EQUIPO = ("quieto", "leve", "brusco")


def prediccion_movimiento(dispositivo: str, generador: GeneradorClases) -> dict[str, Any]:
    """Mensaje ``movimiento`` con ejes plausibles para la clase simulada."""
    clase = generador.siguiente()
    desvio, giro = _INTENSIDAD[clase]
    azar = generador.azar
    return {
        "dispositivo": dispositivo,
        "clase": clase,
        "confianza": generador.confianza(),
        "ax": round(azar.gauss(0.0, desvio), 3),
        "ay": round(azar.gauss(0.0, desvio), 3),
        "az": round(azar.gauss(1.0, desvio), 3),
        "gx": round(abs(azar.gauss(giro, giro * 0.25)), 1),
        "gy": round(abs(azar.gauss(giro, giro * 0.25)), 1),
        "gz": round(abs(azar.gauss(giro, giro * 0.25)), 1),
    }
