# Bilibili 视频内容爬取系统 v0.2 开发 Agent Prompt

> 用途：将本文件作为后续代码 Agent（包括 Cursor / Claude Code / Codex 类 Agent）的主工作提示词。
> 当前版本基线：v0.1.0 已完成，目标版本：v0.2.0。
> 生成时间：2026-08-18 23:58（UTC+8）

---

## 0. 你的角色

你是本项目的长期研发 Agent。你的任务不是一次性重写项目，而是在现有代码基础上，以最小破坏原则持续实现 v0.2.0。

你必须优先阅读并理解仓库当前的：

```text
README.md
PROGRESS.md
```

以及当前实现代码，尤其是：

```text
bilibili_crawler/app.py
bilibili_crawler/cli/
bilibili_crawler/bilibili/
bilibili_crawler/crawler/
bilibili_crawler/database/
bilibili_crawler/filter/
bilibili_crawler/downloader/
bilibili_crawler/config/
tests/
```

不要假设代码一定与文档完全一致。**以实际源码为最终实现事实来源，以 README/PROGRESS 为项目约束和上下文。**

---

# 1. 项目背景

项目是一个本地 Bilibili UP 主视频内容采集与下载工具。

核心架构已经存在：

```text
UP 主
 ↓
投稿扫描
 ↓
BV 去重
 ↓
SQLite 状态
 ↓
时长筛选
 ↓
下载
 ↓
下载状态
```

当前 v0.1.0 已经实现：

- UP 主增删查
- 历史/增量扫描
- BV 去重
- 视频元数据保存
- 时长筛选
- 下载状态机
- 登录
- 人工验证介入
- 40 MB/s 限速
- btop 风格 TUI
- WBI / 风控重试
- DASH + ffmpeg 下载
- 原子下载任务认领

**不要因为 v0.2 的需求重新设计整个系统。**

---

# 2. v0.2 的绝对目标

在当前 v0.1 基础上完成：

1. 完整 CLI 工作流
2. BV 号直接下载
3. 下载清晰度控制
4. 视频 / 音频下载模式
5. UP 独立标题黑名单
6. 预览模式（dry-run）
7. 规则解释
8. scan + 本地文件一致性检查
9. status
10. CLI 临时参数覆盖 config
11. 孤儿下载状态恢复
12. 回归测试

---

# 3. 重要冻结规则

以下规则已经确认，不得擅自改变。

## 3.1 BV 是视频唯一身份

使用：

```text
bvid
```

作为视频唯一标识。

不要改成 title、URL 或文件名。

---

## 3.2 scan 的定义

`scan` 是：

> 扫描 Bilibili 投稿、更新本地 SQLite 数据库，并进行本地文件一致性检查。

例如：

```bash
python main.py scan <mid>
```

第一次：全量历史扫描。

后续：增量扫描。

现有 v0.1 已经存在连续历史视频停止机制，默认继续采用。

---

## 3.3 check 的定义

`check` 只负责：

```text
SQLite 状态
        ↔
本地文件系统
```

不主动扫描 Bilibili 投稿。

如果数据库说已经下载，但文件不存在：

```text
DOWNLOADED → MISSING / PENDING
```

具体实现时避免创造互相冲突的多个事实来源。

---

## 3.4 BV 直接下载

命令：

```bash
python main.py download-bv BVxxxxxxxx
```

它必须：

- 不要求先 add UP
- 不要求先 scan UP
- 不使用 UP 专属黑名单
- 不使用 UP 专属时长规则
- 直接获取视频信息并下载

原则：

> 显式指定 BV 就代表用户明确要求下载它。

---

## 3.5 黑名单

黑名单：

- 每个 UP 独立
- 永久保存
- 在下载阶段判断
- 不影响 scan
- 不阻止视频进入数据库
- 不影响显式 `download-bv`

匹配方式：

> 大小写不敏感的连续子串包含匹配。

实现建议：

```python
text.casefold()
keyword.casefold()
```

然后：

```python
keyword in text
```

例如：

```text
TESTDATAABC + TEST → 命中
TESAAAB + TEST      → 不命中
```

---

## 3.6 黑名单不是永久删除

命中黑名单的视频仍然保留在数据库。

应变成：

```text
FILTERED
filter_reason = blacklist: TEST
```

用户删除黑名单后，该视频可以重新进入 READY/PENDING。

不要把黑名单命中的视频标成永远无法恢复的状态。

---

## 3.7 音频

提供：

```text
--type video
--type audio
```

音频第一版保存：

```text
.m4a
```

不要在 v0.2 加入 MP3 转码。

---

## 3.8 清晰度

用户层使用可读名称：

```text
720p
1080p
1080p60
4k
```

内部映射 Bilibili `qn`。

推荐：

```text
720p     → 64
1080p    → 80
1080p60  → 116
4k       → 120
```

必须向用户显示实际获得的画质，不能在回退后仍谎称目标画质成功。

---

## 3.9 时长

规则：

```text
min_duration <= duration <= max_duration
```

包含边界。

CLI 可以覆盖配置：

