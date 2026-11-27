/*
 * IA aplicada a IoT - Actividad 4 - Cuarto de un bebe   (firmware v4)
 *
 * v4 = v3 del equipo publicando en el broker Mosquitto del portatil (sin AWS):
 * asi los datos llegan a InfluxDB, Grafana y las alertas de Telegram. Lo demas
 * (modelos, MCP, actuadores) no cambia.
 * UNA sola ESP32 con DOS modelos de Edge Impulse en el dispositivo:
 *   - Ambiente:   DHT11  -> temperatura/humedad -> confort / riesgo_leve / riesgo   (proyecto 1173172)
 *   - Movimiento: MPU6050 -> acelerometro+giroscopio -> clases de movimiento        (proyecto 1175392)
 * + broker MQTT local (Mosquitto con usuario y clave) + servidor MCP (HTTP) para consultar y controlar en lenguaje natural.
 *
 * Ambos modelos vienen de UNA biblioteca combinada (generate.py de multi-impulse):
 *     #include <AmbienteMovimiento_inferencing.h>
 * Los nombres impulse_<ID>_1 / impulse_handle_<ID>_1 salen del source/main.cpp generado.
 * Si regeneras la biblioteca, revisa que el ID y el sufijo (_1) sigan siendo los mismos.
 *
 * Topics:
 *   publica   cuartobebe/clima       {"dispositivo","temperatura_c","humedad_pct","clase_ambiente","confianza","nivel","alarma"}
 *   publica   cuartobebe/movimiento  {"dispositivo","clase_movimiento","confianza","nivel","alarma"}
 *   publica   cuartobebe/estado      {"dispositivo","modo","ventilador","aire","calentador"}
 *   suscribe  cuartobebe/comandos    {"actuador":"ventilador|aire|calentador","estado":"ON|OFF"} | {"modo":"AUTO"}
 *
 * Nivel de movimiento (decision de equipo, se puede cambiar): la etiqueta que contenga "brusco" -> 2 (alarma),
 * "leve" -> 1 (aviso), cualquier otra (quieto) -> 0.
 *
 * CABLEADO: DHT11 VCC->3.3V | DATA->GPIO 33 (pull-up 10k a 3.3V) | GND->GND
 *           MPU6050 VCC->3.3V | GND->GND | SDA->GPIO 21 | SCL->GPIO 22 | AD0->GND
 * BIBLIOTECAS: DHT sensor library, Adafruit Unified Sensor, Adafruit MPU6050, PubSubClient, ArduinoJson 7.x,
 *              AmbienteMovimiento_inferencing (la biblioteca combinada; NO deben estar instaladas las dos originales).
 * Tools -> Partition Scheme: "Huge APP" si el programa no cabe.
 * SECRETOS: secrets.h en la misma carpeta (no lo subas a git).
 */

#include <WiFi.h>
#include <WebServer.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <AmbienteMovimiento_inferencing.h>
#include "secrets.h"


// ---------------- Pines y nombres ----------------
#define DHTPIN            33
#define DHTTYPE           DHT11
#define SDA_PIN           21
#define SCL_PIN           22
#define LED_PIN           2
#define DEVICE_ID         "esp32-cuna"

#define TOPIC_CLIMA       "cuartobebe/clima"
#define TOPIC_MOVIMIENTO  "cuartobebe/movimiento"
#define TOPIC_ESTADO      "cuartobebe/estado"
#define TOPIC_COMANDOS    "cuartobebe/comandos"

// ---------------- MCP ----------------
#define MCP_PUERTO        80
// Vacio = sin autenticacion (solo pruebas en red local). Pon un valor largo antes de exponerlo a internet.
#define MCP_TOKEN         ""

// ---------------- Tiempos ----------------
#define INTERVALO_LECTURA_MS     2000     // DHT11 a 0.5 Hz, igual que en el entrenamiento
#define INTERVALO_PUBLICAR_MS    10000
#define REINTENTO_WIFI_MS        10000
#define REINTENTO_MQTT_MS        5000
#define INTERVALO_CLASIF_MOV_MS  500      // el modelo de movimiento corre cada 500 ms (window increase del entrenamiento)
#define PUB_MOV_MIN_MS           2000     // minimo entre publicaciones de movimiento por cambio de clase

