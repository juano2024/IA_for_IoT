#Requires -Version 5.1
<#
.SYNOPSIS
    Instalacion automatica del laboratorio "IA for IoT" en Windows.

.DESCRIPTION
    Deja el servidor Ubuntu funcionando de punta a punta:
      1. Verifica/instala uv y las dependencias de Python.
      2. Verifica/instala VirtualBox y Vagrant.
      3. Descarga la imagen base de Ubuntu (si falta).
      4. Crea la VM, instala Docker y levanta el contenedor.
      5. Comprueba que la API responda en http://127.0.0.1:8080/api/health.
      6. Instala el entorno de Arduino para el ESP32 (opcional).

    Es idempotente: se puede ejecutar varias veces sin romper nada.

.PARAMETER SinArduino
    Omite la instalacion de Arduino CLI, el core ESP32 y la libreria DHT.

.PARAMETER SoloVerificar
    No instala ni levanta nada; solo comprueba el estado actual.

.EXAMPLE
    .\scripts\setup.ps1

.EXAMPLE
    .\scripts\setup.ps1 -SinArduino
#>
[CmdletBinding()]
param(
    [switch]$SinArduino,
    [switch]$SoloVerificar
)

$ErrorActionPreference = "Stop"

# ----------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------
function Write-Paso {
    param([string]$Numero, [string]$Texto)
    Write-Host ""
    Write-Host "[$Numero] $Texto" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Texto)
    Write-Host "     OK  $Texto" -ForegroundColor Green
}

function Write-Aviso {
    param([string]$Texto)
    Write-Host "     !!  $Texto" -ForegroundColor Yellow
}

function Test-Comando {
    param([string]$Nombre)
    return [bool](Get-Command $Nombre -ErrorAction SilentlyContinue)
}

function Update-SessionPath {
    # winget actualiza el PATH de la maquina, pero la sesion actual conserva el viejo.
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
    $vbox = [Environment]::GetEnvironmentVariable("VBOX_MSI_INSTALL_PATH", "Machine")
    if ($vbox) { $env:VBOX_MSI_INSTALL_PATH = $vbox }
}

function Install-Winget {
    param([string]$Id, [string]$Nombre, [string]$ComandoEsperado)

    if (-not (Test-Comando "winget")) {
        throw "winget no esta disponible. Instala $Nombre manualmente y vuelve a ejecutar el script."
    }

    Write-Host "     Instalando $Nombre ..." -ForegroundColor DarkGray
    & winget install --id $Id -e --accept-package-agreements --accept-source-agreements --disable-interactivity
    Update-SessionPath

    if ($ComandoEsperado -and -not (Test-Comando $ComandoEsperado) -and -not (Test-Path $ComandoEsperado)) {
        throw "No se pudo confirmar la instalacion de $Nombre (winget salio con $LASTEXITCODE)."
    }
}

function Get-ArduinoCli {
    $rutas = @(
        "C:\Program Files\Arduino CLI\arduino-cli.exe",
        "$env:LOCALAPPDATA\Programs\Arduino CLI\arduino-cli.exe"
    )
    foreach ($ruta in $rutas) {
        if (Test-Path -LiteralPath $ruta) { return $ruta }
    }
    if (Test-Comando "arduino-cli") { return "arduino-cli" }
    return $null
}

# ----------------------------------------------------------------------
# 0. Contexto
# ----------------------------------------------------------------------
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $raiz

Write-Host "======================================================" -ForegroundColor Magenta
Write-Host "  IA for IoT - Instalacion automatica (Windows)" -ForegroundColor Magenta
Write-Host "======================================================" -ForegroundColor Magenta
Write-Host "  Directorio del proyecto: $raiz"

if (-not (Test-Path -LiteralPath (Join-Path $raiz "Vagrantfile"))) {
    throw "No se encontro el Vagrantfile. Ejecuta este script desde la carpeta del proyecto."
}
if (-not (Test-Path -LiteralPath (Join-Path $raiz "pyproject.toml"))) {
    throw "No se encontro pyproject.toml. Estas en la carpeta correcta del repositorio?"
}

# ----------------------------------------------------------------------
# 1. uv y dependencias de Python
# ----------------------------------------------------------------------
Write-Paso "1/6" "Entorno de Python (uv)"

if (-not (Test-Comando "uv")) {
    Write-Aviso "uv no esta instalado."
    if (-not $SoloVerificar) {
        Install-Winget -Id "astral-sh.uv" -Nombre "uv" -ComandoEsperado "uv"
    }
}

