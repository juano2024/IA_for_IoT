<#
.SYNOPSIS
    Despliega, revisa o elimina el stack de la Practica 2 en Google Cloud.

.DESCRIPTION
    -Up     Crea (si hace falta) el proyecto, la IP estatica, las reglas de
            firewall y la VM. La VM se configura sola con
            deploy/gcp_mqtt_startup.sh y las claves de .env.gcp.
    -Status Muestra la VM y el estado de los contenedores (SSH por IAP).
    -Down   Elimina el proyecto completo (solo proyectos ia-iot-p2-*).

    Las claves se guardan en .env.gcp (ignorado por git). Si el archivo no
    existe, -Up lo genera con valores aleatorios.

.EXAMPLE
    .\scripts\gcp_practica2.ps1 -Up -ProjectId ia-iot-p2-2026 -BillingAccount 0142B9-33AEF0-17B7AA

.EXAMPLE
    .\scripts\gcp_practica2.ps1 -Status -ProjectId ia-iot-p2-2026

.EXAMPLE
    .\scripts\gcp_practica2.ps1 -Down -ProjectId ia-iot-p2-2026
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [string]$BillingAccount = "",
    [string]$Zone = "us-central1-a",
    # e2-micro entra en la capa gratuita; el stack usa ~250 MB y el startup
    # script agrega 2 GB de intercambio para construir la imagen.
    [string]$MachineType = "e2-micro",
    [string]$Branch = "practica-2-mqtt",
    [switch]$Up,
    [switch]$Status,
    [switch]$Down
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$SecretsFile = Join-Path $RepoRoot ".env.gcp"
$StartupScript = Join-Path $RepoRoot "deploy\gcp_mqtt_startup.sh"
$Region = $Zone.Substring(0, $Zone.LastIndexOf("-"))
$Vm = "iot-p2-vm"
$Ip = "iot-p2-ip"
$Tag = "iot-p2"

function Invoke-Gcloud {
    # Ejecuta gcloud y falla si el comando devuelve un codigo distinto de 0.
    gcloud @args
    if ($LASTEXITCODE -ne 0) { throw "Fallo: gcloud $($args -join ' ')" }
}

function Test-Gcloud {
    # Devuelve $true si el comando gcloud termina bien (sin mostrar salida).
    gcloud @args *> $null
    return ($LASTEXITCODE -eq 0)
}

function New-Clave {
    # Clave aleatoria alfanumerica (sin simbolos, para metadatos y .env).
    $letras = [char[]]"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return -join ($bytes | ForEach-Object { $letras[$_ % $letras.Length] })
}

function Get-Claves {
    if (-not (Test-Path $SecretsFile)) {
        Write-Host "==> Generando claves nuevas en .env.gcp" -ForegroundColor Cyan
        @(
            "MQTT_USERNAME=iot",
            "MQTT_PASSWORD=$(New-Clave)",
            "INFLUX_ADMIN_PASSWORD=$(New-Clave)",
            "INFLUX_TOKEN=$(New-Clave)",
            "GRAFANA_ADMIN_PASSWORD=$(New-Clave)"
        ) | Set-Content -Path $SecretsFile -Encoding ascii
    }
    $claves = @{}
    foreach ($linea in Get-Content $SecretsFile) {
        if ($linea -match "^\s*([A-Z_]+)=(.*)$") { $claves[$Matches[1]] = $Matches[2].Trim() }
    }
    return $claves
}

if ($Down) {
    if ($ProjectId -notlike "ia-iot-p2-*") {
        throw "Por seguridad, -Down solo elimina proyectos con nombre ia-iot-p2-*."
    }
    Write-Host "==> Eliminando el proyecto $ProjectId" -ForegroundColor Yellow
    Invoke-Gcloud projects delete $ProjectId --quiet
    Write-Host "Proyecto marcado para eliminacion. Ya no genera costos." -ForegroundColor Green
    exit 0
}

