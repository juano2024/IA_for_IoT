"""Pruebas de los simuladores HTTP y MQTT y del contrato de la Actividad 4."""

from __future__ import annotations

import itertools
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from ia_for_iot import publicador_mqtt, simulador
from ia_for_iot.config import Settings
from ia_for_iot.contrato import (
    CLASES,
    ContratoInvalidoError,
    FiltroAlertas,
    clasificar_ambiente,
    decodificar,
    validar_prediccion,
)
from ia_for_iot.generadores import (
    GeneradorClases,
    GeneradorDHT11,
    prediccion_clima,
    prediccion_movimiento,
)
from ia_for_iot.schemas import LecturaCreate
from ia_for_iot.service import evaluar

RANGO_T, RANGO_H = (18.0, 22.0), (40.0, 60.0)


@pytest.fixture()
def settings() -> Settings:
    """Rangos por defecto, sin leer el .env del desarrollador."""
    return Settings(_env_file=None, temp_min=18, temp_max=22, hum_min=40, hum_max=60)


class ServidorFalso(HTTPServer):
    """Guarda las peticiones y responde los codigos indicados."""

    def __init__(self, codigos: list[int]) -> None:
        super().__init__(("127.0.0.1", 0), _Manejador)
        self.codigos = codigos
        self.peticiones: list[dict[str, Any]] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/api/lecturas"


class _Manejador(BaseHTTPRequestHandler):
    server: ServidorFalso

    def do_POST(self) -> None:
        largo = int(self.headers["Content-Length"])
        self.server.peticiones.append(
            {
                "ruta": self.path,
                "tipo": self.headers["Content-Type"],
                "clave": self.headers.get("X-API-Key"),
                "json": json.loads(self.rfile.read(largo)),
            }
        )
        codigos = self.server.codigos
        self.send_response(codigos.pop(0) if len(codigos) > 1 else codigos[0])
        self.end_headers()
        self.wfile.write(b'{"detalle":"respuesta"}')

    def log_message(self, formato: str, *args: object) -> None:
        pass


@pytest.fixture()
def servidor() -> Iterator[ServidorFalso]:
    instancia = ServidorFalso([201])
    hilo = threading.Thread(target=instancia.serve_forever, daemon=True)
    hilo.start()
    yield instancia
    instancia.shutdown()
    instancia.server_close()


# ---------------------------------------------------------------- generadores
def test_dht11_es_gradual_y_tiene_excursiones(settings: Settings) -> None:
    generador = GeneradorDHT11(settings, semilla=1)
    lecturas = [generador.siguiente() for _ in range(600)]
    for previa, actual in itertools.pairwise(lecturas):
        assert abs(actual["temperatura_c"] - previa["temperatura_c"]) <= 0.82
        assert abs(actual["humedad_pct"] - previa["humedad_pct"]) <= 3.02
    alertas = sum(evaluar(x["temperatura_c"], x["humedad_pct"], settings)[1] for x in lecturas)
    assert 0 < alertas < len(lecturas) * 0.8
    for lectura in lecturas:
        LecturaCreate.model_validate(lectura)


def test_dht11_valores_fijos_y_semilla(settings: Settings) -> None:
    fijo = GeneradorDHT11(settings, temperatura=12, humedad=85)
    assert fijo.siguiente() == {
        "dispositivo": "esp32-dht11",
        "temperatura_c": 12,
        "humedad_pct": 85,
    }
    uno, otro = GeneradorDHT11(settings, semilla=7), GeneradorDHT11(settings, semilla=7)
    assert [uno.siguiente() for _ in range(20)] == [otro.siguiente() for _ in range(20)]


def test_predicciones_simuladas_cumplen_el_contrato(settings: Settings) -> None:
    dht = GeneradorDHT11(settings, semilla=3)
    clima = GeneradorClases("clima", semilla=3)
    movimiento = GeneradorClases("movimiento", semilla=3)
    for _ in range(50):
        validar_prediccion("clima", prediccion_clima(dht.siguiente(), settings, clima))
        validar_prediccion("movimiento", prediccion_movimiento("esp32-cuna-sim", movimiento))
    for clase in CLASES["movimiento"]:
        forzada = GeneradorClases("movimiento", clase=clase)
        validar_prediccion("movimiento", prediccion_movimiento("x", forzada))


