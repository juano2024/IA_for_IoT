// Modelo de postura (Edge Impulse, imagenes). Mientras no exista publica
// "sin_modelo". Para activarlo:
//   1. Exporta el proyecto de Edge Impulse como biblioteca Arduino y agregala
//      en el IDE (Programa -> Incluir biblioteca -> Anadir biblioteca .ZIP).
//   2. Cambia USAR_MODELO_VISION a 1 y el nombre del #include.
// Las etiquetas deben ser: boca_arriba, de_lado, boca_abajo, vacia.
// Si no cabe en memoria: imagen de 96x96 y un MobileNetV1 mas pequeno.
#pragma once

#define USAR_MODELO_VISION 0

#define FOTO_ANCHO 320
#define FOTO_ALTO 240

#if USAR_MODELO_VISION
#include <cuarto_bebe_postura_inferencing.h>
#include "edge-impulse-sdk/dsp/image/image.hpp"
#include "img_converters.h"

static uint8_t *fotoRgb = nullptr;

static int leerPixeles(size_t inicio, size_t cantidad, float *salida) {
  size_t p = inicio * 3;
  for (size_t i = 0; i < cantidad; i++, p += 3) {
    // El conversor de la camara entrega BGR.
    salida[i] = (fotoRgb[p + 2] << 16) + (fotoRgb[p + 1] << 8) + fotoRgb[p];
  }
  return 0;
}

static const char *clasificarFoto(const camera_fb_t *foto, float *confianza) {
  *confianza = 0;
  if (!fotoRgb) fotoRgb = (uint8_t *)ps_malloc(FOTO_ANCHO * FOTO_ALTO * 3);
  if (!fotoRgb || foto->width != FOTO_ANCHO || foto->height != FOTO_ALTO) return "sin_modelo";
  if (!fmt2rgb888(foto->buf, foto->len, PIXFORMAT_JPEG, fotoRgb)) return "sin_modelo";
  ei::image::processing::crop_and_interpolate_rgb888(fotoRgb, FOTO_ANCHO, FOTO_ALTO, fotoRgb,
                                                     EI_CLASSIFIER_INPUT_WIDTH,
                                                     EI_CLASSIFIER_INPUT_HEIGHT);
  ei::signal_t senal;
  senal.total_length = EI_CLASSIFIER_INPUT_WIDTH * EI_CLASSIFIER_INPUT_HEIGHT;
  senal.get_data = &leerPixeles;
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
static const char *clasificarFoto(const camera_fb_t *foto, float *confianza) {
  (void)foto;
  *confianza = 0;
  return "sin_modelo";
}
#endif
