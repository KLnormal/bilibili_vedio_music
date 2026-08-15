# Bilibili 视频内容爬取系统（v0.1）

一个运行在本地的 **Bilibili UP 主视频内容采集与下载程序**。以 UP 主（`mid`）为基本管理单位，以 **SQLite** 为本地状态中心，以 **BV 号** 为唯一视频身份，以 **btop 风格 TUI** 为交互界面，实现「发现 → 去重 → 时长筛选 → 下载 → 记录状态 → 下次增量检查」的可持续运行闭环。

> 本仓库严格依据 `Bilibili 视频内容爬取系统——初代开发计划（Plan v0.1）` 实现，未做范围之外的扩展。

---

## 功能清单（对照开发计划）

| # | 计划要求 | 实现 |
|---|---------|------|
| 1 | 添加 / 删除 / 查看 / 保存 UP 主，`mid` 唯一标识 | ✅ `up` 表 + CLI `add` / `remove` / `list` |
| 2 | 获取 UP 主历史及新增投稿 | ✅ `x/space/wbi/arc/search` 分页扫描 |
| 3 | 保存视频核心元数据 | ✅ `video` 表（bvid/duration/title/description/pic/url/update_time） |
| 4 | BV 号去重 | ✅ `bvid` 为 PRIMARY KEY，`INSERT OR IGNORE` |
| 5 | 时长筛选（`min <= duration <= max`，含边界） | ✅ `DurationFilter`，无效时长判为不满足 → `SKIPPED` |
| 6 | 自动下载到本地 | ✅ `downloads/<UP>/<标题> [BVxxxx].mp4` |
| 7 | 下载状态机 + 失败重试 | ✅ `PENDING → DOWNLOADING → DOWNLOADED / FAILED`，`retry` 命令重置 |
| 8 | 账号登录 + 验证码人工介入 | ✅ 扫码登录 / Cookie 粘贴，自动流程与人工介入切换 |
| 9 | 运行时调整限速，默认 40 MB/s | ✅ 令牌桶 `RateLimiter`，TUI 按 `l` 或 CLI `limit 20M` |
| 10 | btop 风格终端界面（无 GUI） | ✅ `rich` 实时分区面板 + 键盘操作 |

---

## 目录结构

```
bilibili_vedio_music/
├── main.py                         # 入口：无参数启动 TUI，或执行子命令
├── config.yaml                     # 默认配置模板（不含任何账号密码）
├── requirements.txt
├── pyproject.toml
├── bilibili_crawler/
│   ├── app.py                      # 装配所有子系统，供 CLI / TUI 共用
│   ├── state.py                    # 线程安全共享运行状态（供 TUI 读取快照）
│   ├── cli/
│   │   ├── commands.py             # 无头 CLI 子命令
│   │   ├── tui.py                  # btop 风格实时面板
│   │   └── keyreader.py            # 跨平台单键读取（无阻塞 input）
│   ├── bilibili/
│   │   ├── client.py               # HTTP 客户端：WBI 签名 / buvid / 重试 / 反爬参数
│   │   ├── auth.py                 # Interactive Login Manager（扫码 / Cookie）
│   │   ├── user.py                 # UP 信息 + 投稿列表
│   │   └── video.py                # 视频详情 + 播放地址（DASH / 渐进流）
│   ├── crawler/
│   │   ├── user_crawler.py         # 全量 / 增量爬取、去重、状态落库
│   │   ├── video_crawler.py        # 单视频元数据补全
│   │   └── scheduler.py            # 「扫描 → 下载 → 定时重扫」循环
│   ├── database/
│   │   ├── database.py             # SQLite 连接 + 建表
│   │   ├── models.py               # Up / Video / DownloadStatus
│   │   └── repository.py           # 全部 SQL 访问
│   ├── filter/
│   │   └── duration_filter.py      # 时长筛选
│   ├── downloader/
│   │   ├── limiter.py              # 令牌桶限速器
│   │   ├── downloader.py           # 下载 + ffmpeg 合并 + 原子落盘
│   │   └── task_manager.py         # 下载状态机 + 工作线程池
│   └── config/
│       └── configuration.py        # 配置加载（默认值 + 深合并覆盖）
└── tests/
    └── test_core.py                # 核心逻辑单元测试（无网络）
```

---

## 安装

需要 Python 3.9+。

```bash
git clone https://github.com/KLnormal/bilibili_vedio_music.git
cd bilibili_vedio_music
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

> 下载高清 DASH 流需要 [ffmpeg](https://ffmpeg.org/)（可选）。未安装时自动回退到渐进流（单文件）下载。

---

## 快速开始

```bash
# 1) 登录（推荐，否则空间接口易触发风控）
python main.py login

# 2) 添加 UP 主（mid 见 UP 主页 URL：space.bilibili.com/<mid>）
python main.py add 486906719

# 3) 查看
python main.py list

