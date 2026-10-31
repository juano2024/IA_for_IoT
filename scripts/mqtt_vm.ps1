<#
.SYNOPSIS
    Despliega el stack de la Practica 2 (MQTT + InfluxDB + Grafana) en la VM.

.DESCRIPTION
    Arranca la VM si hace falta, copia el proyecto a /home/vagrant/mqtt-app y
    levanta los contenedores definidos en mqtt/docker-compose.yml.

.EXAMPLE
    .\scripts\mqtt_vm.ps1

.EXAMPLE
    .\scripts\mqtt_vm.ps1 -Logs

.EXAMPLE
    .\scripts\mqtt_vm.ps1 -Down
#>
[CmdletBinding()]
param(
    [switch]$Down,
    [switch]$Logs
)

$ErrorActionPreference = "Stop"

if ($Down) {
    vagrant ssh -c "cd /home/vagrant/mqtt-app && docker compose -f mqtt/docker-compose.yml down"
    exit $LASTEXITCODE
}

if ($Logs) {
    vagrant ssh -c "cd /home/vagrant/mqtt-app && docker compose -f mqtt/docker-compose.yml logs -f --tail 80"
    exit $LASTEXITCODE
}

Write-Host "==> Asegurando que la VM este encendida" -ForegroundColor Cyan
vagrant up
if ($LASTEXITCODE -ne 0) {
    throw "No se pudo arrancar la VM. Revisa 'vagrant status'."
}

Write-Host "==> Sincronizando el proyecto con la VM" -ForegroundColor Cyan
vagrant ssh -c "rsync -a --delete --exclude '.git/' --exclude '.venv/' --exclude 'data/' --exclude '__pycache__/' --exclude '.pytest_cache/' --exclude '.ruff_cache/' /vagrant/ /home/vagrant/mqtt-app/ && cd /home/vagrant/mqtt-app && sudo mkdir -p data mqtt/notifier/data && sudo chown -R 1000:1000 data && sudo chown 1000:1000 mqtt/notifier/data && docker compose -f mqtt/docker-compose.yml up -d --build"

if ($LASTEXITCODE -ne 0) {
    throw "El despliegue MQTT fallo. Revisa los logs con: .\scripts\mqtt_vm.ps1 -Logs"
}

Write-Host ""
Write-Host "Servicios desplegados:" -ForegroundColor Green
Write-Host "  Grafana  : http://127.0.0.1:3000  (admin / admin)" -ForegroundColor Green
Write-Host "  InfluxDB : http://127.0.0.1:8086  (admin / admin12345)" -ForegroundColor Green
Write-Host "  MQTT     : mqtt://127.0.0.1:1883 (iot / iot2026)" -ForegroundColor Green
Write-Host ""
Write-Host "Enviar lecturas simuladas:" -ForegroundColor White
Write-Host "  uv run simular-esp32-mqtt --host 127.0.0.1 --count 20 --interval 1" -ForegroundColor Gray
Write-Host ""
Write-Host "Ver notificaciones de alerta:" -ForegroundColor White
Write-Host "  .\scripts\mqtt_vm.ps1 -Logs" -ForegroundColor Gray