/*
 * IA for IoT - Actividad 4: postura del bebe en la ESP32-CAM (AI Thinker)
 *
 * MODO_CAPTURA 1: sirve /capture y /stream para tomar las fotos del dataset
 *                 (capturar-dataset). No usa el modelo.
 * MODO_CAPTURA 0: toma una foto, la clasifica en la placa y publica
 *                 cuartobebe/vision por MQTT. No envia imagenes.
 *
 * Usa siempre un muneco, nunca un bebe real.
 *
 * BIBLIOTECAS: PubSubClient (Nick O'Leary).
 * TARJETA: "AI Thinker ESP32-CAM" con PSRAM, particion "Huge APP (3MB No OTA)".
 * PROGRAMAR: adaptador USB-serial con GPIO 0 a GND; quitalo y reinicia al terminar.
 *
 * SECRETOS: copia secrets.h.example a secrets.h (esta en .gitignore: no lo subas).
 */

#include <PubSubClient.h>
#include <WebServer.h>
#include <WiFi.h>
#include "esp_camera.h"

#include "modelo_vision.h"
#include "secrets.h"

#define MODO_CAPTURA 1

#define DEVICE_ID "esp32-cam"
#define TOPIC_VISION "cuartobebe/vision"
#define INTERVALO_FOTO 3000UL
#define REINTENTO_MQTT 5000UL

WiFiClient red;
PubSubClient mqtt(red);
WebServer http(80);
bool camaraLista = false;
bool servidorListo = false;
unsigned long ultimaFoto = 0;
unsigned long ultimoIntentoMqtt = 0;

// ------------------------------------------------------------------
// Camara (pines de la AI Thinker)
// ------------------------------------------------------------------
bool iniciarCamara() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = 5;
  config.pin_d1 = 18;
  config.pin_d2 = 19;
  config.pin_d3 = 21;
  config.pin_d4 = 36;
  config.pin_d5 = 39;
  config.pin_d6 = 34;
  config.pin_d7 = 35;
  config.pin_xclk = 0;
  config.pin_pclk = 22;
  config.pin_vsync = 25;
  config.pin_href = 23;
  config.pin_sccb_sda = 26;
  config.pin_sccb_scl = 27;
  config.pin_pwdn = 32;
  config.pin_reset = -1;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_QVGA;  // 320x240, igual en captura e inferencia
  config.jpeg_quality = 10;
  config.fb_count = 1;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  return esp_camera_init(&config) == ESP_OK;
}

// Con un solo buffer la primera foto puede ser vieja: se descarta.
camera_fb_t *tomarFoto() {
  camera_fb_t *foto = esp_camera_fb_get();
  if (foto) esp_camera_fb_return(foto);
  return esp_camera_fb_get();
}

// ------------------------------------------------------------------
// Captura del dataset
// ------------------------------------------------------------------
void atenderCaptura() {
  camera_fb_t *foto = tomarFoto();
  if (!foto) {
    http.send(503, "text/plain", "sin foto");
    return;
  }
  http.send_P(200, "image/jpeg", (const char *)foto->buf, foto->len);
  esp_camera_fb_return(foto);
}

void atenderVideo() {
  WiFiClient cliente = http.client();
  cliente.print(
      "HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=frame\r\n\r\n");
  while (cliente.connected()) {
    camera_fb_t *foto = esp_camera_fb_get();
    if (!foto) break;
    cliente.printf("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
                   (unsigned)foto->len);
    cliente.write(foto->buf, foto->len);
    cliente.print("\r\n");
    esp_camera_fb_return(foto);
    delay(100);
  }
}

void iniciarServidor() {
  http.on("/capture", HTTP_GET, atenderCaptura);
  http.on("/stream", HTTP_GET, atenderVideo);
  http.begin();
  Serial.print("Fotos en http://");
  Serial.print(WiFi.localIP());
  Serial.println("/capture  (video en /stream)");
}

// ------------------------------------------------------------------
// Inferencia y MQTT
// ------------------------------------------------------------------
void mantenerMqtt() {
  if (mqtt.connected()) {
    mqtt.loop();
    return;
  }
  if (millis() - ultimoIntentoMqtt < REINTENTO_MQTT) return;
  ultimoIntentoMqtt = millis();
  if (mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASS)) {
    Serial.println("Conectado al broker MQTT");
  } else {
    Serial.print("Broker MQTT no disponible, rc=");
    Serial.println(mqtt.state());
  }
}

void clasificarYPublicar() {
  camera_fb_t *foto = tomarFoto();
  if (!foto) {
    Serial.println("Error: la camara no entrego la foto.");
    return;
  }
  float confianza;
  const char *clase = clasificarFoto(foto, &confianza);
  esp_camera_fb_return(foto);

  char cuerpo[128];
  snprintf(cuerpo, sizeof(cuerpo),
           "{\"dispositivo\":\"" DEVICE_ID "\",\"clase\":\"%s\",\"confianza\":%.3f}", clase,
           confianza);
  Serial.println(cuerpo);
  if (mqtt.connected()) mqtt.publish(TOPIC_VISION, cuerpo);
}

// ------------------------------------------------------------------
// Inicio y ciclo principal
// ------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== IA for IoT - Actividad 4 (ESP32-CAM) ===");
  if (!psramFound()) Serial.println("Aviso: no se detecto PSRAM; revisa la tarjeta elegida.");
  camaraLista = iniciarCamara();
  if (!camaraLista) Serial.println("Error: no se pudo iniciar la camara.");
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setSocketTimeout(3);
}

void loop() {
  if (!camaraLista || WiFi.status() != WL_CONNECTED) {
    delay(200);
    return;
  }
#if MODO_CAPTURA
  if (!servidorListo) {
    iniciarServidor();
    servidorListo = true;
  }
  http.handleClient();
#else
  mantenerMqtt();
  if (millis() - ultimaFoto >= INTERVALO_FOTO) {
    ultimaFoto = millis();
    clasificarYPublicar();
  }
#endif
}