// ---------------- Modelos ----------------
#define EJES_AMB          2
#define AMB_VALORES       10              // 5 lecturas x 2 ejes
#define FRECUENCIA_MPU_HZ 50              // igual que en el entrenamiento
#define INTERVALO_MPU_US  (1000000UL / FRECUENCIA_MPU_HZ)
#define EJES_MOV          6               // ax, ay, az, gx, gy, gz
#define MOV_VALORES       600             // 100 lecturas x 6 ejes = 2 s
#define CONFIANZA_MINIMA  0.60f

// ---------------- Umbrales fijos ----------------
#define TEMP_MIN          18.0
#define TEMP_MAX          22.0
#define TEMP_AIRE         25.0
#define TEMP_RIESGO_BAJA  16.0
#define HUM_LEVE_BAJA     40.0
#define HUM_LEVE_ALTA     60.0
#define HUM_RIESGO_BAJA   30.0
#define HUM_RIESGO_ALTA   70.0

DHT dht(DHTPIN, DHTTYPE);
Adafruit_MPU6050 mpu;
WiFiClient red;
PubSubClient mqtt(red);
WebServer mcp(MCP_PUERTO);

static ei_impulse_result_t resAmb;
static ei_impulse_result_t resMov;

// ---- Ambiente ----
float ventanaAmb[AMB_VALORES];
int nValoresAmb = 0;
float temperatura = NAN;
float humedad = NAN;
const char *claseAmbiente = "calentando";
float confianza = 0;
int nivel = 0;                 // 0 normal, 1 aviso, 2 alarma
int nivelPublicado = -1;

// ---- Movimiento ----
float ventanaMov[MOV_VALORES];
int nValoresMov = 0;
bool mpuOk = false;
unsigned long siguienteMuestraUs = 0;
char claseMovimiento[24] = "sin_datos";
float confianzaMov = 0;
int nivelMov = 0;
unsigned long movimientoActualizadoMs = 0;   // 0 = nunca
int nivelMovPublicado = -1;
char clasePublicadaMov[24] = "";
unsigned long ultimaPubMov = 0;

unsigned long ultimaLectura = 0;
unsigned long ultimaClasifMov = 0;
unsigned long ultimaPublicacion = 0;
unsigned long ultimoIntentoWifi = 0;
unsigned long ultimoIntentoMqtt = 0;
bool ntpConfigurado = false;
bool mcpIniciado = false;

bool modoAuto = true;
const char *NOMBRES[3] = {"ventilador", "aire", "calentador"};
bool encendido[3] = {false, false, false};

// =====================================================================
//  Actuadores
// =====================================================================

void actualizarLed() {
  bool alguno = encendido[0] || encendido[1] || encendido[2];
  digitalWrite(LED_PIN, alguno ? HIGH : LOW);
}

void publicarEstado() {
  if (!mqtt.connected()) return;
  JsonDocument doc;
  doc["dispositivo"] = DEVICE_ID;
  doc["modo"] = modoAuto ? "AUTO" : "MANUAL";
  for (int i = 0; i < 3; i++) doc[NOMBRES[i]] = encendido[i] ? "ON" : "OFF";
  char buffer[200];
  serializeJson(doc, buffer);
  mqtt.publish(TOPIC_ESTADO, buffer);
}

void poner(int i, bool valor) {
  encendido[i] = valor;
  actualizarLed();
}

int actuadorParaTemperatura(float t) {
  if (t < TEMP_MIN) return 2;    // calentador
  if (t > TEMP_AIRE) return 1;   // aire acondicionado
  if (t > TEMP_MAX) return 0;    // ventilador
  return -1;
}

