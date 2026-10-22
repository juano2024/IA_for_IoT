"""Configuracion central del servidor IA for IoT.

Todos los valores se pueden sobreescribir con variables de entorno:

    APP_HOST, APP_PORT, DATABASE_URL, CSV_PATH,
    TEMP_MIN, TEMP_MAX, HUM_MIN, HUM_MAX, DEVICE_API_KEY
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
