# 数据库模块

## 职责

负责 SQLite 状态持久化、视频下载状态、UP 黑名单和 UP 级筛选规则。

## 关键结构

- `up`：UP 主基本信息。
- `video`：BV 视频元数据和下载状态。
- `up_blacklist`：每个 UP 独立的标题黑名单。
- `up_allowlist`：每个 UP 独立的指定下载名单关键词。
- `up_filter_settings`：每个 UP 独立的时长/发布时间筛选设置；空值继承全局默认值。

## 关键接口

- `Repository.get_up_filter_settings(mid)`
- `Repository.upsert_up_filter_settings(settings)`
- `Repository.set_pending(bvid)`：规则更新后将可下载视频恢复到队列。

数据库启动时会自动创建新增表，旧数据库无需手动迁移。

## 更新记录

2026-08-24：新增 `up_filter_settings` 和规则恢复接口，支持 UP 级筛选。
