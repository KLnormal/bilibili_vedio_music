# Bilibili 视频内容爬取系统

**当前版本：v0.2.0（开发计划）**  
**计划制定时间：2026-08-18 23:58（UTC+8）**  
**项目类型：本地 Bilibili UP 主视频采集、筛选与下载工具**

> 本文档基于当前仓库已经实现的 v0.1.0 与下一阶段冻结的 v0.2.0 需求编写。
> v0.1.0 已完成 UP 主管理、历史/增量扫描、BV 去重、时长过滤、下载状态机、登录、40 MB/s 限速以及 btop 风格 TUI 等核心能力。
> 本版本不推翻现有架构，而是在现有基础上扩展完整 CLI 工作流、BV 直接下载、清晰度与媒体类型控制、UP 专属标题黑名单、预览模式、规则解释以及本地文件一致性检查。

---

## 1. 项目目标

本项目的核心目标是建立一个可长期运行的、本地状态持久化的 Bilibili 视频采集系统：

```text
UP 主
  ↓
扫描投稿
  ↓
获取视频元数据
  ↓
BV 号去重
  ↓
更新本地数据库
  ↓
下载阶段进行规则筛选
  ↓
时长筛选 + UP 专属标题黑名单
  ↓
视频 / 音频下载
  ↓
记录下载状态
  ↓
下一次 scan 增量检查
```

核心设计原则：

1. **SQLite 是本地真实状态中心。**
2. **BV 号是视频唯一身份。**
3. **扫描与下载分离。** `scan` 负责发现/更新，`download` 负责根据当前规则执行下载。
4. **黑名单只参与下载决策，不阻止视频进入数据库。**
5. **BV 直接下载绕过 UP 专属规则。**
6. **TUI 与核心业务逻辑解耦。**
7. **规则尽量可由 CLI 临时覆盖，默认配置仍来自 `config.yaml`。**

---

# 2. 当前项目状态

## v0.1.0 已实现

当前代码已经具备：

- UP 主添加、删除、查看
- UP 主历史投稿扫描
- 后续增量扫描
- BV 号去重
- 视频核心元数据存储
- 时长筛选
- 视频下载
- `PENDING → DOWNLOADING → DOWNLOADED / FAILED` 状态机
- 失败重试
- Bilibili 登录与人工验证介入
- 默认 40 MB/s 下载限速
- 运行时限速调整
- btop 风格 TUI
- WBI、风控重试、DASH + ffmpeg 合并
- 下载 worker 原子认领，避免并发重复下载

当前验证中已经确认 1080P 与 4K 下载链路可行。

### 当前 v0.1 遗留项

来自现有 `PROGRESS.md`：

- `download --qn` 命令行清晰度覆盖尚未完成
- 启动时恢复孤儿 `DOWNLOADING` 尚未完成
- 多 P 视频目前只处理第一 P
- 当前没有真正的断点续传，`.part` 主要用于原子落盘

这些问题中，前两项可以在 v0.2 的 CLI/状态恢复工作中一并处理；多 P 与断点续传暂不作为本轮核心需求。

---

# 3. v0.2.0 开发目标

## 3.1 完整 CLI 工作流

CLI 是 v0.2 的重点。核心能力应能够完全脱离 TUI 使用。

建议命令结构：

```bash
python main.py login

python main.py add <mid>
python main.py remove <mid>
python main.py list

python main.py scan [mid]
python main.py check [mid]

python main.py download [mid]
python main.py download-bv <bvid> [options]

python main.py retry [mid]
python main.py status [mid]

python main.py blacklist add <mid> <keyword>
python main.py blacklist remove <mid> <keyword>
python main.py blacklist list <mid>

python main.py limit [MB/s]
python main.py run [--once]
```

其中：

