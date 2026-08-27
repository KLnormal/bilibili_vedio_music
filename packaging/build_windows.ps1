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
    if ($LASTEXITCODE -ne 0) { throw "安装项目依赖失败（退出码 $LASTEXITCODE）" }
    & $python -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "安装 PyInstaller 失败（退出码 $LASTEXITCODE）" }
}

& $python -m PyInstaller --noconfirm --clean packaging\BilibiliVideoWorkbench.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 构建失败（退出码 $LASTEXITCODE）。如果旧 exe 正在运行，请先关闭它。"
}
$exe = Join-Path $projectRoot "dist\BilibiliVideoWorkbench.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "PyInstaller 未生成预期文件：$exe"
}

Write-Host "已生成：$exe"
Write-Host "双击该文件即可首次自检并启动桌面工作台。"
