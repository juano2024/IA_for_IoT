"""API serverless de la Practica 3 (AWS Lambda + API Gateway HTTP API).

Caso de uso: monitoreo del cuarto de un bebe (18-22 C y 40-60 % de humedad).
Actuadores: ventilador, aire acondicionado y calentador.

Rutas:
    GET  /api/lecturas?limite=100&dispositivo=esp32-dht11
    GET  /api/lecturas.csv
    GET  /api/actuador?dispositivo=esp32-dht11
    POST /api/actuador   {"actuador": "ventilador|aire|calentador", "estado": "ON|OFF"}
                         {"modo": "AUTO"}            (cabecera x-api-token)
"""

from __future__ import annotations

import csv
import hmac
import io
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

DISPOSITIVO = "esp32-dht11"
TOPIC_COMANDOS = "cuartobebe/comandos"
ACTUADORES = ("ventilador", "aire", "calentador")
MENSAJE_COMANDO = "use {modo:AUTO} o {actuador:ventilador|aire|calentador, estado:ON|OFF}"
MAX_CSV = 10000
CABECERA_CSV = [
    "timestamp",
    "dispositivo",
    "temperatura_c",
    "humedad_pct",
    "estado",
    "actuador_sugerido",
]

_recursos: dict[str, Any] = {}


# ------------------------------------------------------------------ AWS (perezoso)
def _tabla(nombre_env: str) -> Any:
    if nombre_env not in _recursos:
        import boto3

        _recursos[nombre_env] = boto3.resource("dynamodb").Table(os.environ[nombre_env])
    return _recursos[nombre_env]


def _publicar(topic: str, payload: dict[str, Any]) -> None:
    if "iot" not in _recursos:
        import boto3

        iot = boto3.client("iot")
        endpoint = iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
        _recursos["iot"] = boto3.client("iot-data", endpoint_url=f"https://{endpoint}")
    _recursos["iot"].publish(topic=topic, qos=1, payload=json.dumps(payload))


# ------------------------------------------------------------------ logica
def _rangos() -> dict[str, float]:
    return {
        "t_min": float(os.environ.get("TEMP_MIN", 18)),
        "t_max": float(os.environ.get("TEMP_MAX", 22)),
        "h_min": float(os.environ.get("HUM_MIN", 40)),
        "h_max": float(os.environ.get("HUM_MAX", 60)),
        "t_aire": float(os.environ.get("TEMP_AIRE", 25)),
    }


def evaluar(temperatura: float, humedad: float) -> str:
    """Devuelve OK o ALERTA segun los rangos de confort del cuarto del bebe."""
    r = _rangos()
    fuera = not (r["t_min"] <= temperatura <= r["t_max"]) or not (
        r["h_min"] <= humedad <= r["h_max"]
    )
    return "ALERTA" if fuera else "OK"


def actuador_sugerido(temperatura: float) -> str | None:
    """Actuador que corresponde segun la alerta de temperatura.

    - Poco calor (por encima del maximo, hasta TEMP_AIRE): ventilador.
    - Mucho calor (por encima de TEMP_AIRE): aire acondicionado.
    - Frio (por debajo del minimo): calentador.
    - Temperatura de confort: ninguno.
    """
    r = _rangos()
    if temperatura < r["t_min"]:
        return "calentador"
    if temperatura > r["t_aire"]:
        return "aire"
    if temperatura > r["t_max"]:
        return "ventilador"
    return None


