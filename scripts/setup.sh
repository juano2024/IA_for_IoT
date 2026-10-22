#!/usr/bin/env bash
#
# Instalacion automatica del laboratorio "IA for IoT" en macOS y Linux.
#
#   ./scripts/setup.sh                 # detecta el mejor modo disponible
#   ./scripts/setup.sh --docker        # fuerza Docker directo (mas portable)
#   ./scripts/setup.sh --vagrant       # fuerza VM Ubuntu con Vagrant
#   ./scripts/setup.sh --verificar     # solo comprueba el estado
#   ./scripts/setup.sh --sin-arduino   # no instala el entorno del ESP32
#
# Es idempotente: se puede ejecutar varias veces sin romper nada.

set -euo pipefail

MODO="auto"
SIN_ARDUINO=0
VERIFICAR=0
API="http://localhost:8080"

PASO=0
total_pasos=6

info()  { printf '\n[%d/%d] %s\n' "$PASO" "$total_pasos" "$1"; }
ok()    { printf '     OK  %s\n' "$1"; }
aviso() { printf '     !!  %s\n' "$1"; }
error() { printf '    ERR  %s\n' "$1" >&2; }

para_argumentos() {
  for arg in "$@"; do
    case "$arg" in
      --docker)       MODO="docker" ;;
      --vagrant)      MODO="vagrant" ;;
      --verificar)    VERIFICAR=1 ;;
      --sin-arduino)  SIN_ARDUINO=1 ;;
      -h|--help)      sed -n '2,16p' "$0"; exit 0 ;;
      *) error "Opcion desconocida: $arg"; exit 2 ;;
    esac
  done
}

tiene() { command -v "$1" >/dev/null 2>&1; }

instalar_uv() {
  if tiene uv; then
    ok "uv $(uv --version)"
    return
  fi
  aviso "uv no esta instalado."
  if [ "$VERIFICAR" -eq 1 ]; then return; fi
  if tiene brew; then
    brew install uv
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # El instalador deja el binario en ~/.local/bin o ~/.cargo/bin
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  fi
  if tiene uv; then ok "uv $(uv --version)"; else error "No se pudo instalar uv."; exit 1; fi
}

instalar_arduino_cli() {
  if tiene arduino-cli; then
    ok "arduino-cli $(arduino-cli version | awk '{print $3}')"
  else
    aviso "Arduino CLI no esta instalado."
    if [ "$VERIFICAR" -eq 1 ]; then return; fi
    if tiene brew; then
      brew install arduino-cli
    else
      mkdir -p "$HOME/.local/bin"
      curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh \
        | BINDIR="$HOME/.local/bin" sh
      export PATH="$HOME/.local/bin:$PATH"
    fi
    if tiene arduino-cli; then ok "Arduino CLI instalado"; else error "No se pudo instalar Arduino CLI."; return; fi
  fi

  if [ "$VERIFICAR" -eq 1 ]; then return; fi

  arduino-cli config init --overwrite >/dev/null 2>&1 || true
  arduino-cli config add board_manager.additional_urls \
    https://espressif.github.io/arduino-esp32/package_esp32_index.json >/dev/null 2>&1 || true
  arduino-cli core update-index >/dev/null 2>&1 || true

  if ! arduino-cli core list | grep -q "esp32:esp32"; then
    aviso "Instalando core esp32 (~1 GB, puede tardar)..."
    arduino-cli core install esp32:esp32 >/dev/null
  fi
  ok "Core esp32 disponible"

  if ! arduino-cli lib list | grep -q "DHT sensor library"; then
    arduino-cli lib install "DHT sensor library" >/dev/null
  fi
  ok "Libreria DHT disponible"

  aviso "Compilando el firmware..."
  if arduino-cli compile --fqbn esp32:esp32:esp32 firmware/esp32_dht11 >/dev/null; then
    ok "Firmware compila correctamente"
  else
    aviso "El firmware no compilo."
  fi
}

esperar_api() {
  for _ in $(seq 1 30); do
    if curl -fsS "$API/api/health" >/dev/null 2>&1; then
      ok "API respondiendo en $API"
      return 0
    fi
    sleep 3
  done
  aviso "La API no respondio en $API. Revisa los logs del contenedor."
  return 1
}

# ----------------------------------------------------------------------
# 0. Contexto
# ----------------------------------------------------------------------
para_argumentos "$@"

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

SO="$(uname -s)"
printf '======================================================\n'
printf '  IA for IoT - Instalacion automatica (%s)\n' "$SO"
printf '======================================================\n'
printf '  Proyecto: %s\n' "$RAIZ"