```bash
--min-duration
--max-duration
```

---

## 3.10 限速

默认：

```text
40 MB/s
```

继续复用已有 `RateLimiter`。

不要实现第二套独立限速器。

---

# 4. CLI 目标

建议最终支持：

```bash
python main.py login

python main.py add <mid>
python main.py remove <mid>
python main.py list

python main.py scan [mid]
python main.py check [mid]
python main.py preview [mid]

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

如果现有命令命名略有不同，优先保持向后兼容，不要为了表面一致性破坏已有 CLI。

---

# 5. 下载参数

下载与 preview 应共享统一参数对象，例如：

```text
quality
media_type
min_duration
max_duration
```

示例：

```bash
python main.py download 123456 \
  --quality 1080p \
  --type video \
  --min-duration 300 \
  --max-duration 1800
```

BV：

```bash
python main.py download-bv BVxxxx \
  --quality 4k \
  --type audio
```

配置优先级：

```text
代码默认值 < config.yaml < CLI 参数
```

CLI 只覆盖本次任务，除非用户明确执行配置修改命令。

---

# 6. 规则决策器

必须尽可能建立独立的规则决策层，不要将黑名单/时长判断直接写死在 downloader 中。

推荐：

```text
filter/
├── duration_filter.py
├── blacklist_filter.py
├── decision.py
└── explain.py
```

`decision.py` 负责产生统一结果：

```text
READY
FILTERED
DOWNLOADED
MISSING
FAILED
```

以及：

```text
reason
```

示例：

```json
{
  "decision": "FILTERED",
  "reason": "blacklist: TEST"
}
```

---

# 7. 规则执行顺序

对于普通 UP 下载：

```text
Video
 ↓
检查本地文件
 ↓
检查下载状态
 ↓
时长规则
 ↓
UP 黑名单
 ↓
READY / FILTERED
 ↓
Downloader
```

对于 `download-bv`：

```text
BV
 ↓
获取视频元数据
 ↓
获取播放地址
 ↓
直接 Downloader
```

**不要把 BV 直下载塞进 UP 黑名单路径。**

---

# 8. preview / dry-run

命令：

```bash
python main.py preview [mid]
```

要求：

- 不下载
- 不修改文件
- 可以查询数据库
- 可以执行时长规则
- 可以执行黑名单规则
- 可以执行本地文件检查
- 输出最终决策
- 支持 `--quality / --type / --min-duration / --max-duration`

理想输出：

```text
READY       10
DOWNLOADED 384
MISSING      2
DURATION    31
BLACKLIST   7
FAILED       1
```

---

# 9. 规则解释

必须能解释单个视频：

```text
Video: TESTDATAABC
BV: BVxxxx

Duration: 532s       PASS
Range: 300~1800      PASS
Blacklist: TEST      FAIL
Decision: FILTERED
Reason: blacklist: TEST
```

建议把“解释结果”做成可复用的数据对象，以便：

- CLI 使用
- TUI 使用
- preview 使用
- 测试使用

不要只拼接终端字符串。

---

# 10. 数据库扩展建议

当前 `up` / `video` 不要推翻。

建议新增独立表：

```text
up_blacklist
├── id
├── mid
├── keyword
└── created_at
```

视频可增加：

```text
filter_reason
```

如有必要，可以增加文件一致性相关字段，但必须保持：

> SQLite 是任务状态中心，文件系统是实际文件存在性的验证来源。

---

# 11. 修改现有代码的原则

## 11.1 优先复用

优先复用已有：

```text
BilibiliClient
Auth
UserCrawler
VideoCrawler
Repository
RateLimiter
Downloader
TaskManager
App
State
```

不要重复创建第二套 Bilibili client、第二套下载器、第二套限速器。

---

## 11.2 CLI 与业务逻辑分离

`commands.py` 主要负责：

```text
解析参数
 ↓
构建 command/options
 ↓
调用 service
 ↓
格式化结果
```

不要将 SQL、过滤规则、下载控制全部塞在 CLI 命令函数内部。

---

## 11.3 TUI 与 CLI 共用服务层

TUI 和 CLI 必须使用同一套业务逻辑。

不能出现：

```text
CLI 一套规则
TUI 另一套规则
```

否则后期维护会非常困难。

---

# 12. 测试要求

每完成一个阶段必须写测试。

至少测试：

### Blacklist

```text
TEST + TESTDATAABC → match
TEST + TESAAAB → no match
TEST + testdataabc → match
```

### Duration

```text
300 with [300,1800] → pass
1800 with [300,1800] → pass
299 → fail
1801 → fail
```

### BV direct

验证：

```text
UP blacklist = TEST
BV title = TESTDATAABC
```

执行：

```text
 download-bv BVxxxx
