// Servidor MCP de la Actividad 4: logica del protocolo, sin red.
//
// Debe responder igual que src/ia_for_iot/mcp_esp32.py. Atiende la version
// 2026-07-28 (sin estado, con la version en _meta) y las anteriores, que
// empiezan con initialize. Todas las herramientas son de solo lectura.
#pragma once
#include <ArduinoJson.h>
#include <stdio.h>
#include <string.h>

#define MCP_VERSION_MODERNA "2026-07-28"
#define MCP_VIGENCIA_S 60
#define MCP_MAXIMO_CUERPO 4096

struct DatoCuna {
  bool disponible = false;
  char clase[20] = "";
  float confianza = 0;
  unsigned long momento = 0;  // millis() de la ultima actualizacion
  float temperatura = 0;
  float humedad = 0;
  bool conEjes = false;
  float ejes[6] = {0, 0, 0, 0, 0, 0};
};

struct EstadoCuna {
  DatoCuna clima;
  DatoCuna movimiento;
  DatoCuna vision;
};

// Cabeceras HTTP del mensaje; "" si no llegaron.
struct CabecerasMcp {
  const char *version;
  const char *metodo;
  const char *nombre;
};

static const char *const MCP_VERSIONES_ANTERIORES[] = {"2025-11-25", "2025-06-18", "2025-03-26",
                                                       "2024-11-05"};
static const char *const MCP_INSTRUCCIONES =
    "Estado del cuarto del bebe medido por un ESP32: ambiente, movimiento de la cuna y "
    "postura. Es un prototipo academico y no reemplaza la supervision de un adulto.";
static const char *const MCP_HERRAMIENTAS[4][3] = {
    {"leer_ambiente", "Leer ambiente", "Temperatura, humedad y clase del ambiente del cuarto."},
    {"leer_movimiento", "Leer movimiento", "Ultima clase de movimiento de la cuna."},
    {"leer_postura", "Leer postura", "Ultima postura del bebe detectada por la camara."},
    {"estado_general", "Estado general", "Resumen de todo y si hay alguna alerta."},
};

// Nivel de cada clase: 0 normal, 1 advertencia, 2 critica.
static int mcpNivel(const char *clase) {
  if (strcmp(clase, "riesgo") == 0 || strcmp(clase, "boca_abajo") == 0 ||
      strcmp(clase, "brusco") == 0) {
    return 2;
  }
  if (strcmp(clase, "riesgo_leve") == 0 || strcmp(clase, "agitacion") == 0 ||
      strcmp(clase, "de_lado") == 0 || strcmp(clase, "leve") == 0) {
    return 1;
  }
  return 0;
}

static long mcpEdad(const DatoCuna &dato, unsigned long ahora) {
  return (long)((ahora - dato.momento + 500) / 1000);
}

static bool mcpVigente(const DatoCuna &dato, unsigned long ahora) {
  return dato.disponible && mcpEdad(dato, ahora) <= MCP_VIGENCIA_S;
}

// Datos estructurados de una prediccion.
static void mcpContenido(JsonObject salida, const DatoCuna &dato, bool esClima,
                         unsigned long ahora) {
  salida["disponible"] = dato.disponible;
  salida["vigente"] = mcpVigente(dato, ahora);
  if (!dato.disponible) return;
  salida["hace_s"] = mcpEdad(dato, ahora);
  salida["nivel"] = mcpNivel(dato.clase);
  salida["clase"] = dato.clase;
  salida["confianza"] = dato.confianza;
  if (esClima) {
    salida["temperatura_c"] = dato.temperatura;
    salida["humedad_pct"] = dato.humedad;
  }
  if (dato.conEjes) {
    static const char *const nombres[6] = {"ax", "ay", "az", "gx", "gy", "gz"};
    for (int i = 0; i < 6; i++) salida[nombres[i]] = dato.ejes[i];
  }
}

// Frase corta con el estado; devuelve los caracteres escritos.
static size_t mcpDescribir(char *texto, size_t largo, const char *nombre, const DatoCuna &dato,
                           bool esClima, unsigned long ahora) {
  if (!dato.disponible) return snprintf(texto, largo, "%s: sin datos.", nombre);
  char clase[20];
  snprintf(clase, sizeof(clase), "%s", dato.clase);
  for (char *c = clase; *c; c++) {
    if (*c == '_') *c = ' ';
  }
  char extra[48] = "";
  if (esClima) {
    snprintf(extra, sizeof(extra), "; %.1f C, %.0f %% de humedad", dato.temperatura, dato.humedad);
  }
  return snprintf(texto, largo, "%s: %s%s; confianza %.0f%%, hace %ld s.%s", nombre, clase, extra,
                  dato.confianza * 100.0f, mcpEdad(dato, ahora),
                  mcpVigente(dato, ahora) ? "" : " Dato antiguo: el dispositivo dejo de publicar.");
}

