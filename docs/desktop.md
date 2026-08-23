# 桌面工作台

## 职责

`bilibili_crawler.desktop` 提供 Windows 上的 PySide6 可视化工作台；它只负责
界面、任务调度和状态展示，扫描、筛选、SQLite 与下载仍由现有 `App` 提供。

## 启动

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py desktop
.\.venv\Scripts\python.exe main.py desktop --config path\to\config.yaml
```

Windows 上建议使用项目目录内的虚拟环境。直接安装到 Python Store 的全局用户
目录时，PySide6 的深层 QML 路径可能触发 Windows 长路径限制（`Errno 2`）。
若虚拟环境已激活，也可以简写为 `python main.py desktop`。无参数运行
`python main.py` 仍启动原有 TUI。

桌面入口会自动把窗口提升到前台并激活输入焦点，避免窗口已经绘制但鼠标仍被
启动它的终端或其他窗口接收。如果当前 PowerShell 继承了测试用的
`QT_QPA_PLATFORM=offscreen` / `minimal`，桌面入口会在 Windows 上自动清除它并
使用可交互的 Qt 平台；只有明确设置 `BILIBILI_DESKTOP_HEADLESS=1` 时才保留无头
模式（用于截图或 CI）。启动后应看到原生窗口标题栏，点击左侧“任务与视频”、筛选
复选框和清晰度下拉框即可验证交互。

## 页面

- 总览：状态卡片、当前进度、UP 概览和日志。
- UP 管理：添加/删除/启用 UP，以及单个 UP 扫描、检查、预览和下载。
- 任务与视频：视频表格、搜索、清晰度/媒体类型、任务控制和日志。
- 设置：全局默认数据库、Cookie、下载、筛选、限速、ffmpeg 和日志配置。
- UP 规则：在“UP 管理 → 规则设置”中为每个 UP 单独配置默认时长和发布时间；
  在“UP 管理 → 黑名单”中单独维护标题关键词。未填写的时长/日期继承全局默认值。
- 本次下载：在“任务与视频”页展开本次下载筛选，可临时覆盖时长和发布时间，
  并选择清晰度、视频/音频类型；不修改 UP 默认规则。
- 扫描状态：总览和“任务与视频”页显示当前 UP、页码、处理条数、新增/已有/过滤计数，
  扫描中显示忙碌进度条，完成或暂停后保留结果状态。

## 控制层接口

`DesktopController` 是视图唯一的业务入口。长任务通过 Qt worker 在后台线程
执行，并发出 `task_started`、`task_progress`、`task_finished`、`task_failed`、
`state_changed` 和 `log_message` 信号。SQLite 与 `RuntimeState` 仍是唯一状态源。

主要操作包括：

- `start_scan(mid)` / `start_check(mid)` / `start_preview(mid, options)`
- `start_download(mid, options)` / `start_retry(mid)`
- `start_add_up(mid)` / `start_login()`
- `pause(value)` / `stop()` / `save_settings(config)`

## 登录

`LoginManager.request_qrcode()` 获取二维码矩阵，`poll_qrcode_once()` 单次轮询并
持久化 Cookie。CLI 仍使用原有交互式封装，桌面端将轮询放在后台 worker 中。

## 更新记录

2026-08-24：修复 Windows 桌面窗口启动后焦点落在终端、以及继承 offscreen Qt
平台导致无法鼠标交互的问题；增加前台激活与启动保护。
2026-08-24：新增 PySide6 桌面入口、深色工作台、后台任务控制、二维码登录窗口、
缩略图缓存、可视化设置和离线 smoke tests。
2026-08-24：修复重复重建视频表导致的卡顿/日志闪烁；新增下载进度条和下载启动提示；筛选规则改为 UP 级别。