# ------------------------------------------------------------------- contrato
@pytest.mark.parametrize(
    ("temperatura", "humedad", "clase"),
    [(20, 50, "confort"), (23.5, 50, "riesgo_leve"), (20, 33, "riesgo_leve"), (27, 50, "riesgo")],
)
def test_clasificar_ambiente(temperatura: float, humedad: float, clase: str) -> None:
    assert clasificar_ambiente(temperatura, humedad, RANGO_T, RANGO_H) == clase


@pytest.mark.parametrize(
    ("tipo", "cuerpo"),
    [
        ("vision", b"no es json"),
        ("vision", b"[1]"),
        ("vision", b'{"clase":"dormido","confianza":0.9}'),
        ("vision", b'{"clase":"vacia"}'),
        ("vision", b'{"clase":"vacia","confianza":1.2}'),
        ("vision", b'{"clase":"vacia","confianza":true}'),
        ("vision", b'{"clase":"vacia","confianza":NaN}'),
        ("vision", b"\xff\xfe"),
        ("clima", b'{"clase":"confort","confianza":0.9}'),
        ("clima", b'{"clase":"confort","confianza":0.9,"temperatura_c":200,"humedad_pct":5}'),
        ("otro", b'{"clase":"confort","confianza":0.9}'),
    ],
)
def test_contrato_rechaza_mensajes_invalidos(tipo: str, cuerpo: bytes) -> None:
    with pytest.raises(ContratoInvalidoError):
        decodificar(tipo, cuerpo)


def test_contrato_acepta_sin_modelo_y_campos_opcionales() -> None:
    assert decodificar("vision", b'{"clase":"sin_modelo","confianza":0}')["clase"] == "sin_modelo"
    datos = decodificar("movimiento", b'{"clase":"quieto","confianza":1,"ax":0.1,"extra":5}')
    assert datos["ax"] == 0.1
    assert "extra" not in datos and "ay" not in datos


def test_filtro_exige_tres_seguidas_con_confianza() -> None:
    filtro = FiltroAlertas()
    niveles = [filtro.nivel("vision", "cam", "boca_abajo", 0.9) for _ in range(3)]
    assert niveles == [0, 0, 2]
    # Una imagen suelta o dudosa no cambia el nivel confirmado.
    assert filtro.nivel("vision", "cam", "boca_arriba", 0.95) == 2
    assert filtro.nivel("vision", "cam", "boca_arriba", 0.4) == 2
    # La racha se reinicia tras la imagen dudosa.
    assert [filtro.nivel("vision", "cam", "boca_arriba", 0.9) for _ in range(3)] == [2, 2, 0]


def test_filtro_separa_dispositivos_y_se_reinicia_sin_modelo() -> None:
    filtro = FiltroAlertas(repeticiones=1)
    assert filtro.nivel("vision", "cam-a", "boca_abajo", 0.9) == 2
    assert filtro.nivel("vision", "cam-b", "boca_arriba", 0.9) == 0
    assert filtro.nivel("vision", "cam-a", "sin_modelo", 0.0) == 0


# ----------------------------------------------------------------------- HTTP
def test_http_respeta_el_contrato(servidor: ServidorFalso, capsys: Any) -> None:
    codigo = simulador.main(
        ["--url", servidor.url, "--count", "1", "--api-key", "secreta-123", "--temperatura", "30"]
    )
    assert codigo == 0
    peticion = servidor.peticiones[0]
    assert peticion["ruta"] == "/api/lecturas"
    assert peticion["tipo"] == "application/json"
    assert peticion["clave"] == "secreta-123"
    assert set(peticion["json"]) == {"dispositivo", "temperatura_c", "humedad_pct"}
    assert peticion["json"]["dispositivo"] == "esp32-dht11"
    assert peticion["json"]["temperatura_c"] == 30
    salida = capsys.readouterr().out
    assert "HTTP 201" in salida and "respuesta" in salida
    assert "secreta-123" not in salida


def test_http_reintenta_fallos_transitorios(servidor: ServidorFalso) -> None:
    servidor.codigos = [503, 500, 201]
    esperas: list[float] = []
    codigo, _ = simulador.enviar_con_reintentos(servidor.url, {}, dormir=esperas.append)
    assert codigo == 201
    assert esperas == [1.0, 2.0]


def test_http_no_reintenta_errores_del_cliente(servidor: ServidorFalso) -> None:
    servidor.codigos = [401]
    esperas: list[float] = []
    codigo, _ = simulador.enviar_con_reintentos(servidor.url, {}, dormir=esperas.append)
    assert codigo == 401
    assert esperas == []
    assert simulador.main(["--url", servidor.url, "--count", "1"]) == 1