void aplicarAuto(float t) {
  int elegido = actuadorParaTemperatura(t);
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

bool aplicarActuador(const char *actuador, const char *estado) {
  bool valido = strcmp(estado, "ON") == 0 || strcmp(estado, "OFF") == 0;
  if (!valido) return false;
  for (int i = 0; i < 3; i++) {
    if (strcmp(actuador, NOMBRES[i]) == 0) {
      modoAuto = false;
      bool encender = strcmp(estado, "ON") == 0;
      if (encender) {
        for (int j = 0; j < 3; j++) poner(j, false);   // nunca calentador y aire a la vez
      }
      poner(i, encender);
      Serial.printf("Comando: %s -> %s\n", actuador, estado);
      publicarEstado();
      return true;
    }
  }
  return false;
}

void activarModoAuto() {
  modoAuto = true;
  Serial.println("Modo AUTO");
  publicarEstado();
}

void alRecibir(char *topic, byte *payload, unsigned int largo) {
  JsonDocument doc;
  if (deserializeJson(doc, payload, largo)) {
    Serial.println("Mensaje MQTT ignorado: JSON invalido");
    return;
  }
  const char *modo = doc["modo"] | "";
  if (strcmp(modo, "AUTO") == 0) {
    activarModoAuto();
    return;
  }
  aplicarActuador(doc["actuador"] | "", doc["estado"] | "");
}

// =====================================================================
//  Modelo de ambiente (impulso 1173172)
// =====================================================================

void agregarMuestraAmb(float t, float h) {
  if (nValoresAmb >= AMB_VALORES) {
    memmove(ventanaAmb, ventanaAmb + EJES_AMB, (AMB_VALORES - EJES_AMB) * sizeof(float));
    nValoresAmb -= EJES_AMB;
  }
  ventanaAmb[nValoresAmb++] = t;
  ventanaAmb[nValoresAmb++] = h;
}

void clasificarAmbiente() {
  if (nValoresAmb < AMB_VALORES) {
    claseAmbiente = "calentando";   // todavia no hay 5 lecturas (primeros 10 s)
    confianza = 0;
    return;
  }

  signal_t signal;
  if (numpy::signal_from_buffer(ventanaAmb, AMB_VALORES, &signal) != 0) {
    Serial.println("Error preparando la senal de ambiente");
    return;
  }
  EI_IMPULSE_ERROR r = process_impulse(&impulse_handle_1173172_1, &signal, &resAmb, false);
  if (r != EI_IMPULSE_OK) {
    Serial.printf("Error en el modelo de ambiente (%d)\n", (int)r);
    return;
  }

  int mejor = 0;
  for (uint16_t i = 1; i < impulse_1173172_1.label_count; i++) {
    if (resAmb.classification[i].value > resAmb.classification[mejor].value) mejor = i;
  }
  confianza = resAmb.classification[mejor].value;
  claseAmbiente = (confianza >= CONFIANZA_MINIMA) ? resAmb.classification[mejor].label : "incierto";
}

int nivelPorModelo() {
  if (strcmp(claseAmbiente, "riesgo") == 0) return 2;
  if (strcmp(claseAmbiente, "riesgo_leve") == 0) return 1;
  return 0;
}

int nivelPorUmbrales(float t, float h) {
  if (t < TEMP_RIESGO_BAJA || t > TEMP_AIRE || h < HUM_RIESGO_BAJA || h > HUM_RIESGO_ALTA) return 2;
  if (t < TEMP_MIN || t > TEMP_MAX || h < HUM_LEVE_BAJA || h > HUM_LEVE_ALTA) return 1;
  return 0;
}

const char *nombreNivel(int n) {
  if (n >= 2) return "alarma";
  if (n == 1) return "aviso";
  return "normal";
}

// =====================================================================
//  Modelo de movimiento (impulso 1175392)
// =====================================================================

// Debe llamarse en CADA vuelta del loop: toma una lectura cada 20 ms (50 Hz).
// Unidades, rango y filtro iguales a los del sketch de recoleccion (m/s^2 y rad/s).
void muestrearMovimiento() {
  if (!mpuOk) return;
  unsigned long ahora = micros();
  long atraso = (long)(ahora - siguienteMuestraUs);
  if (atraso < 0) return;                       // aun no toca

  if (atraso > 200000) nValoresMov = 0;         // hueco > 200 ms: la ventana ya no es continua
  if (atraso > (long)(3 * INTERVALO_MPU_US)) siguienteMuestraUs = ahora;   // sin rafagas de recuperacion
  siguienteMuestraUs += INTERVALO_MPU_US;

  sensors_event_t a, g, temp;
  mpu.getEvent(&a, &g, &temp);

  if (nValoresMov >= MOV_VALORES) {
    memmove(ventanaMov, ventanaMov + EJES_MOV, (MOV_VALORES - EJES_MOV) * sizeof(float));
    nValoresMov -= EJES_MOV;
  }
  ventanaMov[nValoresMov++] = a.acceleration.x;
  ventanaMov[nValoresMov++] = a.acceleration.y;
  ventanaMov[nValoresMov++] = a.acceleration.z;
  ventanaMov[nValoresMov++] = g.gyro.x;
  ventanaMov[nValoresMov++] = g.gyro.y;
  ventanaMov[nValoresMov++] = g.gyro.z;
}

bool publicarMovimiento() {
  if (!mqtt.connected()) return false;
  JsonDocument doc;
  doc["dispositivo"] = DEVICE_ID;
  doc["clase_movimiento"] = claseMovimiento;
  doc["confianza"] = serialized(String(confianzaMov, 2));
  doc["nivel"] = nivelMov;
  doc["alarma"] = (nivelMov == 2);
  char buffer[256];
  serializeJson(doc, buffer);
  bool ok = mqtt.publish(TOPIC_MOVIMIENTO, buffer);
  Serial.print(ok ? "Publicado: " : "Fallo al publicar: ");
  Serial.println(buffer);
  return ok;
}

void clasificarMovimiento() {
  if (!mpuOk || nValoresMov < MOV_VALORES) return;   // ventana incompleta (primeros 2 s)

  signal_t signal;
  if (numpy::signal_from_buffer(ventanaMov, MOV_VALORES, &signal) != 0) {
    Serial.println("Error preparando la senal de movimiento");
    return;
  }
  EI_IMPULSE_ERROR r = process_impulse(&impulse_handle_1175392_1, &signal, &resMov, false);
  if (r != EI_IMPULSE_OK) {
    Serial.printf("Error en el modelo de movimiento (%d)\n", (int)r);
    return;
  }

  int mejor = 0;
  for (uint16_t i = 1; i < impulse_1175392_1.label_count; i++) {
    if (resMov.classification[i].value > resMov.classification[mejor].value) mejor = i;
  }
  confianzaMov = resMov.classification[mejor].value;
  const char *etiqueta = resMov.classification[mejor].label;

  if (confianzaMov >= CONFIANZA_MINIMA) {
    strlcpy(claseMovimiento, etiqueta, sizeof(claseMovimiento));
    nivelMov = strstr(etiqueta, "brusco") ? 2 : (strstr(etiqueta, "leve") ? 1 : 0);
  } else {
    strlcpy(claseMovimiento, "incierto", sizeof(claseMovimiento));
    nivelMov = 0;
  }
  movimientoActualizadoMs = millis();
}

void clasificarYPublicarMovimiento() {
  clasificarMovimiento();
  if (movimientoActualizadoMs == 0) return;

  unsigned long ahora = millis();
  bool cambio = (nivelMov != nivelMovPublicado) || strcmp(claseMovimiento, clasePublicadaMov) != 0;
  bool periodica = (ahora - ultimaPubMov) >= INTERVALO_PUBLICAR_MS;
  bool porCambio = cambio && (ahora - ultimaPubMov) >= PUB_MOV_MIN_MS;
  if (periodica || porCambio) {
    if (publicarMovimiento()) {
      ultimaPubMov = ahora;
      nivelMovPublicado = nivelMov;
      strlcpy(clasePublicadaMov, claseMovimiento, sizeof(clasePublicadaMov));
    }
  }
}

// =====================================================================
//  Servidor MCP (Model Context Protocol, JSON-RPC 2.0 sobre HTTP)
// =====================================================================

String textoAmbiente() {
  JsonDocument d;
  if (isnan(temperatura)) d["temperatura_c"] = nullptr; else d["temperatura_c"] = serialized(String(temperatura, 1));
  if (isnan(humedad)) d["humedad_pct"] = nullptr; else d["humedad_pct"] = serialized(String(humedad, 1));
  d["clase_ambiente"] = claseAmbiente;
  d["confianza"] = serialized(String(confianza, 2));
  d["nivel"] = nivel;
  d["nivel_texto"] = nombreNivel(nivel);
  d["alarma"] = (nivel == 2);
  d["nota"] = "Clases: confort (18-22 C, 40-60 %), riesgo_leve, riesgo. Nivel final = el mayor entre modelo y umbrales.";
  String s;
  serializeJson(d, s);
  return s;
}

String textoMovimiento() {
  JsonDocument d;
  d["clase_movimiento"] = claseMovimiento;
  d["confianza"] = serialized(String(confianzaMov, 2));
  d["nivel"] = nivelMov;
  d["nivel_texto"] = nombreNivel(nivelMov);
  d["alarma"] = (nivelMov == 2);
  if (!mpuOk) {
    d["nota"] = "El MPU6050 no esta conectado.";
  } else if (movimientoActualizadoMs == 0) {
    d["nota"] = "Aun no hay datos de movimiento (la ventana tarda unos 2 s en llenarse).";
  } else {
    d["segundos_desde_actualizacion"] = (millis() - movimientoActualizadoMs) / 1000;
  }
  String s;
  serializeJson(d, s);
  return s;
}

String textoEstado() {
  JsonDocument d;
  d["modo"] = modoAuto ? "AUTO" : "MANUAL";
  for (int i = 0; i < 3; i++) d[NOMBRES[i]] = encendido[i] ? "ON" : "OFF";
  String s;
  serializeJson(d, s);
  return s;
}

JsonObject agregarHerramienta(JsonArray tools, const char *nombre, const char *descripcion) {
  JsonObject t = tools.add<JsonObject>();
  t["name"] = nombre;
  t["description"] = descripcion;
  t["inputSchema"]["type"] = "object";
  return t;
}

void mcpEnviar(JsonDocument &doc) {
  String salida;
  serializeJson(doc, salida);
  mcp.send(200, "application/json", salida);
}

bool mcpAutorizado() {
  if (strlen(MCP_TOKEN) == 0) return true;
  String esperado = String("Bearer ") + MCP_TOKEN;
  return mcp.header("Authorization") == esperado;
}

void manejarMcp() {
  if (!mcpAutorizado()) {
    mcp.send(401, "application/json", "{\"error\":\"no autorizado\"}");
    return;
  }
  if (mcp.method() != HTTP_POST) {
    mcp.sendHeader("Allow", "POST");
    mcp.send(405, "text/plain", "Usa POST");
    return;
  }

  JsonDocument req;
  DeserializationError e = deserializeJson(req, mcp.arg("plain"));
  if (e || !req.is<JsonObject>()) {
    mcp.send(400, "application/json",
             "{\"jsonrpc\":\"2.0\",\"id\":null,\"error\":{\"code\":-32700,\"message\":\"JSON invalido\"}}");
    return;
  }

  const char *metodo = req["method"] | "";

  if (req["id"].isNull()) {   // notificaciones: sin respuesta
    mcp.send(202);
    return;
  }

  JsonDocument res;
  res["jsonrpc"] = "2.0";
  res["id"] = req["id"];

  if (strcmp(metodo, "initialize") == 0) {
    JsonObject r = res["result"].to<JsonObject>();
    r["protocolVersion"] = req["params"]["protocolVersion"] | "2025-03-26";
    r["capabilities"]["tools"].to<JsonObject>();
    r["serverInfo"]["name"] = "cuarto-bebe-esp32";
    r["serverInfo"]["version"] = "1.0.0";
    r["instructions"] = "Monitor del cuarto de un bebe: temperatura, humedad, clasificacion de IA del ambiente y del movimiento, y control de ventilador, aire y calentador.";

  } else if (strcmp(metodo, "ping") == 0) {
    res["result"].to<JsonObject>();

  } else if (strcmp(metodo, "tools/list") == 0) {
    JsonArray tools = res["result"]["tools"].to<JsonArray>();
    agregarHerramienta(tools, "obtener_ambiente",
      "Temperatura (C), humedad (%), clase del modelo de IA (confort, riesgo_leve, riesgo), confianza y nivel de alerta del cuarto del bebe.");
    agregarHerramienta(tools, "obtener_movimiento",
      "Ultima clasificacion de movimiento del bebe segun el acelerometro y giroscopio, con su confianza y nivel de alerta.");
    agregarHerramienta(tools, "obtener_estado",
      "Estado de los actuadores (ventilador, aire, calentador) y modo de control (AUTO o MANUAL).");
    JsonObject c = agregarHerramienta(tools, "controlar_actuador",
      "Enciende o apaga un actuador manualmente. Pasa el sistema a modo MANUAL. Encender uno apaga los demas por seguridad.");
    JsonObject props = c["inputSchema"]["properties"].to<JsonObject>();
    props["actuador"]["type"] = "string";
    JsonArray enA = props["actuador"]["enum"].to<JsonArray>();
    enA.add("ventilador"); enA.add("aire"); enA.add("calentador");
    props["estado"]["type"] = "string";
    JsonArray enE = props["estado"]["enum"].to<JsonArray>();
    enE.add("ON"); enE.add("OFF");
    JsonArray requeridos = c["inputSchema"]["required"].to<JsonArray>();
    requeridos.add("actuador"); requeridos.add("estado");
    agregarHerramienta(tools, "activar_modo_automatico",
      "Devuelve el control de los actuadores al modo automatico (decide segun la temperatura).");

  } else if (strcmp(metodo, "tools/call") == 0) {
    const char *nombre = req["params"]["name"] | "";
    JsonVariant args = req["params"]["arguments"];
    String texto;
    bool esError = false;
    bool conocida = true;

    if (strcmp(nombre, "obtener_ambiente") == 0) {
      texto = textoAmbiente();
    } else if (strcmp(nombre, "obtener_movimiento") == 0) {
      texto = textoMovimiento();
    } else if (strcmp(nombre, "obtener_estado") == 0) {
      texto = textoEstado();
    } else if (strcmp(nombre, "controlar_actuador") == 0) {
      if (aplicarActuador(args["actuador"] | "", args["estado"] | "")) {
        texto = textoEstado();
      } else {
        texto = "Parametros invalidos. actuador: ventilador, aire o calentador. estado: ON u OFF.";
        esError = true;
      }
    } else if (strcmp(nombre, "activar_modo_automatico") == 0) {
      activarModoAuto();
      texto = textoEstado();
    } else {
      conocida = false;
    }

    if (!conocida) {
      res["error"]["code"] = -32602;
      res["error"]["message"] = "Herramienta desconocida";
    } else {
      JsonObject r = res["result"].to<JsonObject>();
      JsonObject item = r["content"].to<JsonArray>().add<JsonObject>();
      item["type"] = "text";
      item["text"] = texto;
      if (esError) r["isError"] = true;
    }

  } else {
    res["error"]["code"] = -32601;
    res["error"]["message"] = "Metodo no encontrado";
  }

  mcpEnviar(res);
}

void iniciarMcp() {
  static const char *cabeceras[] = {"Authorization"};
  mcp.collectHeaders(cabeceras, 1);
  mcp.on("/mcp", HTTP_ANY, manejarMcp);
  mcp.begin();
  mcpIniciado = true;
  Serial.print("Servidor MCP listo en http://");
  Serial.print(WiFi.localIP());
  Serial.println("/mcp");
}

// =====================================================================
//  Red: Wi-Fi y MQTT sin bloquear el loop
// =====================================================================

void mantenerConexiones() {
  unsigned long ahora = millis();

  if (WiFi.status() != WL_CONNECTED) {
    if (ahora - ultimoIntentoWifi >= REINTENTO_WIFI_MS) {
      ultimoIntentoWifi = ahora;
      Serial.println("Wi-Fi: (re)conectando...");
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_PASS);
    }
    return;
  }

  if (!ntpConfigurado) {
    Serial.print("Wi-Fi conectado. IP: ");
    Serial.println(WiFi.localIP());
    ntpConfigurado = true;
  }
  if (!mcpIniciado) iniciarMcp();

  if (!mqtt.connected() && ahora - ultimoIntentoMqtt >= REINTENTO_MQTT_MS) {
    ultimoIntentoMqtt = ahora;
    Serial.print("Conectando al broker MQTT ");
    Serial.print(MQTT_HOST);
    Serial.print("...");
    if (mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASS)) {
      Serial.println(" conectado");
      mqtt.subscribe(TOPIC_COMANDOS, 1);
      nivelPublicado = -1;          // fuerza publicar al reconectar
      nivelMovPublicado = -1;
      clasePublicadaMov[0] = '\0';
      publicarEstado();
    } else {
      Serial.printf(" fallo (rc=%d)\n", mqtt.state());
    }
  }
}

