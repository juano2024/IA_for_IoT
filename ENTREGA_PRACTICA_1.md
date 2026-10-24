# Práctica 1: Servidor Web para Proyecto de IoT
**Especialización en Inteligencia Artificial aplicada a Internet de las Cosas**  
**Universidad Autónoma de Occidente**

---

## 🌐 1. Enlaces Públicos en Producción (Google Cloud Platform)

El servidor se encuentra activo 24/7 en la nube en Google Compute Engine con una **IP Externa Estática**:

* 📊 **Panel Web en Tiempo Real (Dashboard):** [http://34.136.139.59:8080/](http://34.136.139.59:8080/)
* 📚 **Documentación Interactiva (Swagger UI):** [http://34.136.139.59:8080/docs](http://34.136.139.59:8080/docs)
* ❤️ **Endpoint de Salud (Healthcheck):** [http://34.136.139.59:8080/api/health](http://34.136.139.59:8080/api/health)
* 📈 **Estadísticas de Confort:** [http://34.136.139.59:8080/api/estadisticas](http://34.136.139.59:8080/api/estadisticas)
* 📥 **Descarga del Archivo CSV:** [http://34.136.139.59:8080/api/lecturas.csv](http://34.136.139.59:8080/api/lecturas.csv)

---

## 📋 2. Cumplimiento de Criterios de la Rúbrica

| Requerimiento de la Guía | Implementación en Google Cloud | Estado |
| :--- | :--- | :---: |
| **1. Caso de uso (Temperatura y Humedad)** | Monitoreo de confort térmico y respiratorio en **cuarto de bebé** (Rangos: 18–22 °C, 40–60 % HR). Genera estados `OK` y `ALERTA` con motivo específico. | ✅ Cumplido |
| **2. Aplicación web en Ubuntu** | FastAPI + Uvicorn empaquetado en Docker sobre **Ubuntu 22.04 LTS** en Google Compute Engine (`servidor-ia-iot`). | ✅ Cumplido |
| **3. Modificación del código ESP32** | Firmware en C++ ([esp32_dht11.ino](firmware/esp32_dht11/esp32_dht11.ino)) con lectura de DHT11 y envío HTTP POST con JSON al endpoint `/api/lecturas`. | ✅ Cumplido |
| **4. Almacenamiento Dual (CSV y Base de Datos)** | Cada medición se almacena sincrónicamente en **SQLite** (`/app/data/ia_for_iot.db`) y en **CSV** (`/app/data/lecturas.csv`). | ✅ Cumplido |
| **5. Despliegue en la Nube (VM + IP Fija + Firewall)** | VM `e2-micro` en GCP con **IP Externa Estática** `34.136.139.59` y regla de cortafuegos `permitir-iot-web` en puertos `8080` y `8000`. | ✅ Cumplido |
| **6. Repositorio en GitHub** | Código fuente versionado con Docker, scripts de aprovisionamiento y pruebas unitarias (11/11 aprobadas). | ✅ Cumplido |

---

## 🛠️ 3. Instrucciones para el Compañero con el ESP32

El compañero que tiene la placa ESP32 y el sensor DHT11 en otra ciudad solo debe seguir estos pasos:

1. Abrir Arduino IDE.
2. Abrir el archivo `firmware/esp32_dht11/esp32_dht11.ino`.
3. Ajustar sus credenciales de Wi-Fi:
   ```cpp
   #define WIFI_SSID       "NOMBRE_DE_SU_WIFI"
   #define WIFI_PASS       "CLAVE_DE_SU_WIFI"
   ```
4. Verificar que la URL del servidor ya está configurada a la IP pública de GCP:
   ```cpp
   #define SERVER_URL     "http://34.136.139.59:8080"
   #define API_PATH       "/api/lecturas"
   ```
5. Conectar el ESP32 por USB y presionar **Subir / Flash**.
6. Conectar el sensor DHT11:
   * **VCC** -> 3.3V
   * **DATA** -> GPIO 33 (con resistencia pull-up de 10k a 3.3V)
   * **GND** -> GND
7. Abrir el Monitor Serial a **115200 baudios** para confirmar la conexión y ver los envíos cada 10 segundos.

---

## 💻 4. Simulación de Datos (Sin Hardware Físico)

Para hacer pruebas o demostraciones cuando el hardware no esté encendido:

```powershell
# Enviar 15 lecturas con 1 segundo de intervalo al servidor en GCP
uv run simular-esp32 --url http://34.136.139.59:8080/api/lecturas --count 15 --interval 1
```

---

## ☁️ 5. Arquitectura Cloud (GCP)

```
┌────────────────────────────────────────────────────────────────────────┐
│                        ESP32 + DHT11 (Remoto)                         │
│                    Envío HTTP POST cada 10s                            │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Internet
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     Google Cloud Platform (VPC)                        │
│                                                                        │
│   IP Estática: 34.136.139.59                                           │
│   Regla de Firewall: permitir-iot-web (TCP 8080, 8000)                │
│                                                                        │
│   ┌────────────────────────────────────────────────────────────────┐   │
│   │   Compute Engine (servidor-ia-iot - Ubuntu 22.04 LTS)          │   │
│   │                                                                │   │
│   │   ┌────────────────────────────────────────────────────────┐   │   │
│   │   │ Docker Container (ia_for_iot_api)                      │   │   │
│   │   │  • FastAPI + Uvicorn en puerto 8000 (mapeado a 8080)   │   │   │
│   │   │  • Clasificador de Confort Térmico (OK / ALERTA)       │   │   │
│   │   │  • Dashboard Web en HTML5 + SVG                        │   │   │
│   │   └───────────────────────────┬────────────────────────────┘   │   │
│   │                               │                                │   │
│   │                  ┌────────────┴────────────┐                   │   │
│   │                  ▼                         ▼                   │   │
│   │           Volumen Persistente       Volumen Persistente        │   │
│   │           SQLite Database           Archivo CSV                │   │
│   │           ia_for_iot.db             lecturas.csv               │   │
│   │                                                                │   │
│   └────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
```