if (Test-Comando "uv") {
    Write-Ok (& uv --version)
    if (-not $SoloVerificar) {
        Write-Host "     Instalando dependencias con uv sync ..." -ForegroundColor DarkGray
        & uv sync
        if ($LASTEXITCODE -ne 0) { throw "uv sync fallo." }
        Write-Ok "Dependencias instaladas en .venv"
    }
} elseif ($SoloVerificar) {
    Write-Aviso "uv no encontrado (modo verificacion)."
}

# ----------------------------------------------------------------------
# 2. VirtualBox
# ----------------------------------------------------------------------
Write-Paso "2/6" "VirtualBox (hipervisor)"

$vboxExe = "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
$vboxInstalado = (Test-Path -LiteralPath $vboxExe) -or (Test-Comando "VBoxManage")
$actualizarVbox = $false

if ($vboxInstalado) {
    $version = "desconocida"
    try {
        if (Test-Path -LiteralPath $vboxExe) {
            $version = (& $vboxExe --version 2>$null)
        } else {
            $version = (& VBoxManage --version 2>$null)
        }
    } catch { }
    Write-Ok "VirtualBox $version"
    if ($version -match "^(\d+)\.(\d+)") {
        $mayor = [int]$Matches[1]
        $menor = [int]$Matches[2]
        if ($mayor -lt 7 -or ($mayor -eq 7 -and $menor -lt 2)) {
            Write-Aviso "Vagrant 2.4.9 necesita VirtualBox 7.2 o superior."
            $actualizarVbox = $true
        }
    }
} else {
    Write-Aviso "VirtualBox no esta instalado."
    $actualizarVbox = $true
}

if ($actualizarVbox -and -not $SoloVerificar) {
    Write-Host "     Te aparecera un aviso de UAC (instala drivers de kernel)." -ForegroundColor DarkGray
    Install-Winget -Id "Oracle.VirtualBox" -Nombre "VirtualBox" -ComandoEsperado $vboxExe
    Write-Ok "VirtualBox instalado"
}

# ----------------------------------------------------------------------
# 3. Vagrant
# ----------------------------------------------------------------------
Write-Paso "3/6" "Vagrant (gestor de la maquina virtual)"

if (-not (Test-Comando "vagrant")) {
    Write-Aviso "Vagrant no esta instalado."
    if (-not $SoloVerificar) {
        Install-Winget -Id "Hashicorp.Vagrant" -Nombre "Vagrant" -ComandoEsperado "vagrant"
    }
}

if (Test-Comando "vagrant") {
    Write-Ok (& vagrant --version)

    # Hyper-V y VirtualBox no pueden coexistir.
    $hyperv = (Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue).HypervisorPresent
    if ($hyperv) {
        Write-Aviso "Hyper-V/WSL2 esta activo: VirtualBox puede fallar al arrancar la VM."
        Write-Aviso "Desactiva 'Virtual Machine Platform' y reinicia si 'vagrant up' da error."
    }
}

# ----------------------------------------------------------------------
# 4. Imagen base de Ubuntu
# ----------------------------------------------------------------------
Write-Paso "4/6" "Imagen base Ubuntu 22.04"

if (Test-Comando "vagrant") {
    $cajas = & vagrant box list 2>$null
    if ($cajas -match "ubuntu/jammy64") {
        Write-Ok "Box ubuntu/jammy64 ya descargada"
    } elseif (-not $SoloVerificar) {
        Write-Host "     Descargando la box (~600 MB, puede tardar) ..." -ForegroundColor DarkGray
        & vagrant box add ubuntu/jammy64 --provider virtualbox
        if ($LASTEXITCODE -ne 0) { throw "No se pudo descargar la box ubuntu/jammy64." }
        Write-Ok "Box descargada"
    } else {
        Write-Aviso "Box ubuntu/jammy64 no descargada (modo verificacion)."
    }
}

# ----------------------------------------------------------------------
# 5. Servidor Ubuntu + contenedor
# ----------------------------------------------------------------------
Write-Paso "5/6" "Servidor Ubuntu con Docker (vagrant up)"

$api = "http://127.0.0.1:8080"