if [ ! -f Vagrantfile ] || [ ! -f pyproject.toml ]; then
  error "No parece la carpeta del proyecto (falta Vagrantfile o pyproject.toml)."
  exit 1
fi

if [ "$MODO" = "auto" ]; then
  if tiene docker && docker compose version >/dev/null 2>&1; then
    MODO="docker"
  elif tiene vagrant; then
    MODO="vagrant"
  else
    error "No hay Docker ni Vagrant disponibles."
    error "Instala Docker Desktop (macOS) o Docker Engine (Linux) y vuelve a intentarlo:"
    error "  https://docs.docker.com/get-docker/"
    exit 1
  fi
fi
ok "Modo seleccionado: $MODO"

# ----------------------------------------------------------------------
# 1. uv
# ----------------------------------------------------------------------
PASO=1; info "Entorno de Python (uv)"
instalar_uv
if tiene uv && [ "$VERIFICAR" -eq 0 ]; then
  uv sync
  ok "Dependencias instaladas en .venv"
fi

# ----------------------------------------------------------------------
# 2. Dependencias del servidor
# ----------------------------------------------------------------------
PASO=2; info "Servidor"

if [ "$MODO" = "docker" ]; then
  if ! docker compose version >/dev/null 2>&1; then
    error "Docker Compose no esta disponible."; exit 1
  fi
  ok "Docker $(docker --version | awk '{print $3}' | tr -d ,) con Compose"
else
  if ! tiene vagrant; then
    aviso "Vagrant no esta instalado."
    if tiene brew; then
      brew install hashicorp/tap/hashicorp-vagrant
    elif tiene apt-get; then
      sudo apt-get update
      sudo apt-get install -y vagrant
    else
      error "Instala Vagrant manualmente: https://developer.hashicorp.com/vagrant/downloads"
      exit 1
    fi
  fi
  ok "Vagrant $(vagrant --version | awk '{print $2}')"

  if [ "$SO" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    aviso "Apple Silicon: VirtualBox tiene soporte limitado. Se recomienda --docker."
  fi
fi

# ----------------------------------------------------------------------
# 3. Imagen / configuracion
# ----------------------------------------------------------------------
PASO=3; info "Preparando recursos"
if [ "$MODO" = "vagrant" ] && [ "$VERIFICAR" -eq 0 ]; then
  if vagrant box list 2>/dev/null | grep -q "ubuntu/jammy64"; then
    ok "Box ubuntu/jammy64 ya descargada"
  else
    aviso "Descargando la box (~600 MB)..."
    vagrant box add ubuntu/jammy64 --provider virtualbox
    ok "Box descargada"
  fi
else
  ok "Sin recursos adicionales que descargar"
fi

# ----------------------------------------------------------------------
# 4. Levantar el servidor
# ----------------------------------------------------------------------
PASO=4; info "Levantando el servidor"
if [ "$VERIFICAR" -eq 0 ]; then
  if [ "$MODO" = "docker" ]; then
    docker compose up -d --build
    ok "Contenedor levantado"
  else
    aviso "Creando la VM e instalando Docker dentro (la primera vez tarda)..."
    vagrant up
    ok "VM levantada"
  fi
else
  aviso "Modo verificacion: no se levanta nada"
fi

# ----------------------------------------------------------------------
# 5. Verificacion
# ----------------------------------------------------------------------
PASO=5; info "Verificando la API"
esperar_api || true

# ----------------------------------------------------------------------
# 6. Arduino
# ----------------------------------------------------------------------
PASO=6; info "Entorno de Arduino (ESP32)"
if [ "$SIN_ARDUINO" -eq 1 ]; then
  aviso "Omitido por --sin-arduino"
else
  instalar_arduino_cli
fi

# ----------------------------------------------------------------------
# Resumen
# ----------------------------------------------------------------------
printf '\n======================================================\n'
printf '  Listo\n'
printf '======================================================\n\n'
printf '  Panel web : %s/\n' "$API"
printf '  API docs  : %s/docs\n' "$API"
printf '  Salud     : %s/api/health\n\n' "$API"
printf '  Probar sin hardware:\n'
printf '    uv run simular-esp32 --url %s/api/lecturas --count 20 --interval 2\n\n' "$API"
printf '  Detener el servidor:\n'
if [ "$MODO" = "docker" ]; then
  printf '    docker compose down\n\n'
else
  printf '    vagrant halt\n\n'
fi
