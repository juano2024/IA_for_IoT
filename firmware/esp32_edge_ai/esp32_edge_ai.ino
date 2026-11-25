/*
 * IA for IoT - Actividad 4: inferencia en el ESP32 y servidor MCP
 *
 * DHT11   -> modelo de ambiente    -> MQTT cuartobebe/clima
 * MPU6050 -> modelo de movimiento  -> MQTT cuartobebe/movimiento
 * Servidor MCP en http://cuarto-bebe.local/mcp (o la IP del Monitor Serial)
 *
 * CABLEADO
 *   DHT11   VCC -> 3.3V   GND -> GND   DATA -> GPIO 33 (pull-up de 10 kOhm a 3.3V)
 *   MPU6050 VCC -> 3.3V   GND -> GND   SDA  -> GPIO 21   SCL -> GPIO 22
 *
 * BIBLIOTECAS: DHT sensor library y Adafruit Unified Sensor (Adafruit),
 *   PubSubClient (Nick O'Leary), ArduinoJson 7 (Benoit Blanchon).
 * TARJETA: "ESP32 Dev Module", particion "Huge APP (3MB No OTA/1MB SPIFFS)".
 *
 * SECRETOS: copia secrets.h.example a secrets.h (esta en .gitignore: no lo subas).
 */

#include <DHT.h>
#include <ESPmDNS.h>
#include <PubSubClient.h>
#include <WebServer.h>
#include <WiFi.h>
#include <Wire.h>

#include "modelo_ambiente.h"
#include "modelo_movimiento.h"
#include "secrets.h"
#include "servidor_mcp.h"

// 1 = solo imprime los 6 ejes a 50 Hz para edge-impulse-data-forwarder.
#define MODO_CAPTURA 0

#define DHTPIN 33
#define DHTTYPE DHT11
#define PIN_SDA 21
#define PIN_SCL 22
#define MPU_DIRECCION 0x68

#define DEVICE_ID "esp32-cuna"
#define TOPIC_PREFIX "cuartobebe"
#define INTERVALO_CLIMA 10000UL
#define INTERVALO_MUESTRA_US 20000UL  // 50 Hz
#define REINTENTO_MQTT 5000UL

DHT dht(DHTPIN, DHTTYPE);
WiFiClient red;
PubSubClient mqtt(red);
WebServer http(80);
EstadoCuna estado;

float ventana[MOV_VALORES];
int muestras = 0;
bool mpuListo = false;
bool serviciosListos = false;
unsigned long proximaMuestra = 0;
unsigned long ultimoClima = 0;
unsigned long ultimoIntentoMqtt = 0;

// ------------------------------------------------------------------
// MPU6050
// ------------------------------------------------------------------
bool escribirMpu(uint8_t registro, uint8_t valor) {
  Wire.beginTransmission(MPU_DIRECCION);
  Wire.write(registro);
  Wire.write(valor);
  return Wire.endTransmission() == 0;
}

bool iniciarMpu() {
  return escribirMpu(0x6B, 0x00)      // encender
         && escribirMpu(0x1A, 0x04)   // filtro pasa bajas de ~20 Hz
         && escribirMpu(0x1B, 0x08)   // giroscopio de +-500 grados/s
         && escribirMpu(0x1C, 0x08);  // acelerometro de +-4 g
}

// Lee los 6 ejes: aceleracion en g y giro en grados/s.
bool leerMpu(float *ejes) {
  Wire.beginTransmission(MPU_DIRECCION);
  Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint8_t)MPU_DIRECCION, (uint8_t)14) != 14) return false;
  int16_t crudo[7];
  for (int i = 0; i < 7; i++) {
    uint8_t alto = Wire.read();
    uint8_t bajo = Wire.read();
    crudo[i] = (int16_t)((alto << 8) | bajo);
  }
  for (int i = 0; i < 3; i++) {
    ejes[i] = crudo[i] / 8192.0f;
    ejes[i + 3] = crudo[i + 4] / 65.5f;  // crudo[3] es la temperatura interna
  }
  return true;
}

