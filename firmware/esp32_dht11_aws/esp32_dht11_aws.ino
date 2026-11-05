/*
 * IA for IoT - Practica 3 (arquitectura serverless en AWS)
 * ESP32 + DHT11 -> AWS IoT Core (MQTT sobre TLS, puerto 8883) -> DynamoDB
 *
 * Caso de uso: cuarto de un bebe. Confort: 18-22 C y 40-60 % de humedad.
 *
 * ACTUADORES (rele o LED por cada uno) y modo AUTOMATICO segun la temperatura:
 *   T > 22 y T <= 25 C  -> ventilador          (GPIO 26)
 *   T > 25 C            -> aire acondicionado  (GPIO 27)
 *   T < 18 C            -> calentador          (GPIO 25)
 *   18 <= T <= 22 C     -> todo apagado
 * Solo hay un actuador encendido a la vez. Desde el dashboard se puede pasar a
 * modo MANUAL (encender/apagar cada uno) o volver a AUTO.
 *
 * Topics:
 *   publica   cuartobebe/telemetria {"dispositivo","temperatura_c","humedad_pct"}
 *   publica   cuartobebe/estado     {"dispositivo","modo","ventilador","aire","calentador"}
 *   suscribe  cuartobebe/comandos   {"actuador":"ventilador|aire|calentador","estado":"ON|OFF"}
 *                                   {"modo":"AUTO"}
 *
 * CABLEADO
 *   DHT11 VCC -> 3.3V   DHT11 DATA -> GPIO 33 (pull-up externo 10 kOhm a 3.3V)   DHT11 GND -> GND
 *
 * BIBLIOTECAS: DHT sensor library, Adafruit Unified Sensor, PubSubClient, ArduinoJson.
 * SECRETOS: copia secrets.h.example a secrets.h (esta en .gitignore: no lo subas).
 */

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include "secrets.h"

#define DHTPIN          33
#define DHTTYPE         DHT11
#define PIN_VENTILADOR  26
#define PIN_AIRE        27
#define PIN_CALENTADOR  25
#define LED_PIN         2
#define DEVICE_ID       "esp32-dht11"
#define TOPIC_TELEMETRIA "cuartobebe/telemetria"
#define TOPIC_ESTADO     "cuartobebe/estado"
#define TOPIC_COMANDOS   "cuartobebe/comandos"
#define SEND_INTERVAL   10000

// Umbrales de temperatura (C)
#define TEMP_MIN   18.0
#define TEMP_MAX   22.0
#define TEMP_AIRE  25.0

DHT dht(DHTPIN, DHTTYPE);
WiFiClientSecure red;
PubSubClient mqtt(red);
unsigned long ultimaLectura = 0;
bool modoAuto = true;

const char *NOMBRES[3] = {"ventilador", "aire", "calentador"};
const int PINES[3] = {PIN_VENTILADOR, PIN_AIRE, PIN_CALENTADOR};
bool encendido[3] = {false, false, false};

void publicarEstado() {
  JsonDocument doc;
  doc["dispositivo"] = DEVICE_ID;
  doc["modo"] = modoAuto ? "AUTO" : "MANUAL";
  for (int i = 0; i < 3; i++) {
    doc[NOMBRES[i]] = encendido[i] ? "ON" : "OFF";
  }
  char buffer[200];
  serializeJson(doc, buffer);
  mqtt.publish(TOPIC_ESTADO, buffer);
}

void poner(int i, bool valor) {
  encendido[i] = valor;
  digitalWrite(PINES[i], valor ? HIGH : LOW);
}

// Indice del actuador que corresponde a la temperatura, o -1 si esta en confort.
int actuadorParaTemperatura(float t) {
  if (t < TEMP_MIN) return 2;    // calentador
  if (t > TEMP_AIRE) return 1;   // aire acondicionado
  if (t > TEMP_MAX) return 0;    // ventilador
  return -1;
}

