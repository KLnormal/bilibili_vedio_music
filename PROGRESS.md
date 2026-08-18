# PROGRESS.md — 研发进度

> 本文件记录项目的当前研发进度、模块状态、已修复问题与待办事项。
> 每次更新后同步维护本文件（见 `Agent.md` 要求 1）。

---

## 1. 项目状态总览

| 项 | 状态 |
|----|------|
| 版本 | v0.1.0 |
| 里程碑 | Plan v0.1 全部 10 项核心目标 **已实现** |
| 当前分支 | `main` |
| 最新提交 | `edc8479` Fix download worker race and mid filter (atomic claim) |
| 测试 | 离线单测 11/11 通过；端到端 e2e T1~T6 10/10 通过 |
| 关键验证 | ✅ 1080P / 4K 下载可行性已验证（ffprobe 实测 1920×1080 与 3840×2160） |

---

## 2. 功能完成情况（对照 Plan v0.1 第 2 节）

| # | 计划要求 | 状态 | 实现位置 |
|---|---------|------|----------|
| 1 | UP 主管理（增删查、mid 唯一） | ✅ | `database/repository.py` + CLI `add/remove/list` |
| 2 | 获取 UP 历史及新增投稿 | ✅ | `bilibili/user.py` + `crawler/user_crawler.py` |
| 3 | 保存视频核心元数据 | ✅ | `database/models.py`（video 表） |
| 4 | BV 号去重 | ✅ | `bvid` 主键 + `INSERT OR IGNORE` |
| 5 | 时长筛选（含边界） | ✅ | `filter/duration_filter.py` |
| 6 | 自动下载到本地 | ✅ | `downloader/downloader.py` |
| 7 | 下载状态机 + 失败重试 | ✅ | `downloader/task_manager.py` + `retry` 命令 |
| 8 | 账号登录 + 人工验证介入 | ✅ | `bilibili/auth.py`（扫码 / Cookie） |
| 9 | 运行时限速（默认 40MB/s） | ✅ | `downloader/limiter.py` + `limit` 命令 |
| 10 | btop 风格 TUI | ✅ | `cli/tui.py` + `cli/keyreader.py` |

---

## 3. 模块清单与状态

| 子模块 | 文件 | 状态 | 说明 |
|--------|------|------|------|
| config | `config/configuration.py` | ✅ 稳定 | 默认值 + YAML 深合并覆盖 |
| database | `database/{models,database,repository}.py` | ✅ 稳定 | SQLite；含原子认领 `claim_next_pending()` |
| bilibili | `bilibili/{client,auth,user,video}.py` | ✅ 稳定 | WBI 签名、风控重试、扫码登录、投稿/详情/播放地址 |
| crawler | `crawler/{user_crawler,video_crawler,scheduler}.py` | ✅ 稳定 | 全量/增量扫描、去重、调度循环 |
| filter | `filter/duration_filter.py` | ✅ 稳定 | 时长窗口过滤 |
| downloader | `downloader/{limiter,downloader,task_manager}.py` | ✅ 稳定 | 令牌桶限速、DASH+ffmpeg 合并、状态机 |
| cli | `cli/{commands,tui,keyreader}.py` | ✅ 稳定 | 无头命令 + btop 风格 TUI |
| state | `state.py` | ✅ 稳定 | 线程安全共享运行状态 |
| app | `app.py` | ✅ 稳定 | 装配所有子系统（CLI/TUI 共用） |
| tests | `tests/{test_core,e2e_demo}.py` | ✅ 稳定 | 离线单测 + 端到端 e2e |

---

## 4. 已修复问题记录（Changelog）

| 提交 | 问题 | 说明 |
|------|------|------|
| `843051a` | 扫码登录 CookieConflictError | 多域名重复 SESSDATA 导致读取崩溃 → 冲突安全读取 + 去重归一化 |
| `dd5c403` | 文件名双 BV | `bvid` 自带 `BV` 前缀被重复拼接 → `[BVxxx].mp4` |
| `dd5c403` | 风控响应未兜底 | `-352/-412` 空响应体 `JSONDecodeError` 未捕获 → 纳入退避重试 + 刷新设备 Cookie |
| `8869869` | `download` 命令提前退出 | 只看 PENDING 忽略下载中的 DOWNLOADING → 等待 PENDING+DONWLOADING 均清零 |
| `edc8479` | 下载 worker 抢占竞态 + mid 过滤失效 | 两 worker 抢同一视频重复下载；`--mid` 未生效 → 原子认领 `claim_next_pending()` + `set_mid()` |

---

## 5. 待办 / 遗留事项

### 5.1 数据遗留（非代码问题，本地库 `bilibili.db`）

- `Empty_old_City`（mid=3546660365928495）有 **2 个孤儿 `DOWNLOADING` 视频**（旧 bug 遗留）：
  - `BV1hopjeEE6D`（650s）、`BV133D5Y8EyF`（938s，残留 `.mp4.part`）
  - 清理方式：重置为 `PENDING` + 删除 `.part` 后重新 `download`。

### 5.2 可选增强（暂缓，见 Agent.md 要求 2）

- [ ] 给 `download` 命令加 `--qn` 参数（命令行直切清晰度，无需改配置）。
- [ ] 启动时自动恢复孤儿 `DOWNLOADING`（→ `PENDING`）。
- [ ] 多 P 视频（分 P）目前只下载第一 P，后续按需支持。
- [ ] 断点续传（当前重下整文件，`.part` 仅用于原子落盘）。

---

## 6. Git 提交历史

```
edc8479 Fix download worker race and mid filter (atomic claim)
8869869 Fix download command exiting early while a download is in-flight
dd5c403 Harden client retry for risk-control, fix filename bvid duplication, fix e2e assertions
dae750a Add crawler.max_pages param and e2e test harness (T1-T6)
843051a Fix CookieConflictError on QR login (dedupe session cookies)
bb2476d Implement Bilibili video crawler v0.1 (Plan v0.1)
```

---

## 7. 关键验证结论（备忘）

- **清晰度**：登录态 + `qn=127` + 配置 `ffmpeg_path` 后，Empty_old_City 与花譜_kaf
  实测授权 1080P 高码率(112)/1080P60(116)/4K(120)，ffprobe 验证达标。
- **风控**：空间接口 `-352/-412` 需 WBI 签名 + buvid/b_nut + `dm_img_*` 反爬参数 + 登录态缓解。
- **ffmpeg 路径**：本机 winget 安装于
  `C:\Users\Observer\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_...\ffmpeg-9.0-full_build\bin\ffmpeg.exe`，
  需写入 `config.yaml`（不进仓库）。
