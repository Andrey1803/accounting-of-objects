param([string]$msg = "chore: bump version")
$p = "E:\Projects\accounting-of-objects\app_objects.py"
$c = [System.IO.File]::ReadAllText($p, [System.Text.Encoding]::UTF8)
$m = [regex]::Match($c, 'FINANCIAL_LOGIC_VERSION\s*=\s*(\d+)')
if (-not $m.Success) { Write-Error "FINANCIAL_LOGIC_VERSION not found"; exit 1 }
$old = [int]$m.Groups[1].Value
$new = $old + 1
$c = $c -replace "FINANCIAL_LOGIC_VERSION\s*=\s*$old", "FINANCIAL_LOGIC_VERSION = $new"
[System.IO.File]::WriteAllText($p, $c, (New-Object System.Text.UTF8Encoding $false))
Set-Location "E:\Projects\accounting-of-objects"
git add -A
git commit -m $msg
Write-Host "Version bumped: 4.$old -> 4.$new"