static void mcpListar(JsonObject resultado) {
  JsonArray lista = resultado["tools"].to<JsonArray>();
  for (int i = 0; i < 4; i++) {
    JsonObject h = lista.add<JsonObject>();
    h["name"] = MCP_HERRAMIENTAS[i][0];
    h["title"] = MCP_HERRAMIENTAS[i][1];
    h["description"] = MCP_HERRAMIENTAS[i][2];
    JsonObject esquema = h["inputSchema"].to<JsonObject>();
    esquema["type"] = "object";
    esquema["additionalProperties"] = false;
    h["annotations"]["readOnlyHint"] = true;
  }
}

// Ejecuta una herramienta; false si no existe.
static bool mcpLlamar(JsonObject resultado, const char *nombre, const EstadoCuna &estado,
                      unsigned long ahora) {
  const DatoCuna *datos[3] = {&estado.clima, &estado.movimiento, &estado.vision};
  static const char *const nombres[3] = {"Ambiente", "Movimiento", "Postura"};
  static const char *const tipos[3] = {"clima", "movimiento", "vision"};
  char texto[640];
  int elegida = -1;
  for (int i = 0; i < 4; i++) {
    if (strcmp(nombre, MCP_HERRAMIENTAS[i][0]) == 0) elegida = i;
  }
  if (elegida < 0) return false;

  JsonArray contenido = resultado["content"].to<JsonArray>();
  JsonObject estructurado = resultado["structuredContent"].to<JsonObject>();
  if (elegida < 3) {
    mcpDescribir(texto, sizeof(texto), nombres[elegida], *datos[elegida], elegida == 0, ahora);
    mcpContenido(estructurado, *datos[elegida], elegida == 0, ahora);
  } else {
    int nivel = 0;
    bool completo = true;
    for (int i = 0; i < 3; i++) {
      // Un dato antiguo no puede confirmar que todo este bien.
      if (!mcpVigente(*datos[i], ahora)) {
        completo = false;
      } else if (mcpNivel(datos[i]->clase) > nivel) {
        nivel = mcpNivel(datos[i]->clase);
      }
    }
    static const char *const resumen[3] = {"Sin alertas", "ADVERTENCIA", "ALERTA CRITICA"};
    size_t n = snprintf(texto, sizeof(texto), "%s%s.", resumen[nivel],
                        completo ? "" : " (faltan datos recientes)");
    for (int i = 0; i < 3 && n + 2 < sizeof(texto); i++) {
      texto[n++] = '\n';
      n += mcpDescribir(texto + n, sizeof(texto) - n, nombres[i], *datos[i], i == 0, ahora);
    }
    estructurado["nivel"] = nivel;
    estructurado["completo"] = completo;
    for (int i = 0; i < 3; i++) {
      mcpContenido(estructurado[tipos[i]].to<JsonObject>(), *datos[i], i == 0, ahora);
    }
  }
  JsonObject bloque = contenido.add<JsonObject>();
  bloque["type"] = "text";
  bloque["text"] = texto;
  resultado["isError"] = false;
  return true;
}

static int mcpError(JsonDocument &salida, int http, int codigo, const char *mensaje) {
  salida["jsonrpc"] = "2.0";
  salida.remove("result");
  if (salida["id"].isNull()) salida["id"] = nullptr;
  salida["error"]["code"] = codigo;
  salida["error"]["message"] = mensaje;
  return http;
}

// Versiones que empiezan con initialize; no usa sesiones.
static int mcpAnterior(const char *metodo, JsonVariantConst parametros, const EstadoCuna &estado,
                       unsigned long ahora, JsonDocument &salida) {
  JsonObject resultado = salida["result"].to<JsonObject>();
  if (strcmp(metodo, "initialize") == 0) {
    const char *pedida = parametros["protocolVersion"] | "";
    const char *acordada = MCP_VERSIONES_ANTERIORES[0];
    for (const char *conocida : MCP_VERSIONES_ANTERIORES) {
      if (strcmp(pedida, conocida) == 0) acordada = conocida;
    }
    resultado["protocolVersion"] = acordada;
    resultado["capabilities"]["tools"].to<JsonObject>();
    resultado["serverInfo"]["name"] = "cuarto-bebe-esp32";
    resultado["serverInfo"]["version"] = "1.0.0";
    resultado["instructions"] = MCP_INSTRUCCIONES;
  } else if (strcmp(metodo, "tools/list") == 0) {
    mcpListar(resultado);
  } else if (strcmp(metodo, "tools/call") == 0) {
    if (!mcpLlamar(resultado, parametros["name"] | "", estado, ahora)) {
      return mcpError(salida, 200, -32602, "Herramienta desconocida");
    }
  } else if (strcmp(metodo, "ping") != 0) {
    return mcpError(salida, 200, -32601, "Method not found");
  }
  return 200;
}

