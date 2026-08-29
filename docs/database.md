# 数据库模块

## 职责

负责 SQLite 状态持久化、视频下载状态、UP 黑名单和 UP 级筛选规则。

## 关键结构

- `up`：UP 主基本信息。
- `video`：BV 视频元数据；保留 video 媒体状态兼容字段。
- `video_media`：按 BV + `video/audio` 独立保存下载状态、路径、错误和筛选原因。
- `up_blacklist`：每个 UP 独立的标题黑名单。
- `up_allowlist`：每个 UP 独立的指定下载名单关键词。
- `up_filter_settings`：每个 UP 独立的时长/发布时间筛选设置；空值继承全局默认值。
- `app_meta`：运行时元数据；`active_download_root` 记录最近一次生效的规范化下载根目录。

## 关键接口

- `Repository.get_up_filter_settings(mid)`
- `Repository.upsert_up_filter_settings(settings)`
- `Repository.set_pending(bvid, media_type)`：规则更新后将指定媒体任务恢复到队列。
- `Repository.list_videos/list_pending/list_downloaded/claim_next_pending/count_by_status(..., media_type)`：按 `video` 或 `audio` 查询和认领，默认保持 video 兼容。
- `Repository.get_meta/set_meta/reconcile_media_files(...)`：以当前下载目录中的有效 MP4/M4A 文件重建媒体状态。

数据库启动时会自动创建新增表，并将旧 `video.download_*` 字段幂等迁移到
`video_media`；旧数据库无需手动迁移。旧 `.m4a` 路径迁移为 audio DOWNLOADED，
对应 video 记录保持 PENDING；没有旧音频记录的视频会自动补建 audio PENDING。

下载目录切换不会保存多目录历史：切换时会递归扫描新目录，找到的 `[BV号].mp4`
或 `[BV号].m4a` 标记为 DOWNLOADED；旧目录中的 DOWNLOADED/FAILED/DOWNLOADING
状态在新目录中重新排队。目录切换和文件重建均在数据库事务中完成。

## 更新记录

2026-08-28：新增按媒体类型独立的下载状态，修复 MP4 已下载后无法继续下载音频的问题。
2026-08-24：新增 `up_filter_settings` 和规则恢复接口，支持 UP 级筛选。
