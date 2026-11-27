<#
.SYNOPSIS
    Levanta la plataforma de la Actividad 4 para la demo en clase.

.DESCRIPTION
    Sin opciones: levanta Mosquitto, InfluxDB, Grafana, el puente y el
    notificador, espera a que respondan y muestra los datos que necesitan las
    placas y el cliente MCP.

    -McpSimulado  Abre ademas la copia del servidor MCP (puerto 8090) con los
                  datos del broker. Sirve si la placa no responde por MCP.
    -Simular      Abre ademas los simuladores del ESP32 y de la ESP32-CAM.
                  Sus datos llevan "-sim" y no son evidencia de las placas.
    -Down         Detiene la plataforma (los datos se conservan).

.EXAMPLE
    .\scripts\demo_actividad4.ps1
    .\scripts\demo_actividad4.ps1 -McpSimulado
    .\scripts\demo_actividad4.ps1 -Down
#>
[CmdletBinding()]
param(
    [switch]$McpSimulado,
    [switch]$Simular,
    [switch]$Down
)

$ErrorActionPreference = "Stop"
$Raiz = Split-Path -Parent $PSScriptRoot
$Compose = Join-Path $Raiz "mqtt\docker-compose.yml"
$EnvFile = Join-Path $Raiz "mqtt\.env"

function Invoke-Docker {
    # Docker escribe el progreso en stderr: solo cuenta el codigo de salida.
    $ErrorActionPreference = "Continue"
    docker @args 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw "Fallo: docker $($args -join ' ')" }
}

function Get-Valor([string]$nombre) {
    $linea = Get-Content $EnvFile | Where-Object { $_ -match "^$nombre=" } | Select-Object -First 1
    if ($linea) { return ($linea -split "=", 2)[1].Trim() }
    return ""
}

function Esperar-Url([string]$url, [int]$segundos) {
    for ($i = 0; $i -lt $segundos; $i += 2) {
        try {
            Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3 | Out-Null
            return $true
        } catch { Start-Sleep 2 }
    }
    return $false
}

if ($Down) {
    Invoke-Docker compose -f $Compose down
    Write-Host "Plataforma detenida. Los datos siguen en los volumenes de Docker." -ForegroundColor Green
    exit 0
}

if (-not (Test-Path $EnvFile)) {
    throw "Falta mqtt\.env: copia mqtt\.env.example a mqtt\.env y cambia las claves."
}

Write-Host "==> Levantando la plataforma" -ForegroundColor Cyan
# Solo se construye la imagen si falta: asi la demo no depende de Internet.
$ErrorActionPreference = "Continue"
docker image inspect ia-for-iot:latest *> $null
$hayImagen = $LASTEXITCODE -eq 0
$ErrorActionPreference = "Stop"
$construir = if ($hayImagen) { @() } else { @("--build") }
Invoke-Docker compose -f $Compose up -d @construir | Select-Object -Last 6
if (-not (Esperar-Url "http://127.0.0.1:3000/api/health" 120)) {
    throw "Grafana no respondio. Revisa: docker compose -f mqtt/docker-compose.yml logs grafana"
}

$ErrorActionPreference = "Continue"
$telegram = docker logs ia_for_iot_notifier 2>&1 | Select-String "Telegram:" | Select-Object -Last 1
$ErrorActionPreference = "Stop"
$ip = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.InterfaceAlias -notmatch "vEthernet|Loopback|WSL" -and $_.IPAddress -notlike "169.254*" } |
    Select-Object -ExpandProperty IPAddress -First 1

$procesos = @()
$env:MQTT_USERNAME = Get-Valor "MQTT_USERNAME"
$env:MQTT_PASSWORD = Get-Valor "MQTT_PASSWORD"
if ($McpSimulado) {
    $procesos += Start-Process uv -ArgumentList "run", "mcp-esp32-simulado" -WorkingDirectory $Raiz -PassThru
}
if ($Simular) {
    $procesos += Start-Process uv -ArgumentList "run", "simular-esp32-mqtt", "--edge" -WorkingDirectory $Raiz -PassThru
    $procesos += Start-Process uv -ArgumentList "run", "simular-esp32cam" -WorkingDirectory $Raiz -PassThru
}

Write-Host ""
Write-Host "Plataforma lista" -ForegroundColor Green
Write-Host "  Grafana      : http://127.0.0.1:3000/d/ia-iot-edge  (admin / GRAFANA_ADMIN_PASSWORD de mqtt\.env)"
Write-Host "  Telegram     : $(if ($telegram) { ($telegram.Line -replace '^Telegram: ', '') } else { 'sin dato' })"
Write-Host ""
Write-Host "Para secrets.h de las placas:" -ForegroundColor Cyan
Write-Host "  MQTT_HOST    : $ip   (el portatil y las placas en la misma red 2.4 GHz)"
Write-Host "  MQTT_USER    : $env:MQTT_USERNAME"
Write-Host "  MQTT_PASS    : MQTT_PASSWORD de mqtt\.env (compartela por un canal privado)"
Write-Host ""
Write-Host "Cliente MCP (VS Code, .vscode\mcp.json):" -ForegroundColor Cyan
Write-Host "  Placa        : http://IP_DEL_ESP32/mcp  (la imprime su Monitor Serial)"
if ($McpSimulado) { Write-Host "  Simulado     : http://127.0.0.1:8090/mcp" }
if ($procesos) {
    Write-Host ""
    Write-Host "Ventanas abiertas: $($procesos.Count). Cierralas con Ctrl+C al terminar." -ForegroundColor Yellow
}
