# YouTube 模块

## 职责

`bilibili_crawler.youtube.YouTubeService` 负责 YouTube 频道的独立 SQLite
元数据、扫描、筛选和下载；不读写 Bilibili 表。

## 对外接口

- `identify_channel(value)`：识别频道 URL、`@handle` 或 `UC...`。
- `add_channel(identifier)` / `remove_channel(channel_id)` / `list_channels()`：频道管理。
- `scan(channel_id=None)`：扫描指定频道；省略频道时扫描全部启用频道。
- `preview()` / `download()` / `status()` / `check_files()`：媒体状态与下载流程。

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
