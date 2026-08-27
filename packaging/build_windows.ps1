param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project virtual environment not found: $python`nRun: python -m venv .venv"
}

if (-not $SkipInstall) {
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Installing project dependencies failed (exit code $LASTEXITCODE)" }
    & $python -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "Installing PyInstaller failed (exit code $LASTEXITCODE)" }
}

& $python -m PyInstaller --noconfirm --clean packaging\BilibiliVideoWorkbench.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed (exit code $LASTEXITCODE). Close a running executable and retry."
}
$exe = Join-Path $projectRoot "dist\BilibiliVideoWorkbench.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "PyInstaller did not generate expected file: $exe"
}

Write-Host "Generated: $exe"
Write-Host "Double-click the executable for first-run checks and desktop startup."