if ($Status) {
    Invoke-Gcloud compute instances list --project $ProjectId
    Invoke-Gcloud compute ssh $Vm --project $ProjectId --zone $Zone --tunnel-through-iap --quiet `
        --command "sudo tail -n 5 /var/log/startup-script.log; cd /opt/ia_for_iot && sudo docker compose -f mqtt/docker-compose.yml -f mqtt/docker-compose.gcp.yml ps"
    exit 0
}

if (-not $Up) {
    throw "Indica una accion: -Up, -Status o -Down."
}

# 1. Proyecto, facturacion y APIs
if (-not (Test-Gcloud projects describe $ProjectId)) {
    if (-not $BillingAccount) { throw "El proyecto no existe: indica -BillingAccount para crearlo." }
    Write-Host "==> Creando el proyecto $ProjectId" -ForegroundColor Cyan
    Invoke-Gcloud projects create $ProjectId --name "IA for IoT Practica 2"
    Invoke-Gcloud billing projects link $ProjectId --billing-account $BillingAccount
}
Write-Host "==> Habilitando Compute Engine e IAP" -ForegroundColor Cyan
Invoke-Gcloud services enable compute.googleapis.com iap.googleapis.com --project $ProjectId

# 2. IP externa estatica
if (-not (Test-Gcloud compute addresses describe $Ip --region $Region --project $ProjectId)) {
    Write-Host "==> Reservando la IP estatica" -ForegroundColor Cyan
    Invoke-Gcloud compute addresses create $Ip --region $Region --project $ProjectId
}
$IpPublica = (gcloud compute addresses describe $Ip --region $Region --project $ProjectId --format "value(address)").Trim()

# 3. Firewall: MQTT y Grafana abiertos; SSH solo por IAP; InfluxDB cerrado
if (-not (Test-Gcloud compute firewall-rules describe "$Tag-mqtt-grafana" --project $ProjectId)) {
    Write-Host "==> Creando reglas de firewall" -ForegroundColor Cyan
    Invoke-Gcloud compute firewall-rules create "$Tag-mqtt-grafana" --project $ProjectId `
        --network default --direction INGRESS --allow "tcp:1883,tcp:3000" `
        --source-ranges "0.0.0.0/0" --target-tags $Tag
    Invoke-Gcloud compute firewall-rules create "$Tag-ssh-iap" --project $ProjectId `
        --network default --direction INGRESS --allow "tcp:22" `
        --source-ranges "35.235.240.0/20" --target-tags $Tag
}
foreach ($regla in @("default-allow-ssh", "default-allow-rdp")) {
    if (Test-Gcloud compute firewall-rules describe $regla --project $ProjectId) {
        Invoke-Gcloud compute firewall-rules delete $regla --project $ProjectId --quiet
    }
}

# 4. VM con el startup script y las claves en los metadatos
$claves = Get-Claves
if (-not (Test-Gcloud compute instances describe $Vm --zone $Zone --project $ProjectId)) {
    Write-Host "==> Creando la VM $Vm ($MachineType)" -ForegroundColor Cyan
    $metadatos = @(
        "repo-branch=$Branch",
        "mqtt-username=$($claves['MQTT_USERNAME'])",
        "mqtt-password=$($claves['MQTT_PASSWORD'])",
        "influx-password=$($claves['INFLUX_ADMIN_PASSWORD'])",
        "influx-token=$($claves['INFLUX_TOKEN'])",
        "grafana-password=$($claves['GRAFANA_ADMIN_PASSWORD'])"
    ) -join ","
    Invoke-Gcloud compute instances create $Vm --project $ProjectId --zone $Zone `
        --machine-type $MachineType --image-family ubuntu-2204-lts --image-project ubuntu-os-cloud `
        --boot-disk-size 20GB --boot-disk-type pd-standard --tags $Tag --address $IpPublica `
        --metadata-from-file "startup-script=$StartupScript" --metadata $metadatos
}

Write-Host ""
Write-Host "VM creada. El stack tarda unos minutos en quedar listo." -ForegroundColor Green
Write-Host "  MQTT    : mqtt://${IpPublica}:1883  (usuario y clave en .env.gcp)" -ForegroundColor Green
Write-Host "  Grafana : http://${IpPublica}:3000  (admin / GRAFANA_ADMIN_PASSWORD)" -ForegroundColor Green
Write-Host ""
Write-Host "Revisar el arranque:" -ForegroundColor White
Write-Host "  .\scripts\gcp_practica2.ps1 -Status -ProjectId $ProjectId" -ForegroundColor Gray
