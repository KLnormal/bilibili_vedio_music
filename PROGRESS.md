# PROGRESS.md — 研发进度

> 本文件记录项目的当前研发进度、模块状态、已修复问题与待办事项。
> 每次更新后同步维护本文件（见 `Agent.md` 要求 1 与 `AGENT_PROMPT_v0.2.md` 第 13 节）。

---

## 1. 项目状态总览

| 项 | 状态 |
|----|------|
| 版本 | v0.2.0 |
| 里程碑 | v0.1.0 十项目标已实现；v0.2.0 八个 Phase **全部完成** |
| 当前分支 | `bilibili_branch_download` |
| 测试 | 92 个核心/桌面/下载器/CLI/目录切换测试通过；花譜在线扫描为显式 opt-in（默认跳过） |
| 关键验证 | ✅ 1080P / 4K 下载链路可行（ffprobe 实测） |

---

## 2. v0.2.0 开发计划（AGENT_PROMPT_v0.2.md）

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 1 | CLI 基础整理（`download-bv` / `status` / `check`） | ✅ 完成 |
| Phase 2 | 下载参数体系（`--quality` / `--type` / `--min-duration` / `--max-duration`） | ✅ 完成 |
| Phase 3 | UP 专属标题黑名单（`up_blacklist` 表 + `filter_reason` + SKIPPED→FILTERED） | ✅ 完成 |
| Phase 4 | 统一规则决策器与解释（`filter/decision.py` + `explain.py`） | ✅ 完成 |
| Phase 5 | `preview` 预览模式（dry-run） | ✅ 完成 |
| Phase 6 | `check`/`scan` 文件一致性与孤儿 DOWNLOADING 恢复 | ✅ 完成 |
| Phase 7 | TUI 增强 | ✅ 完成 |
| Phase 8 | 完整回归测试与文档更新 | ✅ 完成 |

---

## 3. 功能完成情况

### v0.1.0 已完成（对照 Plan v0.1）

UP 主管理、投稿扫描（全量/增量）、BV 去重、元数据保存、时长筛选、下载状态机、登录、人工验证介入、40MB/s 限速、btop 风格 TUI、WBI/风控重试、DASH+ffmpeg 下载、原子任务认领。

### v0.2.0 已完成（Phase 1~2）

- `download-bv <bvid>...`：BV 直接下载，绕过 UP 规则（`app.download_bv`）
- `status [mid]` / `check [mid]`：全局 / 单 UP 状态统计及本地文件一致性检查，支持 `--type video|audio`
- 下载参数：`--quality`（720p/1080p/**1080p+**/1080p60/4k → qn）、`--type`（video/audio → m4a）、`--min-duration`/`--max-duration`
- `DownloadOptions` 统一参数对象 + 质量映射（`options.py`）
- downloader 支持 audio 模式（`.m4a`，无 MP3 转码）
- UP 专属标题黑名单：`up_blacklist` 表 + `blacklist add/remove/list` 命令 + `blacklist_filter.py`（casefold 子串匹配）
- `DownloadStatus.SKIPPED → FILTERED` 迁移 + `video.filter_reason` 字段（幂等迁移）
- 统一决策器 `DecisionEngine`（READY/FILTERED/DOWNLOADED/MISSING/FAILED + reason）+ 规则解释 `explain.py`
- `download` 命令接入决策器：下载前重新评估黑名单/时长，只下载 READY
- `preview [mid]` 预览模式（dry-run，含 `--explain <bvid>` 单视频规则解释）
- 文件一致性：`scan` 后自动 check，MISSING → PENDING；启动时自动恢复孤儿 `DOWNLOADING`
- TUI 增强：header 显示当前清晰度（qn → 可读名）
- 回归测试：BV 直下绕过黑名单、preview 不修改状态、孤儿恢复等（32 个离线单测）
- e2e 测试案例 `tests/e2e_v020_demo.py`（10/10 通过）：Empty_old_City 1080p+ / 120~300s / Buffer 黑名单 → 17 个下载 + 4 个黑名单 FILTERED
  - `--type video`：17 个 MP4，ffprobe ≥1080P
  - `--type audio`：17 个 M4A（纯音频无视频流），黑名单/时长规则同样生效
- 发布时间筛选（用户明确要求加入）：`--min-date` / `--max-date`（格式 `20xx.xx.xx`，`0`=不限）
  - `video.created` 字段（发布时间戳）+ 幂等迁移；扫描时自动保存
  - 决策规则顺序：时长 → 日期 → 黑名单；过滤原因 `date_out_of_range` / `date_missing`
  - 语义：`a b`=a~b；`0 b`=直到b；`a 0`=从a到现在；`0 0`=不限（已验证 30/7/18/5 全部正确）