- `scan`：扫描 Bilibili 投稿并更新本地数据库，同时检查本地文件状态。
- `check`：只进行数据库记录与本地文件的一致性检查，不请求 Bilibili 投稿列表。
- `download`：从数据库中选择待处理视频并下载。
- `download-bv`：输入 BV 号直接下载，**绕过 UP 专属筛选规则**。
- `preview`：预览当前筛选/下载决策，但不实际下载。
- `status`：展示当前 UP 或全局任务状态。
- `blacklist`：管理每个 UP 主独立的标题黑名单。
- `limit`：查看或修改下载带宽上限。

> 实际命令命名可以在实现时统一，但上述职责必须保留。

---

# 4. `scan` 的定义

`scan` 是本项目的核心日常操作。

例如：

```bash
python main.py scan 486906719
```

执行流程：

```text
Bilibili 投稿列表
      ↓
发现视频
      ↓
读取 BV
      ↓
查询 SQLite
      ↓
新视频 → 创建记录
旧视频 → 更新必要元数据
      ↓
检查本地文件
      ↓
输出扫描结果
```

### 首次扫描

新增 UP 主第一次扫描时，默认执行历史投稿的全量扫描。

### 后续扫描

后续默认增量扫描。

连续发现一定数量历史视频后可停止向旧投稿扩展；当前 v0.1 推荐阈值为连续 10 个已存在视频。

### `scan` 必须产生的结果分类

至少应能够区分：

- `NEW`：新发现的视频
- `EXISTING`：数据库已存在
- `DOWNLOADED`：已下载且本地文件存在
- `MISSING`：数据库记录为已下载，但本地文件缺失
- `FILTERED`：根据当前下载规则不准备下载
- `READY`：符合当前下载规则，可下载
- `FAILED`：最近下载失败

---

# 5. 数据库与本地文件的一致性

数据库仍然是状态中心，但 v0.2 不再简单假设：

```text
DOWNLOADED == 文件一定存在
```

例如数据库中：

```text
bvid = BVxxxx
status = DOWNLOADED
path = downloads/UP/BVxxxx.mp4
```

如果实际文件不存在，则 `check` / `scan` 应识别：

```text
MISSING
```

并允许将其恢复为可下载状态：

```text
MISSING → PENDING
```

这样用户手动删除文件后，系统能够自动恢复。

---

# 6. 视频元数据

第一版已经确定的视频核心字段继续保留：

| 字段 | 含义 |
|---|---|
| `bvid` | BV 号，主键/唯一标识 |
| `mid` | UP 主 UID |
| `duration` | 视频长度，单位秒 |
| `title` | 视频标题 |
| `description` | 视频简介 |
| `pic` | 封面 URL |
| `url` | Bilibili 视频 URL |
| `update_time` | 本地记录最近更新时间 |

不加入播放量、点赞、评论、弹幕、投币等统计字段。

下载状态继续保持独立：

```text
PENDING
DOWNLOADING
DOWNLOADED
FAILED
FILTERED
```

建议额外记录：

```text
filter_reason
```

例如：

```text
duration_out_of_range
blacklist: TEST
invalid_duration
```

这样 `FILTERED` 不会变成一个没有原因的黑盒状态。

---

# 7. 时长筛选

时长使用秒存储：

```text
duration: INTEGER
```

筛选规则：

```text
min_duration <= duration <= max_duration
```

上下边界均包含。

例如：

```text
300 <= duration <= 1800
```

代表 5 分钟到 30 分钟，包含 5 分钟与 30 分钟。

默认配置沿用当前项目：

```yaml
filter:
  min_duration: 300
  max_duration: 1800
```

v0.2 应允许 CLI 临时覆盖：

```bash
python main.py download <mid> --min-duration 300 --max-duration 1800
```

原则：

```text
config.yaml = 默认规则
CLI 参数 = 本次任务临时覆盖
```

---

# 7.1 发布时间筛选（用户新增）

支持按视频发布时间筛选下载（需扫描保存发布时间，`video.created` 字段）。

格式：`20xx.xx.xx`，`0` 表示不限制。