// ------------------------------------------------------------------
// Publicacion
// ------------------------------------------------------------------
void guardar(DatoCuna &dato, const char *clase, float confianza) {
  dato.disponible = true;
  dato.confianza = confianza;
  dato.momento = millis();
  snprintf(dato.clase, sizeof(dato.clase), "%s", clase);
}

void publicar(const char *topico, const char *cuerpo) {
  if (mqtt.connected() && mqtt.publish(topico, cuerpo)) {
    Serial.print(topico);
    Serial.print(" -> ");
    Serial.println(cuerpo);
  }
}

void procesarClima() {
  float temperatura = dht.readTemperature();
  float humedad = dht.readHumidity();
  if (isnan(temperatura) || isnan(humedad)) {
    Serial.println("Error: no se pudo leer el DHT11. Revisa el cableado y la pull-up de 10 kOhm.");
    return;
  }
  float confianza;
  const char *clase = clasificarAmbiente(temperatura, humedad, &confianza);
  guardar(estado.clima, clase, confianza);
  estado.clima.temperatura = temperatura;
  estado.clima.humedad = humedad;

  char cuerpo[200];
  snprintf(cuerpo, sizeof(cuerpo),
           "{\"dispositivo\":\"" DEVICE_ID
           "\",\"temperatura_c\":%.2f,\"humedad_pct\":%.2f,\"clase\":\"%s\",\"confianza\":%.3f}",
           temperatura, humedad, clase, confianza);
  publicar(TOPIC_PREFIX "/clima", cuerpo);
}

void procesarVentana() {
  DatoCuna &dato = estado.movimiento;
  for (int eje = 0; eje < MOV_EJES; eje++) {
    float suma = 0;
    for (int i = 0; i < MOV_MUESTRAS; i++) {
      float v = ventana[i * MOV_EJES + eje];
      suma += eje < 3 ? v : v * v;
    }
    // Promedio del acelerometro y RMS del giroscopio.
    float media = suma / MOV_MUESTRAS;
    dato.ejes[eje] = eje < 3 ? media : sqrtf(media);
  }
  float confianza;
  const char *clase = clasificarMovimiento(ventana, &confianza);
  guardar(dato, clase, confianza);
  dato.conEjes = true;

  char cuerpo[256];
  snprintf(cuerpo, sizeof(cuerpo),
           "{\"dispositivo\":\"" DEVICE_ID
           "\",\"clase\":\"%s\",\"confianza\":%.3f,\"ax\":%.3f,\"ay\":%.3f,\"az\":%.3f,"
           "\"gx\":%.1f,\"gy\":%.1f,\"gz\":%.1f}",
           clase, confianza, dato.ejes[0], dato.ejes[1], dato.ejes[2], dato.ejes[3], dato.ejes[4],
           dato.ejes[5]);
  publicar(TOPIC_PREFIX "/movimiento", cuerpo);
}

// Toma una muestra si ya toca. Si hubo una pausa larga (red o DHT11), la
// ventana se descarta para no mezclar tramos distintos.
void muestrearMovimiento() {
  unsigned long ahora = micros();
  if ((long)(ahora - proximaMuestra) < 0) return;
  if (ahora - proximaMuestra > 5 * INTERVALO_MUESTRA_US) {
    muestras = 0;
    proximaMuestra = ahora;
  }
  proximaMuestra += INTERVALO_MUESTRA_US;

  float *ejes = &ventana[muestras * MOV_EJES];
  if (!leerMpu(ejes)) {
    muestras = 0;
    return;
  }
#if MODO_CAPTURA
  Serial.printf("%.4f,%.4f,%.4f,%.3f,%.3f,%.3f\n", ejes[0], ejes[1], ejes[2], ejes[3], ejes[4],
                ejes[5]);
#else
  if (++muestras == MOV_MUESTRAS) {
    muestras = 0;
    procesarVentana();
  }
#endif
}

// ------------------------------------------------------------------
// MQTT: publica y escucha la postura de la ESP32-CAM
// ------------------------------------------------------------------
void alRecibir(char *topico, byte *cuerpo, unsigned int largo) {
  (void)topico;
  JsonDocument doc;
  if (deserializeJson(doc, cuerpo, largo)) return;
  const char *clase = doc["clase"] | "";
  static const char *const validas[] = {"boca_arriba", "de_lado", "boca_abajo", "vacia",
                                        "sin_modelo"};
  for (const char *valida : validas) {
    if (strcmp(clase, valida) == 0) guardar(estado.vision, clase, doc["confianza"] | 0.0f);
  }
}

