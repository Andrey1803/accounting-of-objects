# sync-uchot.ps1 — синхронизация базы Railway (чёт объектов) -> локальная uchot_railway
$ErrorActionPreference = "Continue"
$root = "E:\Projects\accounting-of-objects"
$envFile = Join-Path $root ".env.sync"
$syncDir = Join-Path $root "_sync"
$dumpFile = Join-Path $syncDir "railway_uchot_dump.sql"
$logFile = Join-Path $syncDir "sync.log"
$pgDump = "E:\PostgreSQL18\pgsql\bin\pg_dump.exe"
$psql = "E:\PostgreSQL18\pgsql\bin\psql.exe"
$localDb = "uchot_railway"

function Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $logFile -Value $line -Encoding UTF8
    Write-Host $line
}

Log "=== Старт синхронизации чёта объектов ==="

if (-not (Test-Path $envFile)) { Log "Ш: .env.sync не найден"; exit 1 }
$url = (Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -match "^RAILWAY_DATABASE_URL=" }) -replace "^RAILWAY_DATABASE_URL=",""
if (-not $url) { Log "Ш: RAILWAY_DATABASE_URL не найден"; exit 1 }

Log "амп с Railway..."
try {
    & $pgDump --no-owner --no-privileges -d $url -f $dumpFile 2>&1 | ForEach-Object { Log $_ }
} catch {
    Log "Ш pg_dump: $_"
    exit 1
}
if (-not (Test-Path $dumpFile)) { Log "Ш: дамп не создан"; exit 1 }
$size = (Get-Item $dumpFile).Length
Log "амп создан: $size байт"
if ($size -lt 1000) { Log "Ш: дамп слишком маленький"; exit 1 }

Log "осстановление в локальную $localDb..."
$out = & $psql -U postgres -h localhost -d $localDb -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" 2>&1
$out | ForEach-Object { Log $_ }
if ($LASTEXITCODE -ne 0) { Log "Ш DROP/CREATE, exit=$LASTEXITCODE"; exit 1 }
$out2 = & $psql -U postgres -h localhost -d $localDb -f $dumpFile 2>&1
$out2 | ForEach-Object { Log $_ }
if ($LASTEXITCODE -ne 0) { Log "Ш restore, exit=$LASTEXITCODE"; exit 1 }

Log "=== Синхронизация завершена успешно ==="