```bash
python main.py download <mid> --min-date 2025.10.01 --max-date 2026.01.25   # 只下这段时间发布的
python main.py download <mid> --min-date 0 --max-date 2026.01.25            # 一直到 2026.01.25
python main.py download <mid> --min-date 2025.10.01 --max-date 0            # 从 2025.10.01 到现在
python main.py preview <mid> --min-date 2025.01.01 --max-date 2025.12.31    # 预览同样支持
```

规则顺序：时长 → 日期 → 黑名单。日期边界包含当天；发布时间缺失时标为 `date_missing` 过滤。

---

# 8. UP 专属标题黑名单

## 8.1 数据归属

黑名单属于 UP 主，而不是全局规则。

例如：

```text
UP A
  TEST
  广告

UP B
  混剪
```

A 与 B 的规则完全独立。

黑名单应持久化保存，而不是只存在于进程内存中。

---

## 8.2 匹配方式

采用：

> **大小写不敏感的连续子串包含匹配。**

例如标题：

```text
TESTDATAABC
TESAAAB
```

黑名单：

```text
TEST
```

结果：

```text
TESTDATAABC  → 命中 → 本次不下载
TESAAAB      → 不命中 → 可以继续判断其他规则
```

下面这些都应认为命中：

```text
TEST
Test
TESTABC
ABC TEST
AAA TEST BBB
```

而 `TESAAAB` 不命中。

实现时建议使用 `casefold()` 做大小写无关比较。

---

## 8.3 黑名单发生在下载阶段

这是一个冻结的设计决策：

> **黑名单不阻止视频被扫描，也不阻止视频写入数据库。黑名单只影响下载决策。**

因此：

```text
scan
 ↓
保存视频
 ↓
download
 ↓
时长筛选
 ↓
黑名单筛选
 ↓
决定是否下载
```

例如：

```text
标题：TESTDATAABC
BV：BVxxxx
```

即使命中 `TEST`：

- 数据库仍保存该视频
- scan 仍认为该视频已发现
- 下载阶段将其标记为 `FILTERED`
- `filter_reason = blacklist: TEST`

如果以后删除 `TEST` 黑名单，这个视频可以重新进入下载流程。

---

# 9. BV 直接下载模式

新增：

```bash
python main.py download-bv BV1xxxxxxxx
```

也建议支持多个 BV：

```bash
python main.py download-bv BV1xxx BV2xxx BV3xxx
```

## 核心规则

BV 直接下载：

- 不要求先 `add` UP
- 不要求先 `scan` UP
- 不使用 UP 专属黑名单
- 不使用 UP 专属时长筛选
- 直接依据 BV 获取视频信息和播放地址并下载

即：

```text
BV
 ↓
获取元数据
 ↓
获取播放地址
 ↓
执行下载
```

如果用户显式指定了 BV，则默认认为：

> 用户明确要求下载这个视频。

这样不会出现“因为所属 UP 的黑名单命中而拒绝显式 BV 下载”的反直觉行为。

---

# 10. 清晰度选择

当前底层已经验证过 1080P 与 4K 下载链路，因此 v0.2 重点是完善命令行控制。

建议用户层提供可读参数：

```bash
python main.py download <mid> --quality 720p
python main.py download <mid> --quality 1080p
python main.py download <mid> --quality 1080p+
python main.py download <mid> --quality 1080p60
python main.py download <mid> --quality 4k
```

BV 直接下载同样支持：

```bash
python main.py download-bv BVxxxx --quality 4k
```

内部仍映射到 Bilibili 的 `qn`。

推荐映射：

```text
720p    → qn 64
1080p   → qn 80
1080p+  → qn 112   （1080P 高码率）
1080p60  → qn 116
4k      → qn 120
```

如果目标画质不可用，应采用安全回退策略，并在 CLI/TUI 中明确提示实际获得的画质，而不是静默声称下载了目标画质。

---

# 11. 视频 / 音频模式

