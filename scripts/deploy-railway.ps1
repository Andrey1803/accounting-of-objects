# Деплой в Railway: railway up (нужен вход: railway login один раз, или RAILWAY_TOKEN).
$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

if (-not (Get-Command railway -ErrorAction SilentlyContinue)) {
    Write-Error "Установите Railway CLI: npm i -g @railway/cli или scripts\download-railway-cli.ps1"
}

& railway whoami | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Нужна авторизация. Варианты:"
    Write-Host "  1) railway login   (браузер)"
    Write-Host "  2) `$env:RAILWAY_TOKEN = 'токен'   (railway.com/account/tokens)"
    exit 1
}

if (-not (Test-Path (Join-Path $root ".railway"))) {
    Write-Host "Связываю с проектом Railway..."
    & railway link
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "railway up ..."
& railway up
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Done."
