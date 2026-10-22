# Imagen del servidor IA for IoT.
# Python 3.14 + uv (gestor de dependencias y empaquetado).
FROM python:3.14-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.12.2 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Primero se copian los manifiestos para aprovechar la cache de Docker.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Despues el codigo fuente del paquete.
COPY src ./src
RUN uv sync --frozen --no-dev

# El contenedor corre sin privilegios.
RUN useradd --create-home --uid 1000 iaforiot \
    && mkdir -p /app/data \
    && chown -R iaforiot:iaforiot /app
USER iaforiot

ENV DATABASE_URL="sqlite:////app/data/ia_for_iot.db" \
    CSV_PATH="/app/data/lecturas.csv" \
    APP_HOST="0.0.0.0" \
    APP_PORT="8000"

VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uv", "run", "--no-dev", "--no-sync", "uvicorn", "ia_for_iot.api:app", "--host", "0.0.0.0", "--port", "8000"]
