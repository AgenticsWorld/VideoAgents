---
name: xhs-cdp-draft
description: |
  用「用户本机的 Google Chrome（专用自动化 profile + CDP 9222）」+ XiaohongshuSkills，把一集成片视频 + 标题/正文填充成一条小红书**视频笔记**草稿，停在发布页由用户人工点击「发布」完成提交（本 skill 永不自动发布）。
  适用场景：收到「发布 <ep> 到小红书」工单后，基于 data/projects/<slug>/publish/<ep>/ 的发布包（mp4 + seo.json + metadata.json）自动完成视频上传与 meta 信息填写、登录态检查、CDP 截图确认。
metadata:
  trigger: 发布某集成片到小红书（视频笔记，半自动，用户点最后一步发布）
  builds_on: skills/XiaohongshuSkills
---

# xhs-cdp-draft — 用本机 Chrome 半自动发布小红书视频笔记

把「一集成片视频 + 标题/正文/话题」填充成小红书**视频笔记草稿**，停在发布页让用户确认并**由用户亲手点击「发布」**。
本 skill 是对 `skills/XiaohongshuSkills` 的一层「踩坑后的可复现操作流程」，复用它的 `publish_pipeline.py` / `cdp_publish.py`（视频模式 + `--preview`）。

## 浏览器策略（为什么是"本机 Chrome + 专用 profile"）

- **用用户本机安装的 Google Chrome**（`/Applications/Google Chrome.app/...`），不是项目内置 CDP Chromium。
- CDP 无法附着到一个**没开调试端口**的已运行 Chrome，而同一个 profile 也不能被两个实例同时打开——所以不能直接接管用户日常开着的那个 Chrome 窗口。
- 因此用同一个 Chrome 程序 + **专用自动化 profile** 另起一个带调试端口的实例：
  - profile 目录：`~/Google/Chrome/XiaohongshuProfiles/default`（与 XiaohongshuSkills `account_manager` 在 macOS 下的默认目录一致，两条启动路径共享登录态）；
  - 与用户日常 Chrome 窗口**并行共存**，互不影响；
  - 小红书创作中心登录态持久化在该 profile：**首次由用户在弹出的 Chrome 窗口手动扫码登录，之后每次免登**。

## 标准操作流程

> 下面命令默认在仓库根目录执行；`<skills>` = `agents/12-publishing/publisher/skills`。

### 1) 启动本机 Chrome（CDP 127.0.0.1:9222）

```bash
bash <skills>/skill-xhs-cdp-draft/scripts/launch_cdp_chrome.sh
# 可选参数：launch_cdp_chrome.sh [PROFILE_DIR] [PORT]；已在 9222 跑着会直接复用、不重启。
```

启动日志会打印 `chrome : /Applications/Google Chrome.app/...`、`profile : ~/Google/Chrome/XiaohongshuProfiles/default` 与 `CDP UP ... on 127.0.0.1:9222`，确认即生效。
后续 `cdp_publish.py` / `publish_pipeline.py` 都用 `--reuse-existing-tab` 连这个 9222 实例，不再自己启动（`ensure_chrome` 见端口已开会跳过）。

> 若报 `SingletonLock: File exists`：说明有别的实例占着这个专用 profile（用户日常 Chrome 用的是自己的 profile，不冲突）。先关掉那个实例、删 `~/Google/Chrome/XiaohongshuProfiles/default/SingletonLock` 再重跑。

### 2) 确认登录态（未登录 → 请用户扫码）

```bash
cd <skills>/XiaohongshuSkills
python3 scripts/cdp_publish.py --reuse-existing-tab check-login
```

- 首次导航可能短暂重定向到 `/login` 再跳回（会话热身），出现 NOT LOGGED IN 先重跑一次确认。
- 确认未登录：**停下来，明确告知用户**「已打开 Chrome 窗口，请在小红书创作中心页面扫码登录」，等用户完成后再跑 `check-login` 直到 `Login confirmed`。不要用 `login` 子命令重启浏览器，窗口已经是有头的。
- 登录只需一次，之后持久化在专用 profile 里。

### 3) 选定成片视频

按优先级取（都在 `data/projects/<slug>/publish/<ep>/` 下，用绝对路径）：
1. `xiaohongshu/package/*.mp4` —— platform-adapter 出过小红书包时首选；
2. 没有小红书包时回退 16:9 通用包：`bilibili/package/<ep>_*.mp4`（或 `youtube/package/`）；
3. 再没有才考虑母版 `edit/<ep>/<ep>_final*.mp4`（以 metadata.json `master.path` 口径为准）。

回退取用时在回执里注明所用文件与原因。视频与图片二选一，视频笔记不传图片。

封面图同理按优先级取：`publish/<ep>/<platform>/package/thumbnail.png` → `edit/<ep>/thumbnail_A.png`（H4 人工选定版）。有封面就传（`--cover`），没有才用平台默认首帧。

### 4) 准备标题 / 正文

```bash
mkdir -p /tmp/xhs-<ep>
printf '%s\n' '标题文本' > /tmp/xhs-<ep>/title.txt
# 正文写入 /tmp/xhs-<ep>/content.txt；最后一行放 #话题 标签（空格分隔），pipeline 会自动识别并勾选为话题
```

