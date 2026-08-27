param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到项目虚拟环境：$python`n请先运行：python -m venv .venv"
}

if (-not $SkipInstall) {
    & $python -m pip install -r requirements.txt
    & $python -m pip install pyinstaller
}

& $python -m PyInstaller --noconfirm --clean packaging\BilibiliVideoWorkbench.spec
$exe = Join-Path $projectRoot "dist\BilibiliVideoWorkbench.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "PyInstaller 未生成预期文件：$exe"
}

Write-Host "已生成：$exe"
Write-Host "双击该文件即可首次自检并启动桌面工作台。"