下载模式提供：

```text
video
 audio
```

例如：

```bash
python main.py download <mid> --type video
python main.py download <mid> --type audio
```

BV：

```bash
python main.py download-bv BVxxxx --type audio
```

## 音频规则

v0.2 第一阶段音频仅保存 Bilibili 提供的最佳音频流，优先保存为：

```text
.m4a
```

不主动转换为 MP3，不增加有损转码过程。

未来如果增加 MP3 转码，应作为单独功能，不与本版本混在一起。

---

# 12. 预览模式（Dry Run）

新增一个非常重要的操作：

```bash
python main.py preview [mid]
```

也可与实际下载参数组合：

```bash
python main.py preview 486906719 \
  --quality 1080p \
  --type video \
  --min-duration 300 \
  --max-duration 1800
```

预览模式：

- 可以读取数据库
- 可以执行所有下载筛选规则
- 可以检查本地文件
- 可以输出最终决策
- **绝对不能开始实际下载**

输出建议：

```text
NEW         12
DOWNLOADED  384
MISSING      2
DURATION    31
BLACKLIST    7
READY       10
FAILED       1
```

它的主要作用是：

> 在真正消耗带宽前，让用户确认本次下载规则会选择哪些视频。

---

# 13. 规则解释

预览和实际下载时，都应该能够解释某个视频为什么被下载或者为什么被过滤。

示例：

```text
Video: TESTDATAABC
BV: BVxxxx

Rule evaluation:
  Duration:     532s          PASS
  Duration range: 300~1800    PASS
  Blacklist:    TEST          FAIL
  Download:                    NO
  Reason:       blacklist: TEST
```

另一个例子：

```text
Video: Robot Tutorial
BV: BVyyyy

Rule evaluation:
  Duration:      45s          FAIL
  Duration range: 300~1800   FAIL
  Blacklist:     none         PASS
  Download:                    NO
  Reason:        duration_out_of_range
```

这样用户可以直接理解：

> “为什么这个视频没有下载？”

而不是只看到 `SKIPPED`。

---

# 14. 状态查看

建议加入：

```bash
python main.py status
python main.py status <mid>
```

至少显示：

```text
视频总数
PENDING
DOWNLOADING
DOWNLOADED
FAILED
FILTERED
MISSING
```

按 UP 查询时，应该同时显示该 UP 的：

- 视频数量
- 黑名单数量
- 最近扫描时间
- 待下载数量
- 已下载数量
- 失败数量
- 文件缺失数量

---

# 15. 限速

默认下载速度继续为：

```text
40 MB/s
```

命令行：

```bash
python main.py limit
python main.py limit 20
```

表示查看 / 设置最大下载速度。

TUI 中继续允许运行时调整。

内部继续沿用当前已验证的令牌桶 `RateLimiter`，不要重复实现第二套限速逻辑。

---

# 16. 登录

当前 v0.1 已经实现扫码登录 / Cookie 登录以及人工验证介入。

v0.2 不需要重新设计登录体系。

要求继续保持：

- 不保存明文账号密码
- 会话 Cookie 可持久化
- 验证码 / 二次验证出现时允许人工介入
- 人工处理完成后程序继续任务
- 登录失败不能导致整个程序崩溃

登录流程与爬虫、下载器继续解耦。

---

# 17. TUI

继续使用 btop 风格，不开发传统 GUI。

TUI 至少需要逐步加入：

- 当前 UP
- 当前任务
- 扫描进度
- 新视频数
- 已存在数
- 过滤数
- 下载数
- 失败数
- 当前速度
- 最大速度
- 当前清晰度
- 当前媒体类型
- 当前规则摘要
- 最近日志

CLI 是完整控制入口，TUI 是实时运行监控入口。

---

# 18. 配置优先级

采用：

```text
代码默认值
    ↓
config.yaml
    ↓
CLI 参数
    ↓
本次任务临时覆盖
```

例如：

