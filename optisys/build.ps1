$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venv = Join-Path $env:LOCALAPPDATA "optisys-build\venv"
$py = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $py)) {
    $sysPy = Join-Path $env:LOCALAPPDATA "Programs\Python\Python314\python.exe"
    if (Test-Path $sysPy) { & $sysPy -m venv $venv }
    elseif (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv $venv }
    else { python -m venv $venv }
}
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r requirements.txt --quiet
& $py -m PyInstaller optisys.spec --noconfirm --clean --log-level WARN
if ($LASTEXITCODE -ne 0) { throw "PyInstaller exit code $LASTEXITCODE" }

Write-Host "OK: $PSScriptRoot\dist\optisys.exe"