void aplicarAuto(float temperatura) {
  int elegido = actuadorParaTemperatura(temperatura);
  bool cambio = false;
  for (int i = 0; i < 3; i++) {
    bool debeEstar = (i == elegido);
    if (encendido[i] != debeEstar) {
      poner(i, debeEstar);
      cambio = true;
    }
  }
  if (cambio) publicarEstado();
}

void alRecibir(char *topic, byte *payload, unsigned int largo) {
  JsonDocument doc;
  if (deserializeJson(doc, payload, largo)) {
    Serial.println("Comando ignorado: JSON invalido");
    return;
  }

  const char *modo = doc["modo"] | "";
  if (strcmp(modo, "AUTO") == 0) {
    modoAuto = true;
    Serial.println("Modo AUTO");
    publicarEstado();
    return;
  }

  const char *actuador = doc["actuador"] | "";
  const char *estado = doc["estado"] | "";
  bool valido = strcmp(estado, "ON") == 0 || strcmp(estado, "OFF") == 0;
  for (int i = 0; i < 3 && valido; i++) {
    if (strcmp(actuador, NOMBRES[i]) == 0) {
      modoAuto = false;  // un comando manual desactiva el automatico
      bool encender = strcmp(estado, "ON") == 0;
      if (encender) {
        // Seguridad: nunca calentador y aire/ventilador a la vez.
        for (int j = 0; j < 3; j++) poner(j, false);
      }
      poner(i, encender);
      Serial.print(actuador);
      Serial.print(" -> ");
      Serial.println(estado);
      publicarEstado();
      return;
    }
  }
}

void conectarWiFi() {
  Serial.print("Conectando a Wi-Fi");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.print("\nWi-Fi conectado. IP: ");
  Serial.println(WiFi.localIP());

  // TLS valida las fechas del certificado: hace falta la hora por NTP.
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  while (time(nullptr) < 1700000000) {
    delay(500);
  }
}

void conectarMQTT() {
  while (!mqtt.connected()) {
    Serial.print("Conectando a AWS IoT...");
    if (mqtt.connect(DEVICE_ID)) {
      Serial.println(" conectado");
      mqtt.subscribe(TOPIC_COMANDOS, 1);
    } else {
      Serial.print(" fallo (rc=");
      Serial.print(mqtt.state());
      Serial.println("), reintento en 5 s");
      delay(5000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  pinMode(LED_PIN, OUTPUT);
  for (int i = 0; i < 3; i++) {
    pinMode(PINES[i], OUTPUT);
    poner(i, false);
  }
  Serial.println("\n=== IA for IoT - Cuarto del bebe (AWS IoT Core) ===");

  dht.begin();
  conectarWiFi();

  red.setCACert(AWS_CERT_CA);
  red.setCertificate(AWS_CERT_CRT);
  red.setPrivateKey(AWS_CERT_PRIVATE);
  mqtt.setServer(AWS_IOT_ENDPOINT, 8883);
  mqtt.setCallback(alRecibir);
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(30);
  conectarMQTT();
  publicarEstado();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    conectarWiFi();
  }
  if (!mqtt.connected()) {
    conectarMQTT();
  }
  mqtt.loop();

  if (millis() - ultimaLectura < SEND_INTERVAL) {
    return;
  }
  ultimaLectura = millis();

  float temperatura = dht.readTemperature();
  float humedad = dht.readHumidity();
  if (isnan(temperatura) || isnan(humedad)) {
    Serial.println("Error: no se pudo leer el DHT11. Revisa cableado y pull-up de 10 kOhm.");
    return;
  }

  digitalWrite(LED_PIN, HIGH);
  JsonDocument doc;
  doc["dispositivo"] = DEVICE_ID;
  doc["temperatura_c"] = serialized(String(temperatura, 2));
  doc["humedad_pct"] = serialized(String(humedad, 2));
  char buffer[200];
  serializeJson(doc, buffer);

  bool ok = mqtt.publish(TOPIC_TELEMETRIA, buffer);
  Serial.print(ok ? "Publicado: " : "Fallo al publicar: ");
  Serial.println(buffer);
  digitalWrite(LED_PIN, LOW);

  if (modoAuto) {
    aplicarAuto(temperatura);
  }
}