```yaml
filter:
  min_duration: 300
  max_duration: 1800

download:
  max_speed_mbps: 40
  qn: 80
  type: video
```

命令行：

```bash
python main.py download 123456 \
  --quality 4k \
  --type audio \
  --min-duration 600 \
  --max-duration 3600
```

只改变本次任务，不修改永久默认配置。

---

# 19. 建议的数据模型扩展

现有 `up` / `video` 两表继续作为核心结构。

## UP

```text
up
├── mid
├── name
├── face
├── description
├── first_crawl_time
├── last_crawl_time
├── enabled
└── save_path
```

增加 UP 专属黑名单的持久化结构。

可以选择单独建立：

```text
up_blacklist
├── id
├── mid
├── keyword
├── created_at
```

而不是把多个关键词序列化到一个字符串字段中。

这样方便：

- 增删关键词
- 去重
- 查询
- 后续扩展

## VIDEO

继续：

```text
video
├── bvid
├── mid
├── duration
├── title
├── description
├── pic
├── url
├── update_time
├── download_status
├── download_path
├── download_time
├── download_error
├── filter_reason
```

如实现需要，可增加实际文件存在状态相关字段，但要避免与 `download_status` 产生重复且互相矛盾的事实来源。

---

# 20. 下载决策模型

建议将所有规则统一成一个可解释的决策流程：

```text
Video
 ↓
是否是显式 BV 下载？
 ├─ 是 → 绕过 UP 规则 → 下载
 └─ 否
      ↓
    时长检查
      ↓
    黑名单检查
      ↓
    本地文件检查
      ↓
    下载状态检查
      ↓
    READY / FILTERED / DOWNLOADED / MISSING / FAILED
```

最关键的是：

> **筛选逻辑与下载器本身解耦。**

下载器只负责“下载已经被判定为 READY 的任务”。

规则系统负责“为什么 READY / 为什么 FILTERED”。

这样未来增加更多规则不会破坏 downloader。

---

# 21. 推荐模块结构

沿用现有项目，不做无必要重构：

```text
bilibili_crawler/
├── app.py
├── state.py
│
├── cli/
│   ├── commands.py
│   ├── tui.py
│   └── keyreader.py
│
├── bilibili/
│   ├── client.py
│   ├── auth.py
│   ├── user.py
│   └── video.py
│
├── crawler/
│   ├── user_crawler.py
│   ├── video_crawler.py
│   └── scheduler.py
│
├── database/
│   ├── database.py
│   ├── models.py
│   └── repository.py
│
├── filter/
│   ├── duration_filter.py
│   ├── blacklist_filter.py
│   ├── decision.py
│   └── explain.py
│
├── downloader/
│   ├── limiter.py
│   ├── downloader.py
│   └── task_manager.py
│
└── config/
    └── configuration.py
```

这里最重要的新概念是：

```text
filter/decision.py
```

负责统一产生：

```text
READY
FILTERED
以及原因
```

这样 TUI、CLI、preview、download 可以共用同一套规则。

---

# 22. v0.2 开发顺序

推荐按下面顺序实施，避免功能互相阻塞。

## Phase 1：CLI 基础整理

- 统一命令解析
- `scan [mid]`
- `download [mid]`
- `download-bv`
- `status`
- `check`

## Phase 2：下载参数体系

- `--quality`
- `--type`
- `--min-duration`
- `--max-duration`
- CLI 覆盖 config

## Phase 3：黑名单

- 数据库结构
- add/remove/list
- 大小写不敏感子串匹配
- UP 独立规则
- `filter_reason`

## Phase 4：统一规则决策器

- duration filter
- blacklist filter
- file state check
- download state check
- 规则解释

## Phase 5：preview

- dry-run
- 输出统计
- 输出单视频规则解释

## Phase 6：本地文件一致性

- `check`
- `scan` 时自动 check
- `MISSING → PENDING`
- 孤儿 `DOWNLOADING` 恢复