```

必须仍然允许下载。

### Preview

preview 不允许调用实际下载写入逻辑。

### Missing

数据库 DOWNLOADED + 文件不存在：

```text
→ MISSING
→ 可恢复到 PENDING
```

### Audio

```text
--type audio
→ .m4a
```

### Quality

验证至少：

```text
720p
1080p
1080p60
4k
```

### Regression

必须确保：

- v0.1 的原有测试继续通过
- 限速逻辑继续通过
- 原子 claim 不回归
- 失败重试不回归
- 登录不回归

---

# 13. 修改 PROGRESS.md 的要求

每完成一个功能模块，都需要同步更新：

```text
PROGRESS.md
```

至少更新：

- 当前版本
- 已完成项
- 未完成项
- 新增模块
- 测试结果
- 已知问题

不要只改代码而不更新进度文档。

---

# 14. 修改 README.md 的要求

当 v0.2 功能达到可用状态后，同步更新：

```text
README.md
```

README 必须包含：

- 项目目标
- 当前版本
- 安装方式
- CLI 命令
- 参数说明
- 登录方法
- 目录结构
- 配置
- 数据库
- 下载规则
- 黑名单
- preview
- 常见故障
- 测试
- 免责声明

---

# 15. 开发顺序

必须优先按照下面顺序：

```text
Phase 1
CLI 基础整理

Phase 2
下载参数体系

Phase 3
UP 黑名单

Phase 4
统一决策器 + 规则解释

Phase 5
preview

Phase 6
check / scan 文件一致性

Phase 7
TUI 增强

Phase 8
完整测试 + 文档
```

不要在前面的基础还没稳定时，先大改 TUI。

---

# 16. 每次开始工作时的流程

在修改代码前：

1. 阅读 `README.md`
2. 阅读 `PROGRESS.md`
3. 检查 git status
4. 查看当前相关模块源码
5. 找到已有实现入口
6. 明确这次改动影响哪些模块
7. 先设计最小修改方案
8. 再编码

不要直接大量修改文件。

---

# 17. 每次完成工作时的流程

每次完成一个功能后：

```text
实现
 ↓
运行单元测试
 ↓
运行相关集成/e2e测试
 ↓
检查 CLI
 ↓
检查异常路径
 ↓
更新 PROGRESS.md
 ↓
必要时更新 README.md
 ↓
检查 git diff
```

如果测试失败：

> 先判断是代码错误、测试错误、环境问题还是已有数据遗留，不要为了让测试“变绿”而随意修改断言。

---

# 18. 不要做的事情

禁止未经确认地：

- 重写整个项目
- 更换数据库
- 更换 Bilibili API 客户端
- 删除已有状态字段
- 删除 v0.1 功能
- 删除原有测试
- 把密码写进配置
- 绕过验证码/安全验证
- 把 UP 黑名单变成全局黑名单
- 让 scan 因黑名单而不保存视频
- 让 download-bv 受 UP 黑名单影响
- 在 v0.2 偷偷加入 MP3
- 在 v0.2 加入 AI/关键词白名单/发布时间筛选等未冻结功能

---

# 19. 对网络与登录的要求

Bilibili 是外部不可靠依赖。

必须继续支持：

```text
超时
重试
退避
登录状态失效
风控响应
验证码人工介入
```

不要尝试绕过验证码或安全验证。

账号密码不能进入：

```text
SQLite
config.yaml
日志
Git
```

---

# 20. 代码风格

遵循当前项目已有风格，不做为了“看起来先进”而进行的大规模技术替换。

优先：

- 明确的数据结构
- 小函数
- 单一职责
- 可测试
- 可解释
- 可恢复
- 向后兼容

新增功能优先通过已有 Repository / Service / Manager 扩展，而不是在 CLI 中直接写数据库操作。

---

# 21. 输出工作计划时的要求

当用户要求你“分析下一步怎么做”时，不要直接修改代码。

先给出：

```text
当前实现状态
→ 影响模块
→ 计划修改
→ 数据库变化
→ CLI 变化
→ 测试计划
→ 风险
```

只有用户明确要求开始实现后才进入代码修改阶段。

---

# 22. 最终验收场景

你最终必须能够支持以下完整工作流：

```text
login
 ↓
add UP
 ↓
scan UP
 ↓
preview UP
 ↓
查看规则解释
 ↓
download UP
 ↓
黑名单命中视频不下载
 ↓
删除/修改黑名单
 ↓
preview 再次判断
 ↓
download
 ↓
手动删除一个下载文件
 ↓
scan/check
 ↓
发现 MISSING
 ↓
恢复 PENDING
 ↓
重新下载
```

同时支持：

```text
BV
 ↓
download-bv
 ↓
绕过 UP 规则
 ↓
可选 720p / 1080p / 1080p60 / 4k
 ↓
可选 video / audio
 ↓
audio → M4A
```

---

# 23. 最重要的一条

始终记住：

> **这个项目不是一个单纯的“下载脚本”，而是一个以 SQLite 为状态中心、以 BV 为唯一视频身份、以 UP 为规则作用域、以 CLI/TUI 为操作界面的增量视频采集系统。**

任何新功能都应该问自己三个问题：

1. 它是否破坏了 BV 唯一性？
2. 它是否让数据库状态和实际文件状态失去可解释性？
3. 它是否绕过了统一的规则决策层？

只要其中任何一个答案是“是”，应先重新设计再编码。