def test_http_sin_servidor_no_lanza_excepcion() -> None:
    esperas: list[float] = []
    codigo, cuerpo = simulador.enviar_con_reintentos(
        "http://127.0.0.1:1/api/lecturas", {}, reintentos=2, dormir=esperas.append
    )
    assert codigo == 0
    assert cuerpo
    assert esperas == [1.0, 2.0]


def test_ctrl_c_termina_limpio(servidor: ServidorFalso, monkeypatch: Any, capsys: Any) -> None:
    def interrumpir(_: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(simulador.time, "sleep", interrumpir)
    assert simulador.main(["--url", servidor.url]) == 0
    assert "Detenido por el usuario" in capsys.readouterr().out


def test_espera_creciente_con_tope() -> None:
    assert [simulador.espera_reintento(n) for n in (1, 2, 3, 4, 9)] == [1, 2, 4, 8, 30]


def test_url_visible_oculta_credenciales() -> None:
    url = "http://usuario:clave@servidor:8000/api/lecturas?token=abc"
    assert simulador.url_visible(url) == "http://servidor:8000/api/lecturas"


@pytest.mark.parametrize("opcion", [["--count", "-1"], ["--interval", "-2"], ["--url", "ftp://x"]])
def test_opciones_invalidas(opcion: list[str]) -> None:
    with pytest.raises(SystemExit):
        simulador.crear_parser().parse_args(opcion)


# ----------------------------------------------------------------------- MQTT
class _Codigo:
    def __init__(self, texto: str, fallo: bool) -> None:
        self.texto, self.is_failure = texto, fallo

    def __str__(self) -> str:
        return self.texto


class _Info:
    def __init__(self, entregado: bool) -> None:
        self._entregado = entregado

    def wait_for_publish(self, espera: float) -> None:
        pass

    def is_published(self) -> bool:
        return self._entregado


class ClienteFalso:
    """Imita la parte de paho-mqtt que usa ``ConexionMqtt``."""

    def __init__(self, resultados: list[Any], entregado: bool = True) -> None:
        self.resultados = resultados
        self.entregado = entregado
        self.publicados: list[tuple[str, str, int]] = []
        self.intentos = 0
        self.tls: dict[str, Any] = {}
        self._pendiente: Any = None

    def username_pw_set(self, usuario: str, clave: str) -> None:
        self.usuario = usuario

    def tls_set(self, **opciones: Any) -> None:
        self.tls = opciones

    def reconnect_delay_set(self, **opciones: Any) -> None:
        self.espera = opciones

    def connect(self, host: str, port: int, keepalive: int) -> None:
        self.intentos += 1
        self._pendiente = self.resultados.pop(0)
        if isinstance(self._pendiente, Exception):
            raise self._pendiente

    def loop_start(self) -> None:
        if self._pendiente is not None:
            self.on_connect(self, None, None, self._pendiente, None)

    def loop_stop(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def publish(self, topico: str, cuerpo: str, qos: int) -> _Info:
        self.publicados.append((topico, cuerpo, qos))
        return _Info(self.entregado)


ACEPTADO = _Codigo("Success", False)


def _conexion(cliente: ClienteFalso, esperas: list[float]) -> publicador_mqtt.ConexionMqtt:
    return publicador_mqtt.ConexionMqtt(
        "broker", 1883, "prueba", cliente=cliente, dormir=esperas.append, espera=0.01
    )


def test_mqtt_reintenta_y_publica_con_qos1() -> None:
    cliente = ClienteFalso([ConnectionRefusedError("rechazada"), None, ACEPTADO])
    esperas: list[float] = []
    conexion = _conexion(cliente, esperas)
    conexion.conectar()
    assert (cliente.intentos, esperas) == (3, [1.0, 2.0])
    assert conexion.publicar("cuartobebe/temperatura", "21.00")
    assert cliente.publicados == [("cuartobebe/temperatura", "21.00", 1)]


def test_mqtt_credenciales_rechazadas_no_se_reintentan() -> None:
    cliente = ClienteFalso([_Codigo("Not authorized", True)])
    with pytest.raises(SystemExit, match="autenticar"):
        _conexion(cliente, []).conectar()
    assert cliente.intentos == 1


def test_mqtt_agota_reintentos_con_mensaje_claro() -> None:
    cliente = ClienteFalso([OSError("sin ruta")] * 3)
    with pytest.raises(SystemExit, match="No se pudo conectar"):
        _conexion(cliente, []).conectar(reintentos=2)


def test_mqtt_desconexion_y_mensaje_sin_confirmar(capsys: Any) -> None:
    cliente = ClienteFalso([ACEPTADO], entregado=False)
    conexion = _conexion(cliente, [])
    conexion.conectar()
    assert not conexion.publicar("a/b", "1")
    cliente.on_disconnect(cliente, None, None, _Codigo("Keep alive timeout", True), None)
    assert not conexion.publicar("a/b", "2")
    salida = capsys.readouterr().out
    assert "reconectando" in salida and "Sin conexion" in salida


def test_mqtt_formatos_de_mensaje() -> None:
    lectura = {"dispositivo": "esp32-dht11", "temperatura_c": 21.456, "humedad_pct": 50.0}
    assert publicador_mqtt.mensajes_dht11(lectura, "cuartobebe", "separado") == [
        ("cuartobebe/temperatura", "21.46"),
        ("cuartobebe/humedad", "50.00"),
    ]
    for formato, topico in (("json", "cuartobebe/sensores"), ("aws", "cuartobebe/telemetria")):
        ((publicado, cuerpo),) = publicador_mqtt.mensajes_dht11(lectura, "cuartobebe", formato)
        assert publicado == topico
        assert json.loads(cuerpo) == lectura


def test_aws_exige_certificados(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        publicador_mqtt.main(["--aws", "--host", "ejemplo", "--count", "1"])
    faltante = str(tmp_path / "no-existe.pem")
    with pytest.raises(SystemExit, match="No existe"):
        publicador_mqtt.main(
            ["--aws", "--host", "x", "--ca", faltante, "--cert", faltante, "--key", faltante]
        )
    with pytest.raises(SystemExit, match="requieren --tls"):
        publicador_mqtt.main(["--ca", faltante, "--count", "1"])


def test_tls_verifica_el_certificado_del_broker() -> None:
    cliente = ClienteFalso([ACEPTADO])
    publicador_mqtt.ConexionMqtt(
        "x", 8883, "c", tls=publicador_mqtt.OpcionesTls(Path("ca.pem")), cliente=cliente
    )
    assert cliente.tls["ca_certs"] == "ca.pem"
    assert cliente.tls["tls_version"].name == "PROTOCOL_TLS_CLIENT"


# --------------------------------------------- firmware del equipo (v3 y v4)
@pytest.mark.parametrize(
    ("tipo", "cuerpo", "clase"),
    [
        (
            "clima",
            b'{"dispositivo":"esp32-cuna","temperatura_c":27.4,"humedad_pct":52.0,'
            b'"clase_ambiente":"riesgo","confianza":0.91,"nivel":2,"alarma":true}',
            "riesgo",
        ),
        (
            "clima",
            b'{"dispositivo":"esp32-cuna","temperatura_c":20.0,"humedad_pct":50.0,'
            b'"clase_ambiente":"calentando","confianza":0,"nivel":0,"alarma":false}',
            "sin_modelo",
        ),
        (
            "movimiento",
            b'{"dispositivo":"esp32-cuna","clase_movimiento":"movimiento_brusco",'
            b'"confianza":0.88,"nivel":2,"alarma":true}',
            "brusco",
        ),
        ("movimiento", b'{"clase_movimiento":"Leve","confianza":0.7,"nivel":1}', "leve"),
        ("movimiento", b'{"clase_movimiento":"quieto","confianza":0.95,"nivel":0}', "quieto"),
        ("movimiento", b'{"clase_movimiento":"incierto","confianza":0.4,"nivel":0}', "sin_modelo"),
    ],
)
def test_acepta_los_mensajes_del_firmware_del_equipo(tipo: str, cuerpo: bytes, clase: str) -> None:
    assert decodificar(tipo, cuerpo)["clase"] == clase


def test_movimiento_brusco_es_critico() -> None:
    filtro = FiltroAlertas()
    niveles = [filtro.nivel("movimiento", "cuna", "brusco", 0.9) for _ in range(3)]
    assert niveles == [0, 0, 2]
    with pytest.raises(ContratoInvalidoError):
        decodificar("movimiento", b'{"clase_movimiento":"saltando","confianza":0.9}')
