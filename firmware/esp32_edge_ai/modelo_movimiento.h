// Modelo de movimiento (Edge Impulse). Ventana de 2 s a 50 Hz con 6 ejes:
// ax, ay, az en g y gx, gy, gz en grados/s, en ese orden.
//
// Mientras no exista el modelo publica "sin_modelo". Para activarlo:
//   1. Exporta el proyecto de Edge Impulse como biblioteca Arduino y agregala
//      en el IDE (Programa -> Incluir biblioteca -> Anadir biblioteca .ZIP).
//   2. Cambia USAR_MODELO_MOVIMIENTO a 1 y el nombre del #include.
// Las etiquetas del proyecto deben ser: quieto, normal, agitacion.
#pragma once

#define USAR_MODELO_MOVIMIENTO 0

#define MOV_MUESTRAS 100
#define MOV_EJES 6
#define MOV_VALORES (MOV_MUESTRAS * MOV_EJES)

#if USAR_MODELO_MOVIMIENTO
#include <cuarto_bebe_movimiento_inferencing.h>

#if EI_CLASSIFIER_DSP_INPUT_FRAME_SIZE != MOV_VALORES
#error "El modelo no usa ventanas de 2 s a 50 Hz con 6 ejes"
#endif

static const char *clasificarMovimiento(float *ventana, float *confianza) {
  *confianza = 0;
  signal_t senal;
  if (numpy::signal_from_buffer(ventana, MOV_VALORES, &senal) != 0) return "sin_modelo";
  ei_impulse_result_t resultado = {0};
  if (run_classifier(&senal, &resultado, false) != EI_IMPULSE_OK) return "sin_modelo";
  const char *clase = "sin_modelo";
  for (size_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT; i++) {
    if (resultado.classification[i].value > *confianza) {
      *confianza = resultado.classification[i].value;
      clase = resultado.classification[i].label;
    }
  }
  return clase;
}
#else
static const char *clasificarMovimiento(float *ventana, float *confianza) {
  (void)ventana;
  *confianza = 0;
  return "sin_modelo";
}
#endif
