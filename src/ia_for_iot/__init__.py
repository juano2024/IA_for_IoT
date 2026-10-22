"""IA for IoT - Servidor web de monitoreo de temperatura y humedad.

Punto de entrada del paquete. Ejecuta ``ia-for-iot`` para levantar la API.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__", "main"]


def main() -> None:
    """Levanta el servidor web con uvicorn usando la configuracion del entorno."""
    import uvicorn

    from .config import get_settings

    settings = get_settings()
    print(f"Iniciando {settings.app_name} en http://{settings.app_host}:{settings.app_port}")
    uvicorn.run(
        "ia_for_iot.api:app",
        host=settings.app_host,
        port=settings.app_port,
        log_level="info",
    )
