# One-time download: native Railway CLI for Windows (no Node.js).
# Use when: node.exe "Access denied (os error 5)".

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$scriptDir = $PSScriptRoot
$localDir = Join-Path $scriptDir ".local"
$exePath = Join-Path $localDir "railway.exe"
$version = "v4.40.0"
$zipName = "railway-$version-x86_64-pc-windows-msvc.zip"
$zipUrl = "https://github.com/railwayapp/cli/releases/download/$version/$zipName"
$zipPath = Join-Path $localDir $zipName
$stage = Join-Path $localDir "_stage"

New-Item -ItemType Directory -Force -Path $localDir | Out-Null

if (Test-Path $exePath) {
    Write-Host "Already exists: $exePath"
    exit 0
}

Write-Host "Downloading $zipUrl ..."
Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing

Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $stage | Out-Null
Write-Host "Extracting..."
Expand-Archive -Path $zipPath -DestinationPath $stage -Force
Remove-Item $zipPath -Force -ErrorAction SilentlyContinue

$found = Get-ChildItem -Path $stage -Filter "railway.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $found) {
    Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
    Write-Error "railway.exe not found in archive. See https://github.com/railwayapp/cli/releases"
}

Move-Item -LiteralPath $found.FullName -Destination $exePath -Force
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "OK: $exePath"
Write-Host "Now run: scripts\push-railway.bat"