## Phase 7：TUI 升级

- 显示新增状态
- 显示规则
- 显示当前下载参数
- 增强实时日志

## Phase 8：测试与回归

必须覆盖：

- BV 去重
- 新/旧视频判断
- 时长边界
- 黑名单大小写
- 黑名单子串
- 黑名单只在下载阶段生效
- BV 直链下载绕过黑名单
- preview 不产生实际下载
- MISSING 自动恢复
- 音频 M4A
- 质量参数映射
- CLI 覆盖配置
- 限速不回归
- 多 worker 不重复下载

---

# 23. 暂不开发

以下内容明确不属于 v0.2 核心范围：

- 播放量/点赞/评论/弹幕统计
- 标题正向关键词白名单
- 发布时间筛选
- AI 视频内容分析
- 自动摘要
- Web UI
- 多用户云端服务
- 多 P 完整支持
- MP3 转码
- 复杂断点续传系统

任何新增需求需要进入下一版本规划，不应直接侵入 v0.2 核心范围。

---

# 24. v0.2 验收标准

以下流程必须完整可用：

```text
1. 登录 Bilibili
2. 添加 UP
3. scan UP
4. 数据库出现新投稿
5. check 本地文件
6. preview 当前下载规则
7. 查看每个视频为什么下载/过滤
8. download UP
9. 只下载 READY 视频
10. blacklist add 添加关键词
11. 再次 preview
12. 命中关键词的视频变为 FILTERED
13. 删除黑名单
14. 原视频重新变为 READY
15. 手动删除一个已下载文件
16. scan / check 发现 MISSING
17. MISSING 恢复为 PENDING
18. download 再次下载
19. download-bv BVxxxx
20. 明确 BV 下载绕过 UP 黑名单
21. download-bv BVxxxx --type audio
22. 得到 M4A
23. download-bv BVxxxx --quality 4k
24. 得到目标或明确回退画质
25. 限速默认 40 MB/s，可运行时调整
```

---

# 25. 开发纪律

### 不推翻已验证的模块

当前 `bilibili/client.py`、`auth.py`、`downloader`、`limiter.py` 等已经经过实际验证。新功能应优先复用现有模块，而不是重写。

### 不让 CLI 成为业务逻辑堆积地

CLI 只负责：

```text
解析参数
→ 调用 service
→ 展示结果
```

不能把数据库、过滤、下载策略全部塞进 `commands.py`。

### 不让 TUI 直接操作数据库

TUI 读取共享状态或调用应用层服务，不直接承担业务逻辑。

### 不用“文件存在”替代数据库状态

文件系统是实际状态的验证来源之一，SQLite 仍然是任务状态中心。

### 所有规则必须可解释

新增任何筛选条件，都应该能够回答：

```text
为什么下载？
为什么没有下载？
```

---

# 26. 最终目标

v0.2 完成后，程序应从一个“自动扫描 + 自动下载”的工具，升级成为一个具有清晰命令语义的本地视频采集系统：

```text
               ┌──────────────┐
               │    Bilibili  │
               └──────┬───────┘
                      │
                  scan / BV
                      │
                      ▼
               ┌──────────────┐
               │    SQLite    │
               │  本地状态中心 │
               └──────┬───────┘
                      │
          ┌───────────┴───────────┐
          │                       │
       UP 下载                 BV 直下
          │                       │
      UP 专属规则               绕过 UP 规则
          │                       │
    ┌─────┴─────┐                 │
    │           │                 │
  时长        黑名单              │
    │           │                 │
    └─────┬─────┘                 │
          │                       │
          └──────────┬────────────┘
                     ▼
               Download Engine
                     │
           ┌─────────┴─────────┐
           │                   │
         Video                Audio
           │                   │
          MP4                 M4A
```

**v0.2 的重点不是增加尽可能多的功能，而是把“扫描—判断—预览—下载—检查—恢复”这条链路做成稳定、可解释、可重复运行的工作流。**