void mantenerMqtt() {
  if (mqtt.connected()) {
    mqtt.loop();
    return;
  }
  if (millis() - ultimoIntentoMqtt < REINTENTO_MQTT) return;
  ultimoIntentoMqtt = millis();
  if (mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASS)) {
    mqtt.subscribe(TOPIC_PREFIX "/vision");
    Serial.println("Conectado al broker MQTT");
  } else {
    Serial.print("Broker MQTT no disponible, rc=");
    Serial.println(mqtt.state());
  }
}

// ------------------------------------------------------------------
// HTTP: transporte del servidor MCP
// ------------------------------------------------------------------
// Solo acepta paginas locales, para evitar DNS rebinding.
bool origenPermitido() {
  String origen = http.header("Origin");
  if (origen.length() == 0) return true;
  int inicio = origen.indexOf("://");
  if (inicio < 0) return false;
  String host = origen.substring(inicio + 3);
  int fin = host.indexOf(':');
  if (fin >= 0) host = host.substring(0, fin);
  return host == "localhost" || host == "127.0.0.1";
}

void atenderMcp() {
  if (!origenPermitido()) {
    http.send(403, "text/plain", "");
    return;
  }
  if (strlen(MCP_TOKEN) > 0 && http.header("Authorization") != String("Bearer ") + MCP_TOKEN) {
    http.send(401, "text/plain", "");
    return;
  }
  String cuerpo = http.arg("plain");
  String version = http.header("MCP-Protocol-Version");
  String metodo = http.header("Mcp-Method");
  String nombre = http.header("Mcp-Name");
  CabecerasMcp cabeceras = {version.c_str(), metodo.c_str(), nombre.c_str()};

  JsonDocument salida;
  int codigo = mcpManejar(cuerpo.c_str(), cuerpo.length(), cabeceras, estado, millis(), salida);
  if (salida.isNull()) {
    http.send(codigo, "text/plain", "");
    return;
  }
  String respuesta;
  serializeJson(salida, respuesta);
  http.send(codigo, "application/json", respuesta);
}

void iniciarServicios() {
  static const char *cabeceras[] = {"Origin", "Authorization", "MCP-Protocol-Version", "Mcp-Method",
                                    "Mcp-Name"};
  http.collectHeaders(cabeceras, 5);
  http.on("/mcp", HTTP_POST, atenderMcp);
  http.on("/mcp", HTTP_ANY, []() {
    http.sendHeader("Allow", "POST");
    http.send(405, "text/plain", "");
  });
  http.onNotFound([]() { http.send(404, "text/plain", "no encontrado"); });
  http.begin();
  MDNS.begin("cuarto-bebe");
  Serial.print("Servidor MCP: http://");
  Serial.print(WiFi.localIP());
  Serial.println("/mcp  (o http://cuarto-bebe.local/mcp)");
}

// ------------------------------------------------------------------
// Inicio y ciclo principal
// ------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== IA for IoT - Actividad 4 (ESP32) ===");
  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  mpuListo = iniciarMpu();
  if (!mpuListo) Serial.println("Error: no se encontro el MPU6050. Revisa SDA 21 y SCL 22.");
  proximaMuestra = micros();

#if !MODO_CAPTURA
  Serial.print("Modelo de ambiente entrenado con datos ");
  Serial.println(MODELO_AMBIENTE_ORIGEN);
  dht.begin();
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(alRecibir);
  mqtt.setBufferSize(512);
  mqtt.setSocketTimeout(3);
#endif
}

void loop() {
  if (mpuListo) muestrearMovimiento();

#if !MODO_CAPTURA
  if (WiFi.status() == WL_CONNECTED) {
    if (!serviciosListos) {
      iniciarServicios();
      serviciosListos = true;
    }
    mantenerMqtt();
    http.handleClient();
  }
  if (millis() - ultimoClima >= INTERVALO_CLIMA) {
    ultimoClima = millis();
    procesarClima();
  }
#endif
}
