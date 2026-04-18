# Deploy "uchot obektov" service to Railway.
#
# In PROJECT ROOT create .env.railway (see .env.railway.example).
#
# Railway uses two env vars (docs.railway.com/reference/cli-api):
#   RAILWAY_API_TOKEN  — account token (create at railway.app/account/tokens; Workspace = empty / "No workspace")
#   RAILWAY_TOKEN      — project-only token
# For "railway up -p <project>" the ACCOUNT token is usually required. If you use RAILWAY_API_TOKEN,
# do not set RAILWAY_TOKEN in the same file (CLI may prefer the wrong one).

$ErrorActionPreference = "Stop"
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $OutputEncoding = [Console]::OutputEncoding
} catch {}

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

function Read-RailwayEnvFile {
    param([string]$Path)
    $api = $null
    $proj = $null
    if (-not (Test-Path $Path)) { return @{ Api = $api; Proj = $proj } }
    $raw = [System.IO.File]::ReadAllText($Path)
    $raw = $raw.TrimStart([char]0xFEFF).Trim()
    foreach ($line in $raw -split "`r?`n") {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith("#")) { continue }
        if ($t -match '^\s*RAILWAY_API_TOKEN\s*=\s*(.*)$') {
            $v = $Matches[1].Trim().Trim('"').Trim("'")
            if ($v) { $api = $v }
        }
        elseif ($t -match '^\s*RAILWAY_TOKEN\s*=\s*(.*)$') {
            $v = $Matches[1].Trim().Trim('"').Trim("'")
            if ($v) { $proj = $v }
        }
    }
    return @{ Api = $api; Proj = $proj }
}

$envFile = Join-Path $root ".env.railway"
$envFileTxt = Join-Path $root ".env.railway.txt"

$file = $null
if (Test-Path $envFile) { $file = $envFile }
elseif (Test-Path $envFileTxt) {
    $file = $envFileTxt
    Write-Host "Note: using .env.railway.txt" -ForegroundColor DarkGray
}

if ($file) {
    $parsed = Read-RailwayEnvFile $file
    if (-not $env:RAILWAY_API_TOKEN -and $parsed.Api) { $env:RAILWAY_API_TOKEN = $parsed.Api }
    if (-not $env:RAILWAY_TOKEN -and $parsed.Proj) { $env:RAILWAY_TOKEN = $parsed.Proj }
}

# Account token takes precedence for deploy; mixed vars confuse the CLI.
if ($env:RAILWAY_API_TOKEN) {
    Remove-Item Env:\RAILWAY_TOKEN -ErrorAction SilentlyContinue
}
elseif (-not $env:RAILWAY_TOKEN) {
    Write-Host ""
    Write-Host "No Railway token found." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "In .env.railway put ONE of these (project root, UTF-8):"
    Write-Host "  RAILWAY_API_TOKEN=your_account_token"
    Write-Host ""
    Write-Host "Create token: https://railway.app/account/tokens"
    Write-Host "Important: when creating the token, leave Workspace EMPTY (account-scoped)."
    Write-Host "Do not use only RAILWAY_TOKEN unless it is a Project token from that project's settings."
    Write-Host ""
    exit 1
}

$cliArgs = @(
    "up", "--ci", "--detach",
    "-p", "cfc00660-22af-44b7-9d44-7e95b270d139",
    "-e", "production",
    "-s", "340b71fe-5712-4b1b-a269-1cd715f97492"
)

# Windows: prefer portable railway.exe (no Node). See scripts/download-railway-cli.ps1 if node.exe is blocked.
$isWindows = ($null -ne $env:OS) -and ($env:OS -like '*Windows*')
$localExe = Join-Path $PSScriptRoot ".local\railway.exe"
$hasRailway = Get-Command railway -ErrorAction SilentlyContinue

if ($isWindows -and (Test-Path $localExe)) {
    $output = & $localExe @cliArgs 2>&1
}
elseif ($isWindows) {
    Write-Host ""
    Write-Host "node.exe is blocked (access denied). Install native CLI once (no Node):" -ForegroundColor Yellow
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\download-railway-cli.ps1`""
    Write-Host "Then run push-railway.bat again."
    Write-Host ""
    exit 1
}
elseif ($hasRailway) {
    $output = & railway @cliArgs 2>&1
}
else {
    $output = & npx -y @railway/cli @cliArgs 2>&1
}
$output | ForEach-Object { Write-Host $_ }

$code = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
$log = ($output | Out-String)

if ($code -ne 0) {
    Write-Host ""
    # ASCII-only pattern (Cyrillic in regex breaks PS5 on some Windows encodings)
    $accessDenied = $log -match '(?i)access denied|os error 5|permission denied|code 5\b|\(os error 5\)'
    $unauth = $log -match '(?i)Unauthorized'

    if ($accessDenied) {
        Write-Host "Windows: access denied (os error 5) - local permissions, not Railway login." -ForegroundColor Yellow
        Write-Host "Try:"
        Write-Host "  1) Run push-railway.bat from an elevated CMD (Run as administrator)"
        Write-Host "  2) Copy the project to a short path, e.g. C:\dev\ObjectAccounting (avoid Desktop/OneDrive if sync locks files)"
        Write-Host "  3) Pause OneDrive/antivirus for this folder or add an exclusion"
        Write-Host "  4) Close other apps that may lock files in the project"
        Write-Host ""
    }
    if ($unauth) {
        Write-Host "Railway API: Unauthorized - fix token:" -ForegroundColor Yellow
        Write-Host "  1) New token: https://railway.app/account/tokens (Workspace EMPTY when creating)"
        Write-Host "  2) In .env.railway: RAILWAY_API_TOKEN=..."
        Write-Host "  3) Or: railway login  then  railway link  then  railway up"
        Write-Host ""
    }
    if (-not $accessDenied -and -not $unauth) {
        Write-Host "Deploy failed (exit $code). Re-run with: railway up --verbose ... or check full log above."
        Write-Host ""
    }
}
exit $code
