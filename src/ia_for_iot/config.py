"""Configuracion central del servidor IA for IoT.

Todos los valores se pueden sobreescribir con variables de entorno:

    APP_HOST, APP_PORT, DATABASE_URL, CSV_PATH,
    TEMP_MIN, TEMP_MAX, HUM_MIN, HUM_MAX, DEVICE_API_KEY,
    MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD,
    MQTT_TOPIC_PREFIX, MQTT_CLIENT_ID, MQTT_QOS, MQTT_PAIR_TTL,
    INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET, INFLUX_MEASUREMENT
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Parametros de ejecucion del servidor web."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Servidor -----------------------------------------------------
    app_name: str = "IA for IoT - Monitoreo de confort termico y respiratorio"
    app_version: str = "0.1.0"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # --- Persistencia -------------------------------------------------
    database_url: str = "sqlite:///./data/ia_for_iot.db"
    csv_path: Path = Path("./data/lecturas.csv")

    # --- Rangos de confort del caso de uso ----------------------------
    temp_min: float = 18.0
    temp_max: float = 22.0
    hum_min: float = 40.0
    hum_max: float = 60.0

    # --- Seguridad ----------------------------------------------------
    # Si se define, el dispositivo debe enviar la cabecera X-API-Key.
    device_api_key: str = ""

    # --- MQTT (Practica 2) --------------------------------------------
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_topic_prefix: str = "invernadero1"
    mqtt_client_id: str = "ia-for-iot-bridge"
    mqtt_qos: int = 1
    # Tiempo maximo (segundos) para emparejar temperatura y humedad.
    mqtt_pair_ttl: float = 60.0

    # --- InfluxDB (Practica 2) ----------------------------------------
    influx_url: str = "http://127.0.0.1:8086"
    influx_token: str = ""
    influx_org: str = "iaforiot"
    influx_bucket: str = "iot"
    influx_measurement: str = "clima"

    def ensure_directories(self) -> None:
        """Crea el directorio del CSV y el de la base de datos si no existen."""
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        db_dir = self._sqlite_directory()
        if db_dir is not None:
            db_dir.mkdir(parents=True, exist_ok=True)

    def _sqlite_directory(self) -> Path | None:
        """Extrae el directorio de un URL de SQLite basado en ruta relativa."""
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            return None
        ruta = self.database_url[len(prefix) :].split("?", 1)[0]
        if not ruta or ruta == ":memory:":
            return None
        return Path(ruta).parent


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la configuracion cacheada del proceso."""
    return Settings()
