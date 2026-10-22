#!/usr/bin/env bash
# Provisioner de Vagrant: prepara el servidor Ubuntu del laboratorio.
#   1. Instala Docker Engine y el plugin de Docker Compose.
#   2. Copia el proyecto a /home/vagrant/app (sistema de archivos nativo).
#   3. Construye la imagen y levanta el contenedor de la API.
#   4. Espera a que el servidor responda en /api/health.
#
# Es idempotente: se puede volver a ejecutar con `vagrant provision`.

set -euo pipefail

echo "==> [1/4] Docker Engine + Docker Compose"

if ! command -v docker >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y ca-certificates curl gnupg rsync

  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc

  # shellcheck disable=SC1091
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list

  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
  echo "    Docker $(docker --version)"
else
  echo "    Docker ya instalado: $(docker --version)"
fi

# El usuario vagrant necesita el grupo docker para usar el socket sin sudo.
usermod -aG docker vagrant

echo "==> [2/4] Copiando el proyecto a /home/vagrant/app"
mkdir -p /home/vagrant/app
rsync -a --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude 'data/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  /vagrant/ /home/vagrant/app/
mkdir -p /home/vagrant/app/data
chown -R vagrant:vagrant /home/vagrant/app

echo "==> [3/4] Construyendo la imagen y levantando el contenedor"
cd /home/vagrant/app
docker compose up -d --build

echo "==> [4/4] Verificando el servidor"
for intento in $(seq 1 45); do
  if curl -fsS http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
    echo "    Servidor listo: http://127.0.0.1:8080 (host: http://127.0.0.1:8080)"
    exit 0
  fi
  sleep 2
done

echo "El servidor no respondio a tiempo. Logs del contenedor:" >&2
docker compose logs --tail 60 >&2
exit 1
