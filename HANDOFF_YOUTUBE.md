# Bilibili / YouTube 双平台功能交接说明

更新时间：2026-08-30

## 当前分支与提交

- 当前分支：`bilibili_branch_download`
- 已推送远程分支：`origin/bilibili_branch_download`
- 远程主线也已快进到当前实现：`origin/main`
- 最近提交：
  - `a0f1a6c` 完善 YouTube 日期筛选
  - `a52ff40` 支持 YouTube 频道别名解析
  - `0ce4cd6` 新增 Bilibili 与 YouTube 独立下载模式

播放器分支 `bilibili_branch_player` 未被修改，仍保持独立。

## 已完成内容

### 2026-08-30 扫描触发修复

- 修复桌面“任务与视频”页选择“全部 UP”时将空选择误传为字符串 `"None"` 的问题。
- `YouTubeService.scan(None)` 现在扫描全部启用频道，CLI 也可省略 `--channel`。
- 扫描统一使用频道 `/videos` 地址；频道主页只返回导航条目时不再被误判为扫描成功。
- 已用 KAF 公开频道实测扫描 337 个视频，并按 120–150 秒范围成功下载 14 个 720p 视频。

### 2026-08-30 下载质量与登录

- “UP 管理 → 下载”现在遵循设置页的清晰度和媒体类型，不再固定为默认视频。
- YouTube 视频指定清晰度时使用精确高度匹配，无法提供目标清晰度会失败并提示，避免静默降级。
- 设置页支持 Netscape Cookie 文件或 Chrome/Edge/Firefox 等浏览器 Cookie，并可后台检查登录态；音频任务固定输出 `.m4a`。
- Windows Chromium 浏览器 Cookie 数据库被占用时会显示对应浏览器的明确提示，建议完全退出浏览器后重试或改用 Netscape Cookie 文件。
- 新版 Edge/Chrome 的 v20 App-Bound Encryption 目前无法由稳定版 yt-dlp 解密，需导出 Netscape Cookie 文件。

### YouTube 独立数据库

- 新增 `bilibili_crawler/youtube.py`。
- YouTube 数据默认保存到 Bilibili 数据库同目录下的 `youtube.db`。
- YouTube 使用独立的频道、视频、媒体状态、黑名单、特定下载名单和筛选配置表。
- 不复用 Bilibili 的 `mid`、`bvid` 或 Bilibili 数据库表。
- 视频和音频状态分别保存，可独立下载。

### 输入自动识别

`add` 命令现在接受字符串：

- 数字 UID：识别为 Bilibili。
- `UC...`：识别为 YouTube 频道。
- `@handle`：识别为 YouTube 频道。
- `youtube.com/channel/UC...`：识别为 YouTube 频道。
- 单视频 URL、播放列表 URL 和非法输入会拒绝，不写入数据库。

### YouTube 扫描与下载

- 使用 `yt-dlp` 扫描公开频道。
- 支持 MP4 视频和 M4A 音频下载。
- 支持清晰度、时长、日期、黑名单和特定下载名单筛选。
- 支持停止信号、失败状态和文件检查的基础流程。
- 下载目录自动写入：

```text
<save_root>/Bilibili/...
<save_root>/YouTube/...
```

### 桌面 UI

- 左侧新增来源切换：`Bilibili / YouTube`。
- 总览、UP 管理、任务与视频页会根据来源切换数据。
- “添加 UP”改为文本输入，可输入 UID、频道 URL、`@handle` 或 UC ID。
- 新增“已下载”页面，并分为 Bilibili / YouTube 两个标签。
- YouTube 任务可以查看、预览和下载。

### 打包与文档

- `requirements.txt` 和 `pyproject.toml` 已加入 `yt-dlp`。
- PyInstaller spec 已包含 `yt_dlp` 动态导入。
- EXE 已重新构建：

```text
D:\Github\bilibili_branch_download\dist\BilibiliVideoWorkbench.exe
```

- 根目录 `README.md` 已更新为快速上手说明。

## 验证方式

离线测试：

```powershell
cd D:\Github\bilibili_branch_download
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test*.py"
```

当前结果：

```text
Ran 92 tests
OK (skipped=1)
```

新增测试文件：`tests/test_youtube.py`，目前覆盖输入识别和 YouTube 数据库/媒体状态隔离。

EXE 自检：

```powershell
.\dist\BilibiliVideoWorkbench.exe --check-only --skip-ffmpeg
```

预期退出码：`0`。

## CLI 示例

```powershell
# 添加 YouTube 频道
python main.py add @example

# 扫描频道
python main.py scan --source youtube --channel UCxxxxxxxxxxxxxxxxxxxxxxxx

# 预览
python main.py preview --source youtube --channel UCxxxxxxxxxxxxxxxxxxxxxxxx --type video

# 下载视频或音频
python main.py download --source youtube --channel UCxxxxxxxxxxxxxxxxxxxxxxxx --type video
python main.py download --source youtube --channel UCxxxxxxxxxxxxxxxxxxxxxxxx --type audio

# 查看状态
python main.py status --source youtube --channel UCxxxxxxxxxxxxxxxxxxxxxxxx
```

## 已知限制与后续工作

1. YouTube 目前只面向频道扫描；Google OAuth 登录、直播和播放列表仍未实现。已支持通过
   Netscape Cookie 文件或本机浏览器 Cookie 使用登录态（包括需要登录的高质量/会员格式）。
2. YouTube 的 `retry` CLI 目前只提示后续重新执行下载，建议补充与 Bilibili 一致的 FAILED → PENDING 重置逻辑。
3. YouTube 文件同步目前是基础版本，建议补充递归索引、重复文件选择、目录切换和缺失文件恢复的专门测试。
4. YouTube 在线测试尚未纳入默认测试集，建议增加 `test_live_youtube.py`，并通过环境变量显式启用。
5. 桌面端来源切换已接入，但仍需补充 Qt offscreen 测试，覆盖切换来源、任务运行期间禁止切换和 YouTube 任务完成后的刷新。
6. `youtube.db` 是本地运行数据库，不应提交到 Git；用户现有的 `config.yaml`、`kaf_full_scan.db` 也不要覆盖或提交。

## 接手建议

建议下一步先完善 YouTube 离线测试和 `retry/check` 行为，再进行少量公开频道在线冒烟测试。不要直接修改 Bilibili 的数据库 schema 或扫描器，两个平台的数据隔离是当前实现的核心约束。