// Version 2026-07-28: sin estado y con cabeceras que repiten el cuerpo.
static int mcpModerno(const char *metodo, JsonVariantConst parametros,
                      const CabecerasMcp &cabeceras, const EstadoCuna &estado, unsigned long ahora,
                      JsonDocument &salida) {
  const char *version = parametros["_meta"]["io.modelcontextprotocol/protocolVersion"] | "";
  const char *herramienta = parametros["name"] | "";
  if (strcmp(cabeceras.version, version) != 0) {
    return mcpError(salida, 400, -32020, "La cabecera mcp-protocol-version no coincide");
  }
  if (strcmp(cabeceras.metodo, metodo) != 0) {
    return mcpError(salida, 400, -32020, "La cabecera mcp-method no coincide");
  }
  if (strcmp(metodo, "tools/call") == 0 && strcmp(cabeceras.nombre, herramienta) != 0) {
    return mcpError(salida, 400, -32020, "La cabecera mcp-name no coincide");
  }
  if (strcmp(version, MCP_VERSION_MODERNA) != 0) {
    mcpError(salida, 400, -32022, "Unsupported protocol version");
    salida["error"]["data"]["supported"].add(MCP_VERSION_MODERNA);
    salida["error"]["data"]["requested"] = version;
    return 400;
  }
  if (!parametros["_meta"]["io.modelcontextprotocol/clientCapabilities"].is<JsonObjectConst>()) {
    return mcpError(salida, 400, -32602, "Falta clientCapabilities en _meta");
  }

  JsonObject resultado = salida["result"].to<JsonObject>();
  bool conCache = true;
  if (strcmp(metodo, "server/discover") == 0) {
    resultado["supportedVersions"].add(MCP_VERSION_MODERNA);
    resultado["capabilities"]["tools"].to<JsonObject>();
    resultado["instructions"] = MCP_INSTRUCCIONES;
  } else if (strcmp(metodo, "tools/list") == 0) {
    mcpListar(resultado);
  } else if (strcmp(metodo, "tools/call") == 0) {
    conCache = false;
    if (!mcpLlamar(resultado, herramienta, estado, ahora)) {
      return mcpError(salida, 200, -32602, "Herramienta desconocida");
    }
  } else {
    return mcpError(salida, 404, -32601, "Method not found");
  }
  if (conCache) {
    resultado["ttlMs"] = 300000;
    resultado["cacheScope"] = "public";
  }
  resultado["resultType"] = "complete";
  JsonObject servidor = resultado["_meta"]["io.modelcontextprotocol/serverInfo"].to<JsonObject>();
  servidor["name"] = "cuarto-bebe-esp32";
  servidor["version"] = "1.0.0";
  return 200;
}

// Procesa un mensaje JSON-RPC. Devuelve el codigo HTTP y deja la respuesta en
// `salida`, que queda nula si no hay cuerpo (202 o 413).
static int mcpManejar(const char *cuerpo, size_t largo, const CabecerasMcp &cabeceras,
                      const EstadoCuna &estado, unsigned long ahora, JsonDocument &salida) {
  salida.clear();
  if (largo > MCP_MAXIMO_CUERPO) return 413;

  JsonDocument mensaje;
  if (deserializeJson(mensaje, cuerpo, largo)) {
    return mcpError(salida, 400, -32700, "JSON invalido");
  }
  JsonObjectConst objeto = mensaje.as<JsonObjectConst>();
  if (objeto.isNull() || strcmp(objeto["jsonrpc"] | "", "2.0") != 0 ||
      !objeto["method"].is<const char *>()) {
    return mcpError(salida, 400, -32600, "Peticion JSON-RPC invalida");
  }
  const char *metodo = objeto["method"];
  JsonVariantConst id = objeto["id"];
  if (id.isUnbound()) return 202;  // Notificacion: no lleva respuesta.
  if (!id.is<const char *>() && !id.is<long long>()) {
    return mcpError(salida, 400, -32600, "id invalido");
  }
  salida["jsonrpc"] = "2.0";
  salida["id"] = id;

  JsonVariantConst parametros = objeto["params"];
  if (!parametros.isNull() && !parametros.is<JsonObjectConst>()) {
    return mcpError(salida, 400, -32602, "params debe ser un objeto");
  }
  if (!parametros["_meta"]["io.modelcontextprotocol/protocolVersion"].isNull()) {
    return mcpModerno(metodo, parametros, cabeceras, estado, ahora, salida);
  }
  if (strcmp(cabeceras.version, MCP_VERSION_MODERNA) == 0) {
    return mcpError(salida, 400, -32020, "La version de la cabecera no esta en _meta");
  }
  return mcpAnterior(metodo, parametros, estado, ahora, salida);
}
