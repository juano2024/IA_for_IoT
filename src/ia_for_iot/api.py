"""Aplicacion web FastAPI: recibe, procesa y almacena las lecturas del ESP32."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import build_engine, build_session_factory, get_db, init_db
from .schemas import EstadisticasOut, LecturaCreate, LecturaOut, SaludOut
from .service import estadisticas, listar, registrar, total_lecturas

STATIC_DIR = Path(__file__).parent / "static"
DASHBOARD_HTML = STATIC_DIR / "index.html"

Sesion = Annotated[Session, Depends(get_db)]
ClaveApi = Annotated[str | None, Header(alias="X-API-Key")]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye la aplicacion FastAPI.

    ``settings`` se puede inyectar para las pruebas automatizadas.
    """
    configuracion = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Los recursos compartidos se crean una sola vez al arrancar la API y
        # se liberan al apagarla. Así cada petición puede reutilizar el motor.
        configuracion.ensure_directories()
        engine = build_engine(configuracion.database_url)
        app.state.settings = configuracion
        app.state.engine = engine
        app.state.session_factory = build_session_factory(engine)
        init_db(engine)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title=configuracion.app_name,
        version=configuracion.app_version,
        description=(
            "Servidor IoT del laboratorio. Recibe las lecturas de temperatura y "
            "humedad de un ESP32 con sensor DHT11, las procesa contra los rangos "
            "de confort del caso de uso y las almacena en SQLite y en un CSV."
        ),
        lifespan=lifespan,
    )

    def comprobar_api_key(request: Request, x_api_key: ClaveApi = None) -> None:
        """Valida la cabecera X-API-Key cuando el servidor la exige."""
        # La autenticación es opcional: solo se activa si DEVICE_API_KEY está
        # definida. compare_digest evita comparaciones vulnerables a timing.
        clave = request.app.state.settings.device_api_key
        if not clave:
            return
        if not x_api_key or not secrets.compare_digest(x_api_key, clave):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Cabecera X-API-Key invalida o ausente",
            )

    # ------------------------------------------------------------------
    # Panel web
    # ------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def panel() -> HTMLResponse:
        """Dashboard HTML con la evolucion de temperatura y humedad."""
        # El panel es un recurso estático servido por la misma API; no necesita
        # una ruta adicional ni un servidor web separado.
        if not DASHBOARD_HTML.is_file():
            raise HTTPException(status_code=404, detail="Dashboard no disponible")
        return HTMLResponse(DASHBOARD_HTML.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    @app.post(
        "/api/lecturas",
        response_model=LecturaOut,
        status_code=status.HTTP_201_CREATED,
        summary="Registra una lectura enviada por el dispositivo",
        dependencies=[Depends(comprobar_api_key)],
    )
    def crear_lectura(payload: LecturaCreate, request: Request, db: Sesion) -> LecturaOut:
        """Recibe la medicion, la evalua y la guarda en la BD y en el CSV."""
        # FastAPI valida payload y crea db antes de entrar aquí; registrar()
        # concentra la clasificación y la persistencia en ambos soportes.
        configuracion: Settings = request.app.state.settings
        return registrar(db, payload, configuracion)

    @app.get(
        "/api/lecturas",
        response_model=list[LecturaOut],
        summary="Historico de lecturas",
    )
    def obtener_lecturas(
        db: Sesion,
        limite: int = Query(default=100, ge=1, le=1000),
        dispositivo: str | None = Query(default=None),
    ) -> list[LecturaOut]:
        """Devuelve las lecturas mas recientes, de la mas nueva a la mas antigua."""
        # El límite protege la respuesta y el filtro permite consultar un
        # dispositivo concreto sin duplicar otra ruta de API.
        return listar(db, limite=limite, dispositivo=dispositivo)

    @app.get(
        "/api/lecturas/recientes",
        response_model=list[LecturaOut],
        summary="Ultimas lecturas para el panel",
    )
    def obtener_recientes(
        db: Sesion,
        limite: int = Query(default=60, ge=1, le=1000),
    ) -> list[LecturaOut]:
        """Alias en orden cronologico ideal para dibujar el grafico."""
        return listar(db, limite=limite)

    @app.get(
        "/api/estadisticas",
        response_model=EstadisticasOut,
        summary="Estadisticas descriptivas de las lecturas recientes",
    )
    def obtener_estadisticas(
        request: Request,
        db: Sesion,
        ventana: int = Query(default=100, ge=1, le=1000),
    ) -> EstadisticasOut:
        """Minimo, maximo, promedio y porcentaje de tiempo fuera de rango."""
        # Las estadísticas usan los mismos umbrales configurados que se
        # aplican al registrar nuevas lecturas.
        configuracion: Settings = request.app.state.settings
        return estadisticas(db, ventana=ventana, settings=configuracion)

    @app.get("/api/lecturas.csv", summary="Descarga el archivo CSV con las lecturas")
    def descargar_csv(request: Request) -> FileResponse:
        """Entrega el CSV generado por el servidor."""
        # Se comprueba la existencia para devolver un error explícito cuando
        # todavía no se ha recibido ninguna lectura.
        ruta: Path = request.app.state.settings.csv_path
        if not ruta.is_file():
            raise HTTPException(status_code=404, detail="Todavia no hay datos en el CSV")
        return FileResponse(
            path=ruta, media_type="text/csv; charset=utf-8", filename="lecturas.csv"
        )

    @app.get("/api/health", response_model=SaludOut, summary="Estado del servicio")
    def salud(request: Request, db: Sesion) -> SaludOut:
        """Comprueba la conexion con la base de datos y resume el estado."""
        # Este endpoint está pensado para supervisión: informa del servicio
        # aunque la consulta de conteo falle, marcándolo como degradado.
        try:
            total = total_lecturas(db)
            bd = "ok"
        except Exception as exc:  # pragma: no cover - solo ante caidas de la BD
            total = 0
            bd = f"error: {exc}"
        ultima = listar(db, limite=1)
        return SaludOut(
            estado="ok" if bd == "ok" else "degradado",
            version=request.app.state.settings.app_version,
            total_lecturas=total,
            ultima_lectura=ultima[0] if ultima else None,
            base_de_datos=bd,
            csv=str(request.app.state.settings.csv_path),
        )

    @app.get("/api/umbrales", summary="Rangos de confort configurados")
    def umbrales(request: Request) -> JSONResponse:
        """Expone los umbrales usados para clasificar cada lectura."""
        configuracion: Settings = request.app.state.settings
        return JSONResponse(
            {
                "temperatura_c": {"min": configuracion.temp_min, "max": configuracion.temp_max},
                "humedad_pct": {"min": configuracion.hum_min, "max": configuracion.hum_max},
            }
        )

    return app


app = create_app()
