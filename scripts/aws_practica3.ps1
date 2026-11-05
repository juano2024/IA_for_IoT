<#
.SYNOPSIS
  Despliega la Practica 3 (AWS IoT + DynamoDB + Lambda + S3) con AWS CLI.

.EXAMPLE
  .\scripts\aws_practica3.ps1 -Up -BucketName ia-iot-p3-dashboard-juan
  .\scripts\aws_practica3.ps1 -Certs      # crea el certificado del ESP32 en aws/certs/
  .\scripts\aws_practica3.ps1 -Status
  .\scripts\aws_practica3.ps1 -Down -BucketName ia-iot-p3-dashboard-juan

  Requiere AWS CLI v2 con credenciales configuradas (aws configure).
#>
param(
    [switch]$Up,
    [switch]$Certs,
    [switch]$Status,
    [switch]$Down,
    [string]$Region = "us-east-1",
    [string]$Stack = "ia-iot-p3",
    [string]$BucketName
)

$ErrorActionPreference = "Stop"
$Raiz = Split-Path -Parent $PSScriptRoot
$Aws = Join-Path $Raiz "aws"
$CertsDir = Join-Path $Aws "certs"
$TokenFile = Join-Path $Aws ".api-token"   # ignorado por git

function Aws-Cli { & aws @args --region $Region; if ($LASTEXITCODE -ne 0) { throw "aws $($args -join ' ') fallo" } }
function Salida($clave) {
    Aws-Cli cloudformation describe-stacks --stack-name $Stack `
        --query "Stacks[0].Outputs[?OutputKey=='$clave'].OutputValue" --output text
}

if ($Up) {
    if (-not $BucketName) { throw "Indica -BucketName (nombre global unico para el dashboard)." }
    if (-not (Test-Path $TokenFile)) {
        $bytes = New-Object byte[] 24
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        ([Convert]::ToBase64String($bytes) -replace '[^A-Za-z0-9]', 'x') | Set-Content $TokenFile -NoNewline
    }
    $Token = Get-Content $TokenFile -Raw
    $Cuenta = (& aws sts get-caller-identity --query Account --output text)
    $Artefactos = "$Stack-artefactos-$Cuenta-$Region"

    Write-Host "[1/4] Bucket de artefactos $Artefactos"
    & aws s3api head-bucket --bucket $Artefactos 2>$null
    if ($LASTEXITCODE -ne 0) {
        if ($Region -eq "us-east-1") { Aws-Cli s3api create-bucket --bucket $Artefactos }
        else { Aws-Cli s3api create-bucket --bucket $Artefactos --create-bucket-configuration "LocationConstraint=$Region" }
    }

    Write-Host "[2/4] Empaquetar y desplegar la plantilla (CloudFormation)"
    $Empaquetada = Join-Path $env:TEMP "$Stack-packaged.yaml"
    Aws-Cli cloudformation package --template-file (Join-Path $Aws "template.yaml") `
        --s3-bucket $Artefactos --output-template-file $Empaquetada | Out-Null
    Aws-Cli cloudformation deploy --template-file $Empaquetada --stack-name $Stack `
        --capabilities CAPABILITY_IAM `
        --parameter-overrides "WebsiteBucketName=$BucketName" "ApiToken=$Token"

    Write-Host "[3/4] Configurar y publicar el dashboard"
    $ApiUrl = Salida "ApiUrl"
    $Config = Join-Path $Aws "dashboard\config.js"
    $Original = Get-Content $Config -Raw
    try {
        $Nuevo = $Original -replace 'apiUrl: "[^"]*"', "apiUrl: `"$ApiUrl`"" -replace 'apiToken: "[^"]*"', "apiToken: `"$Token`""
        Set-Content $Config $Nuevo -NoNewline
        Aws-Cli s3 sync (Join-Path $Aws "dashboard") "s3://$BucketName" --delete
    } finally {
        Set-Content $Config $Original -NoNewline   # el token no queda en el repo
    }

    Write-Host "[4/4] Listo"
    Write-Host "  API:       $ApiUrl"
    Write-Host "  Dashboard: $(Salida 'WebsiteUrl')"
    Write-Host "  Siguiente: .\scripts\aws_practica3.ps1 -Certs"
}

if ($Certs) {
    New-Item -ItemType Directory -Force $CertsDir | Out-Null
    $Thing = Salida "ThingName"
    $Politica = Salida "PolicyName"
    $Json = Aws-Cli iot create-keys-and-certificate --set-as-active `
        --certificate-pem-outfile (Join-Path $CertsDir "device.pem.crt") `
        --private-key-outfile (Join-Path $CertsDir "private.pem.key") `
        --public-key-outfile (Join-Path $CertsDir "public.pem.key") | ConvertFrom-Json
    Aws-Cli iot attach-policy --policy-name $Politica --target $Json.certificateArn
    Aws-Cli iot attach-thing-principal --thing-name $Thing --principal $Json.certificateArn
    Invoke-WebRequest "https://www.amazontrust.com/repository/AmazonRootCA1.pem" `
        -OutFile (Join-Path $CertsDir "AmazonRootCA1.pem")
    Write-Host "Certificados en aws/certs/ (no se suben a git)."
    Write-Host "Endpoint para secrets.h:"
    Aws-Cli iot describe-endpoint --endpoint-type iot:Data-ATS --query endpointAddress --output text
}

if ($Status) {
    Aws-Cli cloudformation describe-stacks --stack-name $Stack --query "Stacks[0].[StackStatus,Outputs]" --output table
}

if ($Down) {
    if (-not $BucketName) { throw "Indica -BucketName para vaciar el bucket antes de borrar la pila." }
    Aws-Cli s3 rm "s3://$BucketName" --recursive
    # Desvincular y eliminar los certificados del dispositivo.
    $Thing = Salida "ThingName"
    $Politica = Salida "PolicyName"
    $Principales = (& aws iot list-thing-principals --thing-name $Thing --region $Region --query principals --output text) -split "\s+" | Where-Object { $_ }
    foreach ($Arn in $Principales) {
        $Id = $Arn.Split("/")[-1]
        Aws-Cli iot detach-thing-principal --thing-name $Thing --principal $Arn
        Aws-Cli iot detach-policy --policy-name $Politica --target $Arn
        Aws-Cli iot update-certificate --certificate-id $Id --new-status INACTIVE
        Aws-Cli iot delete-certificate --certificate-id $Id
    }
    Aws-Cli cloudformation delete-stack --stack-name $Stack
    Aws-Cli cloudformation wait stack-delete-complete --stack-name $Stack
    Write-Host "Pila eliminada. El bucket de artefactos $Stack-artefactos-* se puede borrar con aws s3 rb --force."
}

if (-not ($Up -or $Certs -or $Status -or $Down)) {
    Get-Help $MyInvocation.MyCommand.Path -Examples
}
