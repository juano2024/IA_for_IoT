// Modelo de ambiente de la Actividad 4. Archivo generado: no lo edites.
//   uv run entrenar-ambiente --datos <csv>
// Datos: sinteticos. Exactitud en prueba: 97.6%.
// Red neuronal 2-8-3 en C puro; no usa bibliotecas.
#pragma once
#include <math.h>

#define MODELO_AMBIENTE_ORIGEN "sinteticos"

static const char *const AMBIENTE_CLASES[3] = {"confort", "riesgo_leve", "riesgo"};
static const float AMBIENTE_OCULTA[8][3] = {
  {-5.778417f, 0.957657f, 2.607651f},
  {-5.732614f, 4.365160f, -1.723285f},
  {6.573888f, 5.235301f, -1.903053f},
  {-5.069798f, -1.055797f, -2.301798f},
  {-7.994346f, -4.561028f, -1.545177f},
  {0.017505f, -0.068812f, 3.433113f},
  {7.677961f, -4.441416f, -1.595750f},
  {0.106438f, 3.509899f, -2.005970f}
};
static const float AMBIENTE_SALIDA[3][9] = {
  {6.876136f, -6.075776f, -6.345137f, -6.868033f, -7.281441f, -12.047591f, -7.865720f, -6.014456f, -13.078384f},
  {0.712092f, 1.521708f, 1.527514f, -0.761390f, 0.392052f, 1.677526f, 0.448507f, -2.344493f, 1.626515f},
  {-8.291135f, 4.098662f, 4.089638f, 7.899884f, 7.320171f, 10.430279f, 7.166239f, 8.752664f, 11.333965f}
};

// Clasifica una lectura; devuelve la clase y escribe la confianza (0 a 1).
static const char *clasificarAmbiente(float temperatura, float humedad, float *confianza) {
  const float x0 = (temperatura - 20.0f) / 8.0f;
  const float x1 = (humedad - 50.0f) / 25.0f;
  float h[8], z[3];
  for (int j = 0; j < 8; j++) {
    h[j] = tanhf(AMBIENTE_OCULTA[j][0] * x0 + AMBIENTE_OCULTA[j][1] * x1 + AMBIENTE_OCULTA[j][2]);
  }
  int mejor = 0;
  for (int i = 0; i < 3; i++) {
    z[i] = AMBIENTE_SALIDA[i][8];
    for (int j = 0; j < 8; j++) z[i] += AMBIENTE_SALIDA[i][j] * h[j];
    if (z[i] > z[mejor]) mejor = i;
  }
  float total = 0;
  for (int i = 0; i < 3; i++) total += expf(z[i] - z[mejor]);
  *confianza = 1.0f / total;
  return AMBIENTE_CLASES[mejor];
}