### 功能整治验收

- `video_media` 为每个 BV 的 video/audio 独立状态；MP4 已下载不会阻止同 BV 的 M4A 进入队列。
- 任务页时长/日期始终可编辑，预览和下载共享同一份实时参数；黑名单优先于指定下载名单。
- 扫描分页、下载流和 ffmpeg 均支持停止；Windows ffmpeg 使用 `CREATE_NO_WINDOW`，停止会清理 `.part` 并恢复 PENDING。
- 离线回归测试命令：`$env:QT_QPA_PLATFORM='offscreen'; .\\.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test*.py"`。
- 花譜在线冒烟（临时库，不改现有数据）：`$env:RUN_BILIBILI_LIVE='1'; .\\.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_live*.py"`。
- 下载目录切换：`app_meta.active_download_root` 记录当前根目录；切换时递归识别有效 `[BV号].mp4/.m4a`，旧目录的 DOWNLOADED/FAILED/DOWNLOADING 在新目录中重新排队，设置页支持浏览选择和后台同步。

---

## 4. 模块清单与状态

| 子模块 | 文件 | 状态 | 说明 |
|--------|------|------|------|
| config | `config/configuration.py` | ✅ 稳定 | 默认值 + YAML 深合并覆盖 |
| database | `database/{models,database,repository}.py` | ✅ 稳定 | SQLite；BV 元数据与 video/audio 独立媒体状态、当前下载目录元数据、原子认领、扫描游标与完整标记 |
| bilibili | `bilibili/{client,auth,user,video}.py` | ✅ 稳定 | WBI 签名、风控重试、扫码登录、分页校验与投稿断点 |
| crawler | `crawler/{user_crawler,video_crawler,scheduler}.py` | ✅ 稳定 | 全量/增量扫描、去重、风控失败断点续扫 |
| filter | `filter/{duration_filter,blacklist_filter,decision,explain}.py` | ✅ 稳定 | 时长/黑名单规则 + 统一决策器 + 规则解释 |
| downloader | `downloader/{limiter,downloader,task_manager}.py` | ✅ 稳定 | 令牌桶限速、DASH+ffmpeg 合并、progressive/audio fallback、媒体状态机、可取消下载 |
| cli | `cli/{commands,tui,keyreader}.py` | ✅ 稳定 | 新增 `download-bv`/`status`/`check` |
| state | `state.py` | ✅ 稳定 | 线程安全共享运行状态 |
| app | `app.py` | ✅ 稳定 | 新增 `status`/`check_files`/`download_bv`；当前下载目录同步、切换与轻量文件预检 |
| options | `options.py` | ✅ 稳定 | `DownloadOptions` 统一参数对象 + 质量映射 |
| tests | `tests/{test_core,test_downloader,test_packaging,e2e_demo,test_desktop}.py` | ✅ 稳定 | 核心、分页续扫、下载器模式、打包引导与桌面交互离线测试 |
| desktop | `desktop/{app,controller,workers}.py` | ✅ 稳定 | PySide6 工作台、UP 级筛选、后台任务、Bilibili/YouTube 登录与设置；下载目录浏览、后台同步、运行中切换保护 |
| youtube | `youtube.py` | ✅ 稳定 | YouTube 独立频道、视频/音频状态、筛选与下载；支持全部启用频道扫描、Cookie 登录和严格清晰度 |
| packaging | `packaging/{bootstrap.py,*.spec,build_windows.ps1}` | ✅ 稳定 | PyInstaller 单文件 EXE、首次运行环境检查、用户目录配置和 ffmpeg 便携安装 |

---

## 5. 已修复问题记录（Changelog）

| 2026-08-30 | YouTube 桌面扫描未启动 | “任务与视频”页的“全部 UP”空选择被转成字面量 `None`，现由服务层聚合扫描全部启用频道；CLI 同步支持省略 `--channel`。 |
| 2026-08-30 | YouTube 下载参数被桌面操作覆盖 | “UP 管理 → 下载”改用设置页默认清晰度/媒体类型；显式视频清晰度不再静默降级，新增 YouTube Cookie 登录配置与后台检查。 |
| 2026-08-31 | Edge 浏览器 Cookie 读取报错 | yt-dlp 的通用 Chrome 数据库错误现在转换为对应浏览器提示；浏览器进程占用数据库时指导用户完全退出或改用 Netscape Cookie。 |
| 2026-08-31 | Edge DPAPI/v20 Cookie 无法解密 | 检测到新版 Edge 的 App-Bound Encryption；程序明确提示当前 yt-dlp 不支持直接解密，并引导导出 Netscape Cookie。 |
| 2026-08-31 | YouTube 下载停留在准备队列 | 接入 yt-dlp 实时进度，限制网络重试，并在新任务开始时恢复异常退出遗留的 `DOWNLOADING` 记录。 |

