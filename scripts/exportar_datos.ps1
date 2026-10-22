<#
.SYNOPSIS
    Descarga el CSV y la base de datos SQLite desde el servidor (VM o local).

.DESCRIPTION
    El endpoint /api/lecturas.csv entrega el CSV en texto plano. La base de
    datos SQLite es binaria, asi que se transfiere en base64 por SSH para no
    corromperla con la canalizacion de texto de PowerShell.

.EXAMPLE
    .\scripts\exportar_datos.ps1
#>
[CmdletBinding()]
param(
    [string]$Url = "http://127.0.0.1:8080",
    [string]$Destino = "data"
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Path $Destino -Force | Out-Null

# --- CSV ---------------------------------------------------------------
$rutaCsv = Join-Path $Destino "lecturas.csv"
try {
    Invoke-WebRequest -Uri "$Url/api/lecturas.csv" -OutFile $rutaCsv -UseBasicParsing -TimeoutSec 30
    $lineas = (Get-Content -LiteralPath $rutaCsv | Measure-Object -Line).Lines
    Write-Host "CSV descargado  -> $rutaCsv ($lineas lineas)" -ForegroundColor Green
} catch {
    Write-Warning "No se pudo descargar el CSV: $($_.Exception.Message)"
}

# --- Base de datos SQLite ---------------------------------------------
$rutaDb = Join-Path $Destino "ia_for_iot.db"
$rutaTemp = Join-Path $Destino "ia_for_iot.db.tmp"
try {
    $base64 = vagrant ssh -c "sudo base64 -w0 /home/vagrant/app/data/ia_for_iot.db" 2>$null
    if ($base64) {
        $texto = ($base64 -join "").Trim()
        [System.IO.File]::WriteAllBytes(
            (Join-Path (Resolve-Path $Destino) "ia_for_iot.db.tmp"),
            [System.Convert]::FromBase64String($texto)
        )
        Move-Item -LiteralPath $rutaTemp -Destination $rutaDb -Force
        Write-Host "SQLite descargado -> $rutaDb" -ForegroundColor Green
    } else {
        Write-Warning "No se pudo descargar la base de datos (la VM deberia estar encendida)."
    }
} catch {
    Write-Warning "No se pudo descargar la base de datos: $($_.Exception.Message)"
    Write-Warning "Si el archivo esta en uso, detiene el servidor local (puerto 8000) y reintenta."
    if (Test-Path -LiteralPath $rutaTemp) { Remove-Item -LiteralPath $rutaTemp -Force -ErrorAction SilentlyContinue }
}