bool publicarClima() {
  if (!mqtt.connected()) return false;

  JsonDocument doc;
  doc["dispositivo"] = DEVICE_ID;
  doc["temperatura_c"] = serialized(String(temperatura, 1));
  doc["humedad_pct"] = serialized(String(humedad, 1));
  doc["clase_ambiente"] = claseAmbiente;
  doc["confianza"] = serialized(String(confianza, 2));
  doc["nivel"] = nivel;
  doc["alarma"] = (nivel == 2);

  char buffer[300];
  serializeJson(doc, buffer);
  bool ok = mqtt.publish(TOPIC_CLIMA, buffer);
  Serial.print(ok ? "Publicado: " : "Fallo al publicar: ");
  Serial.println(buffer);
  return ok;
}

// =====================================================================
//  Ciclo principal
// =====================================================================

void leerClasificarYPublicar() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (isnan(t) || isnan(h)) {
    Serial.println("Error: no se pudo leer el DHT11. Revisa cableado y pull-up de 10 kOhm.");
    return;
  }
  temperatura = t;
  humedad = h;

  agregarMuestraAmb(t, h);
  clasificarAmbiente();

  int porModelo = nivelPorModelo();
  int porUmbrales = nivelPorUmbrales(t, h);
  nivel = max(porModelo, porUmbrales);

  Serial.printf("T=%.1f C  H=%.1f %%  ambiente=%s (%.2f) nivel=%d [modelo=%d umbrales=%d] | mov=%s (%.2f) nivel=%d\n",
                t, h, claseAmbiente, confianza, nivel, porModelo, porUmbrales,
                claseMovimiento, confianzaMov, nivelMov);

  if (modoAuto) aplicarAuto(t);

  unsigned long ahora = millis();
  bool cambioNivel = (nivel != nivelPublicado);
  if (cambioNivel || ahora - ultimaPublicacion >= INTERVALO_PUBLICAR_MS) {
    if (publicarClima()) {
      ultimaPublicacion = ahora;
      nivelPublicado = nivel;
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  Serial.println("\n=== Cuarto del bebe: v4: 2 modelos Edge + MQTT local + MCP ===");

  // Comprobacion de los tamanos de ventana contra los modelos exportados
  if (impulse_1173172_1.dsp_input_frame_size != AMB_VALORES)
    Serial.printf("ADVERTENCIA: el modelo de ambiente espera %u valores y el firmware usa %d\n",
                  (unsigned)impulse_1173172_1.dsp_input_frame_size, AMB_VALORES);
  if (impulse_1175392_1.dsp_input_frame_size != MOV_VALORES)
    Serial.printf("ADVERTENCIA: el modelo de movimiento espera %u valores y el firmware usa %d\n",
                  (unsigned)impulse_1175392_1.dsp_input_frame_size, MOV_VALORES);

  dht.begin();

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);
  if (mpu.begin()) {
    mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
    mpu.setGyroRange(MPU6050_RANGE_1000_DEG);
    mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);   // igual que el sketch de recoleccion
    mpuOk = true;
    Serial.println("MPU6050 listo");
  } else {
    Serial.println("MPU6050 NO encontrado: el movimiento quedara en sin_datos. Revisa SDA/SCL/AD0.");
  }

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);   // sin esperar: el loop se encarga

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(alRecibir);
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(30);

  delay(2000);                                        // el DHT11 necesita ~1-2 s tras energizar
  ultimaLectura = millis() - INTERVALO_LECTURA_MS;    // primera lectura en el primer loop
  siguienteMuestraUs = micros();
}

void loop() {
  muestrearMovimiento();                              // 50 Hz: debe correr en cada vuelta
  mantenerConexiones();
  if (mqtt.connected()) mqtt.loop();
  if (mcpIniciado && WiFi.status() == WL_CONNECTED) mcp.handleClient();

  unsigned long ahora = millis();
  if (ahora - ultimaLectura >= INTERVALO_LECTURA_MS) {
    ultimaLectura = ahora;
    leerClasificarYPublicar();
  }
  if (ahora - ultimaClasifMov >= INTERVALO_CLASIF_MOV_MS) {
    ultimaClasifMov = ahora;
    clasificarYPublicarMovimiento();
  }
}
