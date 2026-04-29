param(
  [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Join-Path $PSScriptRoot '..')

if (-not $SkipInstall) {
  Write-Host '[1/3] pip install -r requirements.txt...' -ForegroundColor Cyan
  python -m pip install -r requirements.txt
} else {
  Write-Host '[1/3] pip install skipped' -ForegroundColor Yellow
}

Write-Host '[2/3] Python syntax compile...' -ForegroundColor Cyan
python -m compileall -q app_objects.py database.py auth.py estimate_module.py extensions.py

Write-Host '[3/3] Procfile sanity check...' -ForegroundColor Cyan
if (-not (Test-Path 'Procfile')) { throw 'Procfile not found' }
Get-Content 'Procfile' | ForEach-Object { Write-Host $_ }

Write-Host 'ObjectAccounting predeploy check completed.' -ForegroundColor Green