- 标题/正文素材来源：`publish/<ep>/seo.json`（有 xiaohongshu 条目且 `picked` 已定则逐字用 picked 版；没有 xiaohongshu 条目时，从 series_title/episode_title 与既有平台文案改写出小红书风格短标题+短正文，并在回执注明"非 picked、现场改写"）。
- **标题宽度 ≤38**（中文/中文标点=2，英文数字=1），超宽先裁剪再写文件。
- 话题标签从 seo.json tags 里挑 3–6 个,写成 `#时之门 #一千零一夜 ...` 放正文最后一行。

### 5) 填充视频笔记草稿（preview，不发布）

```bash
cd <skills>/XiaohongshuSkills
python3 scripts/publish_pipeline.py --reuse-existing-tab --preview \
  --title-file /tmp/xhs-<ep>/title.txt \
  --content-file /tmp/xhs-<ep>/content.txt \
  --video /abs/path/to/publish/<ep>/.../xxx.mp4 \
  --cover /abs/path/to/publish/<ep>/.../thumbnail.png
```

- 视频模式会自动点「上传视频」页签、上传文件并**等待平台转码处理完成**（成片约 10 分钟的视频要多等一会，不要提前中断）。
- `--cover` 在视频传完后自动走「修改封面」弹窗上传封面图并确认（仅视频模式支持；不传则用平台默认首帧）。
- `--preview` 只填表、勾话题，打印 `FILL_STATUS: READY_TO_PUBLISH` 后**不点发布**。

### 6) 截图给用户确认

```bash
python3 <skills>/skill-xhs-cdp-draft/scripts/cdp_screenshot.py /tmp/xhs-<ep>/preview_draft.png
```

把截图给用户核对标题 / 正文 / 话题 / 视频封面帧。

### 7) 最后一步：交还给用户

**到此为止，本 skill 的活干完了。**告知用户：草稿已填好、停在发布页，请在 Chrome 窗口里核对后**亲手点击「发布」**。
- **任何情况下都不要去掉 `--preview` 重跑、不要以任何方式代点发布按钮**——最后一步提交永远留给用户。
- 用户口头回复"确认/没问题"也不例外：请用户自己在浏览器里点。
- 用户点完发布后，把「所用视频路径、标题、正文、截图路径、状态 `submitted_by_human`」写入回执。

## 必做约束

- 永不自动点「发布」；最后提交由用户在浏览器完成（等价于该渠道的人工签字）。
- 视频笔记必须有视频；视频与图片二选一。
- 标题宽度 ≤38；正文末行话题标签会被自动勾选为话题。
- 一律用 `127.0.0.1:9222`，不要用 `localhost`（Chrome 对 `Host: localhost` 的 `/json/*` 返回 503）。
- 有风控/封号风险，优先测试号、小流量、人工复核。

## 已知坑与排查

- **CDP WebSocket 握手被拒**：Chrome 136+（本机 150）需 `--remote-allow-origins=*`。`launch_cdp_chrome.sh` 与已打补丁的 `chrome_launcher.py` 都带了；若手动起浏览器务必自己加（zsh 下 `*` 要引号）。
- **`websockets` 版本坑**：`cdp_publish.py` 用 `websockets.sync.client`。本机装的 14.x 正常；若升到 v16 出现 `InvalidMessage('did not receive a valid HTTP response')`，用 `scripts/_ws_compat.py` 垫片（websocket-client 实现，接口同名）：拷到 `XiaohongshuSkills/scripts/` 并把 `cdp_publish.py` 的 `import websockets.sync.client as ws_client` 换成 `import _ws_compat as ws_client`。
- **NOT LOGGED IN**：会话热身重跑一次；仍未登录 → 请用户在已打开的 Chrome 窗口扫码；确认用的是专用 profile（不是用户日常 profile，日常 Chrome 登录过不算数）。
- **视频上传/转码慢**：正常。`_wait_video_processing` 按「停滞时长」超时（默认 120s 无进度才报错,进度在走就一直等,可用环境变量 `XHS_VIDEO_PROCESS_TIMEOUT` 调）;127MB 成片在慢上行下传 10–20 分钟属常态,不要中断。真停滞超时可换更小的回退包重试。
- **pipeline 在上传完成后才挂掉（视频还在页面上）**：不要重跑整个 pipeline（会重传大文件）。先用 CDP 看页面是否已有「重新上传」（=视频已传完），是则直接连现有 tab 补填即可：
  ```python
  import sys; sys.path.insert(0, "scripts")
  from cdp_publish import XiaohongshuPublisher
  from publish_pipeline import _extract_topic_tags_from_last_line, _select_topics
  content, tags = _extract_topic_tags_from_last_line(open("content.txt").read().strip())
  p = XiaohongshuPublisher(); p.connect(reuse_existing_tab=True)
  p._fill_title(open("title.txt").read().strip()); p._fill_content(content); _select_topics(p, tags)
  p.upload_cover("/abs/path/thumbnail.png")  # 封面也可单独补传
  ```
- **`/json/list` 503** → 用 127.0.0.1 而非 localhost。
- **发布页结构异常** → 检查 `XiaohongshuSkills/scripts/cdp_publish.py` 的 `SELECTORS` 与视频上传等待逻辑（小红书改版高发区）。
