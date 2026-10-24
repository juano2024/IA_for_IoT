#!/usr/bin/env bash
# Startup script para Google Compute Engine (Ubuntu 22.04 LTS)
# Instala Docker, descarga el repositorio IA_for_IoT y levanta el servidor web.

set -euo pipefail
exec > >(tee -a /var/log/startup-script.log) 2>&1
echo "=== [$(date)] Iniciando aprovisionamiento en Google Cloud ==="

# 1. Instalar Docker Engine y Docker Compose
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

# 2. Descargar el proyecto
echo "=== Clonando repositorio ==="
rm -rf /opt/ia_for_iot
git clone https://github.com/juano2024/IA_for_IoT.git /opt/ia_for_iot
cd /opt/ia_for_iot
mkdir -p /opt/ia_for_iot/data
chown -R 1000:1000 /opt/ia_for_iot/data
chmod -R 775 /opt/ia_for_iot/data

# 3. Levantar contenedor con Docker Compose
echo "=== Levantando contenedor Docker ==="
docker compose up -d --build

# 4. Esperar a que la API responda
echo "=== Verificando estado del servidor ==="
for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
    echo "=== Servidor listo y respondiendo en el puerto 8080 ==="
    exit 0
  fi
  echo "Esperando que el servicio inicie ($i/60)..."
  sleep 2
done

echo "ADVERTENCIA: El servicio no respondió a tiempo."
docker compose logs --tail 50