if (-not $SoloVerificar) {
    if (-not (Test-Comando "vagrant")) {
        throw "Vagrant no esta disponible; no se puede levantar la VM."
    }
    Write-Host "     Creando/arrancando la VM e instalando Docker dentro ..." -ForegroundColor DarkGray
    Write-Host "     La primera vez tarda varios minutos." -ForegroundColor DarkGray
    & vagrant up
    if ($LASTEXITCODE -ne 0) {
        throw "vagrant up fallo. Revisa la seccion 'Solucion de problemas' del README."
    }
}

$salud = $null
for ($intento = 1; $intento -le 30; $intento++) {
    try {
        $salud = Invoke-RestMethod -Uri "$api/api/health" -TimeoutSec 10 -ErrorAction Stop
        break
    } catch {
        Start-Sleep -Seconds 3
    }
}

if ($salud) {
    Write-Ok "API respondiendo: estado=$($salud.estado), lecturas=$($salud.total_lecturas)"
} else {
    Write-Aviso "La API no respondio en $api. Revisa: vagrant ssh -c 'docker logs ia_for_iot_api'"
}

# ----------------------------------------------------------------------
# 6. Entorno de Arduino (ESP32)
# ----------------------------------------------------------------------
Write-Paso "6/6" "Entorno de Arduino para el ESP32"

if ($SinArduino) {
    Write-Aviso "Omitido por parametro -SinArduino"
} else {
    $acli = Get-ArduinoCli
    if (-not $acli) {
        Write-Aviso "Arduino CLI no esta instalado."
        if (-not $SoloVerificar) {
            Install-Winget -Id "ArduinoSA.CLI" -Nombre "Arduino CLI" -ComandoEsperado "arduino-cli"
            $acli = Get-ArduinoCli
        }
    }

    if ($acli) {
        Write-Ok "arduino-cli encontrado en $acli"

        if (-not $SoloVerificar) {
            $config = Join-Path $env:LOCALAPPDATA "Arduino15\arduino-cli.yaml"
            if (-not (Test-Path -LiteralPath $config)) {
                & $acli config init --overwrite | Out-Null
            }

            $indice = "https://espressif.github.io/arduino-esp32/package_esp32_index.json"
            & $acli config add board_manager.additional_urls $indice 2>$null | Out-Null
            & $acli core update-index | Out-Null

            $cores = & $acli core list
            if ($cores -notmatch "esp32:esp32") {
                Write-Host "     Instalando core esp32 (puede tardar y pesar ~1 GB) ..." -ForegroundColor DarkGray
                & $acli core install esp32:esp32 | Out-Null
            }
            Write-Ok "Core esp32 disponible"

            $libs = & $acli lib list
            if ($libs -notmatch "DHT sensor library") {
                Write-Host "     Instalando libreria DHT ..." -ForegroundColor DarkGray
                & $acli lib install "DHT sensor library" | Out-Null
            }
            Write-Ok "Libreria DHT disponible"

            Write-Host "     Compilando el firmware de prueba ..." -ForegroundColor DarkGray
            & $acli compile --fqbn esp32:esp32:esp32 "firmware\esp32_dht11" | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "Firmware compila correctamente"
            } else {
                Write-Aviso "El firmware no compilo. Revisa la salida de arduino-cli."
            }
        }
    } elseif ($SoloVerificar) {
        Write-Aviso "Arduino CLI no encontrado (modo verificacion)."
    }
}

# ----------------------------------------------------------------------
# Resumen
# ----------------------------------------------------------------------
Write-Host ""
Write-Host "======================================================" -ForegroundColor Magenta
Write-Host "  Listo" -ForegroundColor Magenta
Write-Host "======================================================" -ForegroundColor Magenta
Write-Host ""
Write-Host "  Panel web : $api/" -ForegroundColor Green
Write-Host "  API docs  : $api/docs" -ForegroundColor Green
Write-Host "  Salud     : $api/api/health" -ForegroundColor Green
Write-Host ""
Write-Host "  Probar sin hardware:" -ForegroundColor White
Write-Host "    uv run simular-esp32 --url $api/api/lecturas --count 20 --interval 2" -ForegroundColor Gray
Write-Host ""
Write-Host "  Exportar CSV y base de datos a Windows:" -ForegroundColor White
Write-Host "    .\scripts\exportar_datos.ps1" -ForegroundColor Gray
Write-Host ""
Write-Host "  Cargar el ESP32 (edita antes el sketch):" -ForegroundColor White
Write-Host "    arduino-cli upload --fqbn esp32:esp32:esp32 -p COM3 firmware\esp32_dht11" -ForegroundColor Gray
Write-Host ""