# 4) 启动 TUI（实时面板 + 键盘操作）
python main.py
```

TUI 界面（btop 风格）：

```
┌──────────────────────────────────────────────────────────────────┐
│ BILIBILI CRAWLER  v0.1.0  [logged-in]  [dash+ffmpeg]  limit 40 MB/s│
├──────────────────────────────────────────────────────────────────┤
│ > 索尼音乐中国                486906719   123    2026-08-16T12:00:00 │
├──────────────────────────────────────────────────────────────────┤
│ CURRENT TASK                                                     │
│ UP: 索尼音乐中国   Status: 获取投稿列表...                          │
│ New: 12   Existing: 384   Filtered: 31                           │
│ Downloaded: 10   Failed: 1   Pending: 4                          │
├──────────────────────────────────────────────────────────────────┤
│ DOWNLOAD                                                         │
│ 视频标题 [BVxxxxxxxx]                                             │
│ ████████████████████░░░░  82%   32.5 MB/s / 40 MB/s              │
├──────────────────────────────────────────────────────────────────┤
│ LOG                                                              │
│ [12:00:01] 扫描 UP: 索尼音乐中国 (mid=486906719)                   │
├──────────────────────────────────────────────────────────────────┤
│ q Quit    p Pause    r Scan now    l Limit                        │
└──────────────────────────────────────────────────────────────────┘
```

**键盘操作：**

| 按键 | 作用 |
|------|------|
| `q` | 退出 |
| `p` | 暂停 / 恢复 |
| `r` | 立即触发一次扫描 |
| `l` | 设置下载限速（输入 MB/s 后回车，如 `l 20`） |

---

## CLI 无头模式

核心逻辑与 TUI 完全解耦，可脱离界面独立运行：

```bash
python main.py login              # 登录（扫码 / 粘贴 Cookie）
python main.py add <mid>          # 添加 UP
python main.py remove <mid>       # 删除 UP（级联删除其视频）
python main.py list               # 列出所有 UP 及视频数
python main.py scan [--mid MID]   # 扫描投稿（首次全量，之后增量）
python main.py download [--mid]   # 下载所有 PENDING 视频（单次）
python main.py retry [--mid]      # 将 FAILED 重置为 PENDING
python main.py limit [MB/s]       # 查看 / 设置下载限速
python main.py run [--once]       # 无头循环运行（扫描 + 下载）
```

---

## 登录

**账号密码不会进入数据库 / 配置文件 / 日志**，登录成功后仅持久化会话 Cookie 到 gitignored 的 `cookies.json`。

- **扫码登录（推荐）**：终端显示二维码，用 Bilibili 手机 App 扫码确认。验证码等人工环节在手机上完成，程序暂停轮询直到登录成功——这正是计划要求的「验证码 → 人工介入 → 继续运行」。
- **Cookie 粘贴**：从浏览器开发者工具复制 `SESSDATA` 等值后粘贴，适合无头 / 远程环境。

---

## 配置（`config.yaml`）

| 键 | 默认 | 说明 |
|----|------|------|
| `database.path` | `bilibili.db` | SQLite 文件 |
| `auth.cookie_file` | `cookies.json` | 会话 Cookie 文件（已 gitignore） |
| `crawler.page_size` | `30` | 投稿列表分页大小 |
| `crawler.stop_after_existing` | `10` | 连续 N 个历史视频后停止增量扫描 |
| `crawler.request_interval` | `0.3` | 视频详情请求间隔（秒，请勿调得过小） |
| `filter.min_duration` / `max_duration` | `300` / `1800` | 时长窗口（秒，含边界） |
| `download.save_root` | `downloads` | 下载根目录 |
| `download.max_speed_mbps` | `40` | 默认最大带宽（MB/s） |
| `download.concurrency` | `2` | 并发下载线程数 |
| `download.qn` | `80` | 期望清晰度（64=720p，80=1080p） |
| `download.prefer_dash` | `true` | 优先 DASH + ffmpeg 合并 |
| `download.ffmpeg_path` | `""` | ffmpeg 路径（空则从 PATH 查找） |

---

## 数据库设计

```text
up                         video
───────────────            ───────────────────────────
mid  (PK)        1 ── N    bvid      (PK / UNIQUE)
name                       mid       (FK -> up.mid)
face                       duration  (秒)
description                title
first_crawl_time           description
last_crawl_time            pic
enabled                    url
save_path                  update_time
                           download_status  (PENDING/DOWNLOADING/DOWNLOADED/FAILED/SKIPPED)
                           download_path
                           download_time
                           download_error
```

**核心设计原则**（来自计划第 18 节）：

1. **数据库是真实状态源** —— 判断视频是否已处理看 `bvid` + `download_status`，不看文件夹里有没有文件。
2. **元数据与下载状态分离** —— 「已发现」与「已下载」是独立维度；`FAILED` 下次可重试。
3. **首次全量、后续增量** —— 新增 UP 首次扫描全部历史投稿，之后只处理新视频。
4. **核心逻辑与 TUI 解耦** —— 关闭 TUI，爬虫 / 下载 / 数据库仍可独立运行。
5. **所有网络任务允许失败** —— 超时 / 重试 / 失败 / 恢复，单个请求异常不会退出整个程序。

---

## 关于 Bilibili 风控（重要）

Bilibili 的空间类接口（UP 信息、投稿列表）受 **WBI 签名 + 风控** 保护。本程序已内置完整缓解措施：

- WBI 签名（`img_key` / `sub_key` → mixin key → `w_rid`）；
- 真实 `buvid3` / `buvid4`（来自 `x/frontend/finger/spi`）+ `b_nut` Cookie；
- `web_location` / `dm_img_*` 等反爬参数。

即便如此，未登录或使用数据中心 / 代理 IP 时，投稿列表接口仍可能返回 `-352`（风控）或 `412`。**最可靠的缓解方式是先执行 `python main.py login` 登录，并尽量使用家庭宽带的真实 IP。** 程序对这类错误会重试并按 UP 隔离失败，不会崩溃。

---

## 测试

```bash
python -m unittest discover -s tests -v
```

---

## 免责声明

本项目仅用于学习与技术研究，请遵守 Bilibili 用户协议及相关法律法规，尊重 UP 主版权。请勿用于商业用途或大规模批量抓取。下载内容请勿二次传播。

## License

MIT
