# Bilibili 视频工作台

一个本地运行的 Bilibili UP 主投稿扫描、筛选和下载工具，提供命令行、PySide6
桌面界面和 Windows 单文件 EXE。数据保存在本地 SQLite，不上传账号信息或下载记录。

## 功能概览

- 扫描 UP 主历史/增量投稿，支持分页断点续扫
- MP4 视频和 M4A 音频独立记录、独立筛选、独立下载
- 时长、发布时间、黑名单和特定下载关键词筛选
- 预览模式：只显示本次决策，不下载文件
- 下载目录切换：切换后递归扫描新目录，按实际文件重建状态
- DASH 合并、progressive 回退、清晰度选择、限速和可取消下载
- SQLite 状态检查与缺失文件恢复
- PySide6 桌面工作台和 Windows EXE 打包

## 环境安装

需要 Python 3.9+。在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

首次使用前准备 Bilibili Cookie（可通过登录命令交互获取）：

```powershell
.\.venv\Scripts\python.exe main.py login
```

默认配置文件为项目根目录的 `config.yaml`。使用个人配置时可通过
`--config D:\path\config.yaml` 指定。请勿将 Cookie、数据库或下载目录提交到 Git。

## 命令行快速开始

```powershell
# 添加 UP 并扫描投稿
python main.py add 488970166
python main.py scan --mid 488970166

# 预览本次视频下载，不产生文件
python main.py preview 488970166 --type video --quality 1080p

# 下载视频或音频（两种状态互不影响）
python main.py download --mid 488970166 --type video
python main.py download --mid 488970166 --type audio

# 检查并按当前下载目录同步文件状态
python main.py check 488970166 --type video
python main.py status 488970166 --type audio

# 直接下载 BV 号（不使用 UP 筛选规则）
python main.py download-bv BV1xxxxxxxx --type audio
```

可用清晰度：`720p`、`1080p`、`1080p+`、`1080p60`、`4k`。日期参数格式为
`YYYY.MM.DD`，使用 `0` 表示不限；时长单位为秒且边界包含。

黑名单和特定下载名单可通过 CLI 管理，也可在桌面任务页设置：

```powershell
python main.py blacklist --help
```

## 桌面界面

```powershell
python main.py desktop
```

在“设置”页选择下载根目录；保存后程序会验证目录可写并递归同步文件。目录切换期间
若有扫描或下载任务运行，需先停止任务。任务页可以直接调整媒体类型、时长和日期，
点击“预览”或“开始下载”时立即使用当前值。

## Windows EXE

构建单文件程序：

```powershell
.\packaging\build_windows.ps1
```

产物为 `dist\BilibiliVideoWorkbench.exe`，双击即可启动。首次运行会在
`%LOCALAPPDATA%\BilibiliVideoWorkbench` 初始化配置、数据库、Cookie 和日志；缺少
ffmpeg 时会尝试安装便携版本，失败时自动使用 progressive 回退。

构建自检：

```powershell
.\dist\BilibiliVideoWorkbench.exe --check-only --skip-ffmpeg
```

## 测试

离线回归测试（不访问 Bilibili，不修改个人数据库）：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test*.py"
```

在线花譜扫描冒烟测试需要显式启用网络：

```powershell
$env:RUN_BILIBILI_LIVE = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_live*.py"
```

## 目录和状态语义

数据库记录视频元数据，`video_media` 分别保存 `video`/`audio` 状态。程序只把当前
下载根目录中的非空 `[BV号].mp4` 或 `[BV号].m4a` 视为已完成文件；`.part`、零字节和
其他扩展名会被忽略。同一 BV 有多个文件时使用修改时间最新者。

因此从目录 A 切换到目录 B 后，A 中的已下载状态不会阻止 B 补全；切回 A 时会重新
扫描 A。切换失败会保留旧配置和旧状态，不会移动或删除用户文件。

## 文档

- [桌面工作台](docs/desktop.md)
- [数据库与迁移](docs/database.md)
- [下载器](docs/downloader.md)
- [扫描模块](docs/crawler.md)
- [Windows EXE 打包](docs/packaging.md)
- [开发进度](PROGRESS.md)

## 许可证

本项目采用 [MIT License](LICENSE)。使用 Bilibili 接口时请遵守相关服务条款，合理
控制请求频率，不要下载或传播无权使用的内容。
