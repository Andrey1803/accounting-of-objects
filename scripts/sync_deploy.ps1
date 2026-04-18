# Копирует исходники из source\ в deploy\ (и ключевые файлы в корень) для Railway / ручного деплоя.
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "source"
$deploy = Join-Path $root "deploy"
if (-not (Test-Path $src)) { Write-Error "Нет папки source"; exit 1 }

$pyFiles = @(
    "app_objects.py", "auth.py", "database.py", "estimate_module.py", "price_sync.py", "extensions.py"
)
foreach ($f in $pyFiles) {
    $a = Join-Path $src $f
    if (Test-Path $a) {
        Copy-Item -LiteralPath $a -Destination (Join-Path $deploy $f) -Force
        Copy-Item -LiteralPath $a -Destination (Join-Path $root $f) -Force
    }
}
Copy-Item -LiteralPath (Join-Path $src "requirements.txt") -Destination (Join-Path $deploy "requirements.txt") -Force
robocopy (Join-Path $src "templates") (Join-Path $deploy "templates") /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
robocopy (Join-Path $src "static") (Join-Path $deploy "static") /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
robocopy (Join-Path $src "templates") (Join-Path $root "templates") /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
robocopy (Join-Path $src "static") (Join-Path $root "static") /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
Write-Host "OK: source -> deploy и корень"
