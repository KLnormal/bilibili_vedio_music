# Windows EXE 打包

## 职责

`packaging/bootstrap.py` 是 PyInstaller 单文件桌面程序的首次运行引导器：创建用户
数据目录、检查随包依赖、处理 ffmpeg，并把配置传给桌面 UI。

## 构建

在 Windows PowerShell 中执行：

```powershell
cd D:\Github\bilibili_branch_download
python -m venv .venv                 # 仅首次需要
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\packaging\build_windows.ps1
```

产物为 `dist\BilibiliVideoWorkbench.exe`。也可以直接运行构建脚本；它会先安装或
更新构建依赖。`dist/` 和 `build/` 已加入 `.gitignore`，不会把大体积二进制提交到
源码仓库。

## 首次运行行为

- Python、PySide6、requests、rich、qrcode 和 PyYAML 随 exe 打包，不要求用户另装
  Python。
- 配置、SQLite 数据库、Cookie、日志和下载目录默认放在
  `%LOCALAPPDATA%\BilibiliVideoWorkbench`，不会写入程序目录。
- 若系统没有 ffmpeg，首次运行会尝试下载用户目录内的便携版；网络失败时仍可
  启动 UI，并自动使用 progressive 下载回退。
- `--check-only --skip-ffmpeg` 可用于安装/打包自检；`BILIBILI_DESKTOP_HEADLESS=1`
  仅用于明确需要无头截图的场景。

## 最近更新

2026-08-28：新增 PyInstaller 单文件构建、首次运行环境检查、用户目录配置初始化
和 ffmpeg 便携安装回退，并完成 exe 启动自检。
