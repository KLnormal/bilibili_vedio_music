# 下载模块

## 职责

`bilibili_crawler.downloader` 负责按照任务参数取得 Bilibili 媒体流、限速写入
临时文件，并在成功后原子生成 MP4 或 M4A 文件；下载状态的持久化由数据库任务
管理器负责。

## 对外接口

- `VideoDownloader.download(detail, up_dir, limiter, progress, media_type, qn)`：
  下载单个视频，`media_type=video` 生成 `.mp4`，`media_type=audio` 生成 `.m4a`。
- `DownloadTaskManager.set_options(options)` / `set_mid(mid)` / `start()` /
  `stop()` / `join()`：管理后台并发下载队列。
- `RateLimiter.acquire(size)`：共享令牌桶限速。

## 流程与回退

```text
请求播放信息
  ├─ DASH + ffmpeg：按请求 qn 选择不超过目标档位的视频流，合并音频
  ├─ DASH 音频：直接保存音频流为 .m4a
  ├─ progressive：直接保存为 .mp4
  └─ 音频无 DASH / 视频无 progressive：重新请求 progressive；音频由 ffmpeg 提取
```

所有下载先写 `.part` 文件，成功后 `Path.replace` 到最终路径；失败会删除临时
文件并由任务管理器写入 `FAILED`。音频 fallback 会继续使用本次任务的实际 qn，
不会悄悄退回全局清晰度配置。

## 最近更新

2026-08-24：修复音频无 DASH 时 fallback 引用未定义清晰度变量的问题；将本次任务
的有效 qn 传入音频路径，并新增 progressive、DASH、音频直流和音频 fallback 离线
测试。