| 提交 | 问题 | 说明 |
|------|------|------|
| `843051a` | 扫码登录 CookieConflictError | 多域名重复 SESSDATA → 冲突安全读取 + 去重归一化 |
| `dd5c403` | 文件名双 BV | `bvid` 自带 `BV` 前缀被重复拼接 |
| `dd5c403` | 风控响应未兜底 | `-352/-412` 空响应体未捕获 → 退避重试 + 刷新设备 Cookie |
| `8869869` | `download` 命令提前退出 | 只看 PENDING 忽略 DOWNLOADING → 等待二者均清零 |
| `edc8479` | 下载 worker 抢占竞态 + mid 过滤失效 | 原子认领 `claim_next_pending()` + `set_mid()` |
| `b344479` | 清晰度档位越级 | `pick_best` 选码率最高的流，请求 1080p+ 会拿到 4K → 新增 `pick_best_leq()` 限制在请求档位内 |
| 近期 | 扫描只拿到「最近一小段」投稿 | `build_video` 轻量化、翻页风险重试、owner.mid 校验、重复页保护；风控失败保存 `scan_next_page`，下次 scan 从失败页继续。注意：实测 2640 只是第 88 页被拦截前的已扫描数量，不代表完整历史总数。 |
| 2026-08-24 | 旧库残缺记录触发增量短路 | 新增 `scan_complete`；旧库默认为未完成，先全量补扫再启用增量扫描。 |
| 2026-08-24 | Windows 桌面窗口看得到但无法点击 | 桌面启动时清除继承的 `QT_QPA_PLATFORM=offscreen/minimal`，并在原生窗口创建后提升到前台、激活焦点；`BILIBILI_DESKTOP_HEADLESS=1` 可显式保留无头模式。 |
| 2026-08-24 | 音频无 DASH fallback 使用未定义清晰度 | 将本次任务的有效 qn 传入音频 fallback，并补齐 progressive/DASH/audio 离线覆盖。 |
| 2026-08-28 | 任务页筛选标题、控件和状态文字挤在一起 | 为筛选组标题预留顶部空间，固定两行筛选的间距和最小高度，并分隔操作按钮与状态行。 |
| 2026-08-28 | 缺少可双击启动的桌面程序 | 增加 PyInstaller 单文件构建和首次运行引导；随包依赖自检，初始化用户数据目录，缺少 ffmpeg 时尝试安装便携版后启动 UI；构建脚本检查 PyInstaller 退出码，避免文件被占用时误报成功。 |

---

## 6. 待办 / 遗留事项

### 6.1 数据遗留（非代码问题，本地库 `bilibili.db`）

- `Empty_old_City`（mid=3546660365928495）有 2 个孤儿 `DOWNLOADING`（Phase 6 提供自动恢复）：
  `BV1hopjeEE6D`、`BV133D5Y8EyF`（残留 `.mp4.part`）

### 6.2 v0.2 待办（Phase 2~8）

见第 2 节表格。

### 6.3 暂不开发（冻结，见 AGENT_PROMPT 第 23 节）

统计类数据、标题白名单、AI 分析、Web UI、多 P 完整支持、MP3 转码、复杂断点续传。

> 注：原冻结列表中的「发布时间筛选」已由项目所有者明确要求加入（见已完成项）。

---

## 7. Git 提交历史（近期）

```
fef00e9 Add Agent.md (project requirements) and PROGRESS.md (dev progress)
edc8479 Fix download worker race and mid filter (atomic claim)
8869869 Fix download command exiting early while a download is in-flight
dd5c403 Harden client retry for risk-control, fix filename bvid duplication, fix e2e assertions
dae750a Add crawler.max_pages param and e2e test harness (T1-T6)
843051a Fix CookieConflictError on QR login (dedupe session cookies)
bb2476d Implement Bilibili video crawler v0.1 (Plan v0.1)
```

---

## 8. 关键验证结论（备忘）

- **清晰度**：登录态 + `qn=127` + 配置 `ffmpeg_path` 后，实测授权 1080P 高码率(112)/1080P60(116)/4K(120)。
- **风控**：空间接口 `-352/-412` 需 WBI 签名 + buvid/b_nut + `dm_img_*` 反爬参数 + 登录态缓解。
- **ffmpeg 路径**：本机 winget 安装于 `C:\Users\Observer\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_...\bin\ffmpeg.exe`，需写入 `config.yaml`（不进仓库）。
