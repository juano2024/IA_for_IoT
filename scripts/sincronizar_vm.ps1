<#
.SYNOPSIS
    Sincroniza el codigo de Windows con la VM y reconstruye el contenedor.

.DESCRIPTION
    Copia el proyecto del host a /home/vagrant/app dentro de la maquina virtual
    y ejecuta `docker compose up -d --build` para levantar la version nueva.

.EXAMPLE
    .\scripts\sincronizar_vm.ps1
#>
[CmdletBinding()]
param(
    [string]$Servidor = "127.0.0.1"
)

$ErrorActionPreference = "Stop"

Write-Host "==> Sincronizando el proyecto con la maquina virtual" -ForegroundColor Cyan

vagrant ssh -c "rsync -a --delete --exclude '.git/' --exclude '.venv/' --exclude 'data/' --exclude '__pycache__/' --exclude '.pytest_cache/' --exclude '.ruff_cache/' /vagrant/ /home/vagrant/app/ && cd /home/vagrant/app && mkdir -p data && docker compose up -d --build"

if ($LASTEXITCODE -ne 0) {
    throw "La sincronizacion con la VM fallo. Revisa que la VM este encendida (vagrant status)."
}

Write-Host ""
Write-Host "Servidor disponible en http://${Servidor}:8080" -ForegroundColor Green
Write-Host "  Panel   : http://${Servidor}:8080/" -ForegroundColor Green
Write-Host "  API docs: http://${Servidor}:8080/docs" -ForegroundColor Green
Write-Host "  Salud   : http://${Servidor}:8080/api/health" -ForegroundColor Green