def _numero(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return int(valor) if valor == valor.to_integral_value() else float(valor)
    return valor


def _normalizar(item: dict[str, Any]) -> dict[str, Any]:
    ts_ms = int(item["ts"])
    temperatura = float(item["temperatura_c"])
    humedad = float(item["humedad_pct"])
    momento = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
    return {
        "timestamp": momento.isoformat().replace("+00:00", "Z"),
        "ts": ts_ms,
        "dispositivo": item["dispositivo"],
        "temperatura_c": round(temperatura, 2),
        "humedad_pct": round(humedad, 2),
        "estado": evaluar(temperatura, humedad),
        "actuador_sugerido": actuador_sugerido(temperatura) or "ninguno",
    }


def consultar_lecturas(dispositivo: str, limite: int) -> list[dict[str, Any]]:
    """Las `limite` lecturas mas recientes, de la mas nueva a la mas antigua."""
    from boto3.dynamodb.conditions import Key

    items: list[dict[str, Any]] = []
    clave: dict[str, Any] | None = None
    while len(items) < limite:
        args: dict[str, Any] = {
            "KeyConditionExpression": Key("dispositivo").eq(dispositivo),
            "ScanIndexForward": False,
            "Limit": limite - len(items),
        }
        if clave:
            args["ExclusiveStartKey"] = clave
        respuesta = _tabla("TABLA_LECTURAS").query(**args)
        items.extend(respuesta.get("Items", []))
        clave = respuesta.get("LastEvaluatedKey")
        if not clave:
            break
    return [_normalizar(i) for i in items]


def a_csv(lecturas: list[dict[str, Any]]) -> str:
    salida = io.StringIO()
    escritor = csv.writer(salida, lineterminator="\n")
    escritor.writerow(CABECERA_CSV)
    for fila in lecturas:
        escritor.writerow([fila[c] for c in CABECERA_CSV])
    return salida.getvalue()


def token_valido(recibido: str | None) -> bool:
    esperado = os.environ.get("API_TOKEN", "")
    return bool(esperado) and hmac.compare_digest(recibido or "", esperado)


def construir_comando(cuerpo: dict[str, Any]) -> dict[str, Any] | None:
    """Valida el cuerpo del POST y devuelve el mensaje MQTT, o None si es invalido."""
    if str(cuerpo.get("modo", "")).upper() == "AUTO":
        return {"modo": "AUTO"}
    actuador = str(cuerpo.get("actuador", "")).lower()
    estado = str(cuerpo.get("estado", "")).upper()
    if actuador in ACTUADORES and estado in ("ON", "OFF"):
        return {"actuador": actuador, "estado": estado}
    return None


# ------------------------------------------------------------------ respuestas
def _json(codigo: int, cuerpo: Any) -> dict[str, Any]:
    return {
        "statusCode": codigo,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(cuerpo, default=_numero),
    }


def _limite(parametros: dict[str, str]) -> int:
    try:
        return max(1, min(int(parametros.get("limite", 100)), 1000))
    except ValueError:
        return 100


# ------------------------------------------------------------------ handler
def handler(evento: dict[str, Any], _contexto: Any = None) -> dict[str, Any]:
    metodo = evento.get("requestContext", {}).get("http", {}).get("method", "GET")
    ruta = evento.get("rawPath", "")
    parametros = evento.get("queryStringParameters") or {}
    dispositivo = parametros.get("dispositivo", DISPOSITIVO)

    if metodo == "GET" and ruta.endswith("/api/lecturas"):
        return _json(200, consultar_lecturas(dispositivo, _limite(parametros)))

    if metodo == "GET" and ruta.endswith("/api/lecturas.csv"):
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "text/csv; charset=utf-8",
                "Content-Disposition": 'attachment; filename="lecturas.csv"',
            },
            "body": a_csv(consultar_lecturas(dispositivo, MAX_CSV)),
        }

    if ruta.endswith("/api/actuador"):
        if metodo == "GET":
            item = _tabla("TABLA_ACTUADOR").get_item(Key={"dispositivo": dispositivo}).get("Item")
            if not item:
                return _json(200, {"dispositivo": dispositivo, "modo": "DESCONOCIDO"})
            return _json(200, {k: _numero(v) for k, v in item.items()})

        if metodo == "POST":
            cabeceras = {k.lower(): v for k, v in (evento.get("headers") or {}).items()}
            if not token_valido(cabeceras.get("x-api-token")):
                return _json(401, {"error": "token invalido"})
            try:
                cuerpo = json.loads(evento.get("body") or "{}")
            except json.JSONDecodeError:
                return _json(400, {"error": "JSON invalido"})
            comando = construir_comando(cuerpo if isinstance(cuerpo, dict) else {})
            if comando is None:
                return _json(422, {"error": MENSAJE_COMANDO})
            _publicar(TOPIC_COMANDOS, comando)
            return _json(202, {"enviado": True, **comando})

    return _json(404, {"error": "ruta no encontrada"})
