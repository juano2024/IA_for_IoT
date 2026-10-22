/*
 * IA for IoT - Laboratorio 1
 * ESP32 + DHT11 -> servidor web FastAPI (almacenamiento en SQLite y CSV)
 *
 * Caso de uso: monitoreo de confort termico y respiratorio en un cuarto de bebe.
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
 * TARJETA (Gestor de tarjetas):
 *   - ESP32 by Espressif Systems       -> placa "ESP32 Dev Module"
 *
 * AJUSTES: completa las credenciales de Wi-Fi y la direccion del servidor.
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <DHT.h>

// ------------------------------------------------------------------
// 1. CONFIGURACION DE CREDENCIALES
// ------------------------------------------------------------------
#define WIFI_SSID       "TU_NOMBRE_WIFI"
#define WIFI_PASS       "TU_CLAVE_WIFI"

// Direccion del servidor donde corre la API.
// En la maquina virtual de Vagrant el puerto del host (8080) reenvia al contenedor.
// Desde otro dispositivo de la red usa la IP LAN del PC que corre la VM.
#define SERVER_URL     "http://192.168.1.100:8080"
#define API_PATH       "/api/lecturas"

// Dejar vacio si el servidor no exige clave (DEVICE_API_KEY no esta definido).
#define API_KEY        ""

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
#define SEND_INTERVAL  10000   // 10 segundos entre lecturas
#define LED_PIN        2       // LED integrado de la placa (indica actividad)

WiFiClient wifiClient;

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
// 5. ENVIO DE LA LECTURA AL SERVIDOR
// ------------------------------------------------------------------
void enviarLectura(float temperatura, float humedad) {
  char cuerpo[160];
  snprintf(cuerpo, sizeof(cuerpo),
           "{\"dispositivo\":\"%s\",\"temperatura_c\":%.2f,\"humedad_pct\":%.2f}",
           DEVICE_ID, temperatura, humedad);

  HTTPClient http;
  http.setTimeout(10000);
  if (!http.begin(wifiClient, String(SERVER_URL) + API_PATH)) {
    Serial.println("Error: no se pudo abrir la conexion HTTP");
    return;
  }
  http.addHeader("Content-Type", "application/json");
  if (strlen(API_KEY) > 0) {
    http.addHeader("X-API-Key", API_KEY);
  }

  int codigo = http.POST(cuerpo);
  if (codigo > 0) {
    Serial.print("HTTP ");
    Serial.print(codigo);
    Serial.print(" -> ");
    Serial.println(http.getString());
  } else {
    Serial.print("Error en el envio: ");
    Serial.println(http.errorToString(codigo));
  }
  http.end();
}

// ------------------------------------------------------------------
// 6. SETUP (INICIALIZACION)
// ------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(1000);
  while (!Serial) {
    ;
  }

  pinMode(LED_PIN, OUTPUT);
  Serial.println();
  Serial.println("=== IA for IoT - ESP32 + DHT11 ===");

  dht.begin();
  conectarWiFi();
}

// ------------------------------------------------------------------
// 7. LOOP (CICLO PRINCIPAL)
// ------------------------------------------------------------------
void loop() {
  // Si se pierde la conexion, se intenta reconectar.
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi perdido, reconectando...");
    conectarWiFi();
    return;
  }

  digitalWrite(LED_PIN, HIGH);

  // Leer los valores del sensor
  float temperatura = dht.readTemperature();
  float humedad = dht.readHumidity();

  // Validar si la lectura fue exitosa
  if (isnan(temperatura) || isnan(humedad)) {
    Serial.println("Error: no se pudo leer el DHT11. Revisa el cableado y la pull-up de 10 kOhm.");
    digitalWrite(LED_PIN, LOW);
    delay(2000);
    return;
  }

  // Imprimir valores en el monitor serie
  Serial.print("Temperatura: ");
  Serial.print(temperatura);
  Serial.print(" C | Humedad: ");
  Serial.print(humedad);
  Serial.println(" %");

  enviarLectura(temperatura, humedad);

  digitalWrite(LED_PIN, LOW);
  delay(SEND_INTERVAL);
}
