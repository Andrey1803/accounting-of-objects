# Привязать origin и отправить main на GitHub (после создания репо на github.com/new).
# Пример: .\scripts\Push-GitHub.ps1 -RepoUrl "https://github.com/you/ObjectAccounting.git"
param(
    [Parameter(Mandatory = $true)]
    [string] $RepoUrl
)
$ErrorActionPreference = "Stop"

if ($RepoUrl -notmatch '^https://github\.com/[^/]+/[^/]+') {
    Write-Error "RepoUrl должен быть вида https://github.com/USER/REPO.git"
}

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

$hasOrigin = git remote 2>$null | Select-String -Pattern '^origin$' -Quiet
if ($hasOrigin) {
    git remote remove origin
}
git remote add origin $RepoUrl
git push -u origin main
Write-Host "Done: origin = $RepoUrl, branch main pushed."
