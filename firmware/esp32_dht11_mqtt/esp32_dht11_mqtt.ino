/*
 * IA for IoT - Practica 2
 * ESP32 + DHT11 -> broker MQTT -> puente -> InfluxDB + CSV -> Grafana
 *
 * Caso de uso: monitoreo de confort termico y respiratorio en un invernadero.
 *
 * CABLEADO
 *   DHT11 VCC   -> 3.3V del ESP32
 *   DHT11 DATA  -> GPIO 33 del ESP32  (con resistencia pull-up de 10 kOhm a 3.3V)
 *   DHT11 GND   -> GND del ESP32
 *
 * NOTA IMPORTANTE: el GPIO 33 del ESP32 es de SOLO ENTRADA y no tiene
 * resistencia de pull-up interna. La linea de datos del DHT11 necesita una
 * resistencia pull-up EXTERNA de 10 kOhm entre DATA y 3.3V; sin ella las
 * lecturas fallan (devuelven NaN o valores erroneos).
 *
 * BIBLIOTECAS (Arduino IDE -> Gestor de bibliotecas):
 *   - DHT sensor library (Adafruit)   -> "DHT.h"
 *   - Adafruit Unified Sensor          -> dependencia de la anterior
 *   - PubSubClient (Nick O'Leary)      -> "PubSubClient.h"
 * TARJETA (Gestor de tarjetas):
 *   - ESP32 by Espressif Systems       -> placa "ESP32 Dev Module"
 *
 * AJUSTES: completa las credenciales de Wi-Fi y la IP del broker MQTT.
 *   - En local con Vagrant, MQTT_HOST es la IP LAN del PC anfitrion
 *     (por ejemplo 192.168.18.14) y el puerto 1883 esta reenviado a la VM.
 *   - En AWS, MQTT_HOST es la IP publica o el DNS de la instancia EC2.
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>

// ------------------------------------------------------------------
// 1. CONFIGURACION DE CREDENCIALES
// ------------------------------------------------------------------
#define WIFI_SSID       "TU_NOMBRE_WIFI"
#define WIFI_PASS       "TU_CLAVE_WIFI"

// Broker MQTT: IP LAN del PC (Vagrant) o IP publica de la instancia EC2.
#define MQTT_HOST       "192.168.18.14"
#define MQTT_PORT       1883
#define MQTT_USER       "iot"
#define MQTT_PASS       "iot2026"

// ------------------------------------------------------------------
// 2. CONFIGURACION DE PINES Y SENSOR (DHT11)
// ------------------------------------------------------------------
#define DHTPIN 33       // GPIO33 del ESP32 (solo entrada, requiere pull-up externo)
#define DHTTYPE DHT11

DHT dht(DHTPIN, DHTTYPE);

// ------------------------------------------------------------------
// 3. PARAMETROS DE ENVIO
// ------------------------------------------------------------------
#define DEVICE_ID      "esp32-dht11"
#define TOPIC_PREFIX   "invernadero1"
#define SEND_INTERVAL  10000   // 10 segundos entre lecturas
#define LED_PIN        2       // LED integrado de la placa (indica actividad)

WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

// ------------------------------------------------------------------
// 4. CONEXION A LA RED WI-FI
// ------------------------------------------------------------------
void conectarWiFi() {
  Serial.print("Conectando a Wi-Fi");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.print("Wi-Fi conectado. IP: ");
  Serial.println(WiFi.localIP());
}

// ------------------------------------------------------------------
// 5. CONEXION AL BROKER MQTT
// ------------------------------------------------------------------
void conectarMQTT() {
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setKeepAlive(15);
  mqtt.setSocketTimeout(5);

  while (!mqtt.connected()) {
    Serial.print("Conectando al broker MQTT ");
    Serial.print(MQTT_HOST);
    Serial.print("...");

    if (mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASS)) {
      Serial.println(" conectado");
    } else {
      Serial.print(" fallo (rc=");
      Serial.print(mqtt.state());
      Serial.println("), reintento en 5 s");
      delay(5000);
    }
  }
}

// ------------------------------------------------------------------
// 6. PUBLICACION DE LA LECTURA
// ------------------------------------------------------------------
void publicarLectura(float temperatura, float humedad) {
  char texto[16];

  snprintf(texto, sizeof(texto), "%.2f", temperatura);
  mqtt.publish(TOPIC_PREFIX "/temperatura", texto);

  snprintf(texto, sizeof(texto), "%.2f", humedad);
  mqtt.publish(TOPIC_PREFIX "/humedad", texto);

  Serial.print("Publicado en ");
  Serial.print(TOPIC_PREFIX);
  Serial.print("/temperatura y /humedad -> ");
  Serial.print(temperatura);
  Serial.print(" C / ");
  Serial.print(humedad);
  Serial.println(" %");
}

// ------------------------------------------------------------------
// 7. SETUP (INICIALIZACION)
// ------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(1000);
  while (!Serial) {
    ;
  }

  pinMode(LED_PIN, OUTPUT);
  Serial.println();
  Serial.println("=== IA for IoT - ESP32 + DHT11 (MQTT) ===");

  dht.begin();
  conectarWiFi();
  conectarMQTT();
}

// ------------------------------------------------------------------
// 8. LOOP (CICLO PRINCIPAL)
// ------------------------------------------------------------------
unsigned long ultimaLectura = 0;

void loop() {
  // Si se pierde la conexion, se intenta reconectar.
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi perdido, reconectando...");
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

  digitalWrite(LED_PIN, HIGH);

  // Leer los valores del sensor
  float temperatura = dht.readTemperature();
  float humedad = dht.readHumidity();

  // Validar si la lectura fue exitosa
  if (isnan(temperatura) || isnan(humedad)) {
    Serial.println("Error: no se pudo leer el DHT11. Revisa el cableado y la pull-up de 10 kOhm.");
    digitalWrite(LED_PIN, LOW);
    return;
  }

  // Imprimir valores en el monitor serie
  Serial.print("Temperatura: ");
  Serial.print(temperatura);
  Serial.print(" C | Humedad: ");
  Serial.print(humedad);
  Serial.println(" %");

  publicarLectura(temperatura, humedad);

  digitalWrite(LED_PIN, LOW);
}
