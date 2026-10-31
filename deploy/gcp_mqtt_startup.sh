#!/usr/bin/env bash
# Startup script de Compute Engine para la Practica 2 (MQTT + InfluxDB + Grafana).
#
# Se ejecuta en cada arranque de la VM y es idempotente: nunca borra datos.
#   1. Instala Docker Engine y Docker Compose si faltan.
#   2. Clona el repositorio o actualiza la rama indicada en los metadatos.
#   3. Escribe mqtt/.env con las claves guardadas en los metadatos de la VM.
#   4. Levanta el stack con el override de GCP y espera a Grafana.
#
# Metadatos que lee (los crea scripts/gcp_practica2.ps1):
#   repo-branch, mqtt-username, mqtt-password, influx-password,
#   influx-token, grafana-password

set -euo pipefail
exec > >(tee -a /var/log/startup-script.log) 2>&1
echo "=== [$(date)] Arranque de la Practica 2 ==="

REPO_URL="https://github.com/juano2024/IA_for_IoT.git"
APP_DIR=/opt/ia_for_iot

metadata() {
  curl -fsS -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"
}

RAMA="$(metadata repo-branch 2>/dev/null || echo practica-2-mqtt)"
MQTT_USERNAME="$(metadata mqtt-username)"
MQTT_PASSWORD="$(metadata mqtt-password)"
INFLUX_ADMIN_PASSWORD="$(metadata influx-password)"
INFLUX_TOKEN="$(metadata influx-token)"
GRAFANA_ADMIN_PASSWORD="$(metadata grafana-password)"

# 1. Docker Engine y Docker Compose
if ! command -v docker >/dev/null 2>&1; then
  echo "=== Instalando Docker ==="
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y ca-certificates curl gnupg git

  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc

  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list

  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
else
  echo "=== Docker ya instalado: $(docker --version) ==="
fi

# Memoria de intercambio: margen para construir la imagen en VMs pequenas.
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
swapon /swapfile 2>/dev/null || true

# 2. Codigo: clonar o actualizar sin tocar data/ ni mqtt/.env (ignorados por git)
if [ -d "$APP_DIR/.git" ]; then
  echo "=== Actualizando la rama $RAMA ==="
  git -C "$APP_DIR" fetch origin "$RAMA"
  git -C "$APP_DIR" checkout -B "$RAMA" "origin/$RAMA"
else
  echo "=== Clonando la rama $RAMA ==="
  git clone --branch "$RAMA" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

# 3. Claves del stack
umask 077
cat > mqtt/.env <<EOF
MQTT_USERNAME=${MQTT_USERNAME}
MQTT_PASSWORD=${MQTT_PASSWORD}
INFLUX_ADMIN_USER=admin
INFLUX_ADMIN_PASSWORD=${INFLUX_ADMIN_PASSWORD}
INFLUX_TOKEN=${INFLUX_TOKEN}
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}
EOF
umask 022

# El puente corre con el UID 1000 y escribe el CSV en data/.
mkdir -p data mqtt/notifier/data
chown -R 1000:1000 data mqtt/notifier/data

# 4. Levantar el stack
echo "=== Levantando el stack MQTT ==="
docker compose -f mqtt/docker-compose.yml -f mqtt/docker-compose.gcp.yml up -d --build

for i in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:3000/api/health >/dev/null 2>&1; then
    echo "=== Stack listo: Grafana responde en el puerto 3000 ==="
    docker compose -f mqtt/docker-compose.yml -f mqtt/docker-compose.gcp.yml ps
    exit 0
  fi
  echo "Esperando a Grafana ($i/90)..."
  sleep 2
done

echo "ADVERTENCIA: Grafana no respondio a tiempo."
docker compose -f mqtt/docker-compose.yml -f mqtt/docker-compose.gcp.yml logs --tail 50
exit 1
