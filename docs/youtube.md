# YouTube 模块

## 职责

`bilibili_crawler.youtube.YouTubeService` 负责 YouTube 频道的独立 SQLite
元数据、扫描、筛选和下载；不读写 Bilibili 表。

## 对外接口

- `identify_channel(value)`：识别频道 URL、`@handle` 或 `UC...`。
- `add_channel(identifier)` / `remove_channel(channel_id)` / `list_channels()`：频道管理。
- `scan(channel_id=None)`：扫描指定频道；省略频道时扫描全部启用频道。
- `preview()` / `download()` / `status()` / `check_files()`：媒体状态与下载流程。
- 登录：`cookie_file` 使用 Netscape Cookie 文件，`cookies_from_browser` 使用本机
  浏览器会话；二者同时设置时优先浏览器来源。

Windows 上读取 Chromium 浏览器 Cookie 需要复制浏览器的 Cookies SQLite 数据库。
如果浏览器仍在后台运行，yt-dlp 可能无法复制数据库；请完全退出 Edge/Chrome（包括
后台进程）后重试，或改用导出的 Netscape Cookie 文件。程序会把 yt-dlp 通用的
“Could not copy Chrome cookie database”错误转换为对应浏览器的操作提示。较新的
Edge/Chrome 可能使用 v20 App-Bound Encryption；当前 yt-dlp 无法直接解密此类 Cookie，
此时必须导出 Netscape Cookie 文件。

桌面下载会显示当前视频和 yt-dlp 的实时字节进度，并对网络请求设置有限超时与重试次数。
YouTube 当前要求 JavaScript challenge 求解；程序会自动检测系统 Node.js，并启用
yt-dlp 的 EJS（`ejs:github`）组件，否则可能出现“页面需要重新加载”且没有可用格式。
如果程序在下载中异常退出，下一次开始下载会自动把遗留的 `DOWNLOADING` 记录恢复为
`PENDING`，避免队列永久卡住。
开始新下载时还会先检查 `DOWNLOADED` 记录对应的文件是否仍存在；文件被移动或删除时
会自动重新排队，避免数据库状态与磁盘内容不一致。

桌面端通过 `DesktopController.start_scan()` 调用同一服务。任务页的“全部 UP”
值为 `None`，服务层会将其解释为全部启用的 YouTube 频道，而不是字符串频道名。

## 数据流

```text
频道
 ↓ yt-dlp extract_flat
video + media(video/audio)
 ↓ preview 规则
READY / FILTERED / DOWNLOADED
 ↓ yt-dlp download
本地 YouTube/<频道>/ 文件
```

## 更新记录

2026-08-30：支持 `scan(None)` 聚合扫描全部启用频道，修复桌面“全部 UP”扫描
将空选择误转为字符串 `"None"` 导致任务立即失败的问题；CLI YouTube scan 也允许
省略 `--channel` 扫描全部启用频道。扫描地址固定使用频道 `/videos` 标签，避免
`channel_url` 主页返回的三个导航条目被误当成视频。
同日修复桌面设置默认值未传入“UP 管理 → 下载”、YouTube 视频格式静默降级，以及
新增 Cookie/浏览器登录态检查。
