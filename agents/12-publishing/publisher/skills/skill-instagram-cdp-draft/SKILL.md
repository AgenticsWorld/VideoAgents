---
name: instagram-cdp-draft
description: |
  用「用户本机的 Google Chrome（专用自动化 profile + CDP 9222）」把一集成片视频 + caption（标题行+简介+#标签）/封面填进 instagram.com 网页版「新帖子」对话框（网页版发出的视频一律是 Reels），填到最后一步停住，由用户人工点击「Share / 分享」完成提交（本 skill 永不代点）。
  适用场景：收到「发布 <ep> 到 instagram」工单后，基于 data/projects/<slug>/publish/instagram/package/<ep>/ 的发布包（seo/metadata 取 publish/seo.json、publish/metadata.json 的本集条目）自动完成登录态检查、选视频、裁切取原比例、传封面、填 caption、按工单开 AI 标签、CDP 截图确认。
metadata:
  trigger: 发布某集成片到 Instagram（半自动，用户点最后一步 Share）
  builds_on: skills/skill-tiktok-cdp-draft（同构；共用 skill-xhs-cdp-draft 的 launch_cdp_chrome.sh 与专用 Chrome profile）
---

# instagram-cdp-draft — 用本机 Chrome 半自动填 Instagram Reel

把「一集成片视频 + caption + 封面」填进 instagram.com 的「新帖子」对话框，停在最后一步，**由用户亲手点「Share / 分享」**。
自动化脚本自包含：`scripts/igweb_draft.py`。

## 浏览器策略（与其它平台技能完全一致）

- 共用 launcher 与专用 profile：`skill-xhs-cdp-draft/scripts/launch_cdp_chrome.sh`，
  profile = `~/Google/Chrome/XiaohongshuProfiles/default`。
- Instagram 登录态持久化在该 profile：**首次由用户在弹出的 Chrome 窗口手动登录（含两步验证），之后免登**。
  首次登录常要短信 / 备用码验证，勾「Trust this device」可免下次再验。
- 登录判定：打开 instagram.com 后不在 `/accounts/login`、两步验证页，且侧栏有私信入口。
- 脚本挑 tab：优先复用已有 instagram.com 标签页——**但已打开「新帖子」对话框的标签页一律不碰**（见下「没有草稿」）→ 空白页 → 新开标签页。

## 网页版的硬约束（2026-11-01 实测）

- **视频一律发成 Reels**：选完视频会弹「Video posts are now shared as reels」，脚本自动点掉。
- **没有草稿**：关掉对话框或离开页面，填好的内容全部丢失（「Discard post? If you leave, your edits won't be saved.」）。
- **点 Share 之后才开始上传视频**：用户点完 Share 后，标签页必须开着，直到 Instagram 提示已分享（Reel shared）；中途关窗口 = 没发出去。
- **一个 Reel 一个标签页**：上一条还停在 Share 前时，下一条必须 `--new-tab`；脚本在导航前会先检查，标签页里有对话框就拒绝（退出码 1），不会把它冲掉。
- **裁切默认 1:1**：不处理的话 9:16 视频会被裁成正方形。脚本选「Original」（裁切菜单里唯一不是「宽:高」的那一项），保持视频原比例。
- **封面**：「Edit」页「Cover photo → Select from computer」可传自定义封面，只收 JPG / PNG。
- **caption ≤ 2,200 字符**；**#标签每条最多 5 个**（2025 年底起；页面在 Share 前不提示，脚本自己把多出的去掉并打印 `HASHTAGS_TRIMMED`）。没有独立标题栏，标题即 caption 首行。
- **不能传字幕文件**：字幕只能烧在画面里。
- **「Add AI label」开关**：Instagram 要求写实的 AI 生成照片 / 视频打标签；`--ai-label` 打开它（何时加见第 4 步）。
- 「Share to Facebook」「Schedule content」两个开关脚本不碰，保持默认（关）。

## DOM 要点（选择器不依赖界面语言）

- 「新帖子」入口：侧栏里图标为加号形状（path 以 `M21 11h-8V3` 开头）的链接；找不到时退回已知的 aria-label 名单；再找不到，脚本请用户在窗口里手动点「New post / 新帖子」，等 90 秒。
- 视频入口：对话框里的 `input[type=file]`（accept 含 `video/mp4`）。
- 「Next」：对话框标题栏右上角的纯文字按钮；**到 caption 步时这个位置是 Share，脚本在这一步永不点它**（`click_next` 见到 caption 编辑器即拒绝）。
- 裁切按钮是 `div[role=button]` 包着的 `<button>`，只有内层 `<button>` 响应点击。
- 封面：`input[type=file][accept*=image/jpeg]`（只在 Edit 步出现，也是判断进入 Edit 步的依据）。
- caption 编辑器是 Lexical（`[data-lexical-editor=true]`，role=textbox）：`execCommand('selectAll')` + CDP `Input.insertText`，换行与 #标签 都正常；输入 #标签 会弹联想列表，填完 blur 即关闭、不会选中联想项。

## 标准操作流程

> `<skills>` = `agents/12-publishing/publisher/skills`。

### 1) 启动本机 Chrome（CDP 127.0.0.1:9222）

```bash
bash <skills>/skill-xhs-cdp-draft/scripts/launch_cdp_chrome.sh
```

### 2) 确认登录态（未登录 → 请用户手动登录）

```bash
python3 <skills>/skill-instagram-cdp-draft/scripts/igweb_draft.py check-login
```

### 3) 选定素材

都在 `data/projects/<slug>/publish/` 下（WORKFLOW §2 项目级布局：平台包在 `<platform>/package/<ep>/`；单集老项目可能直接放在 `<platform>/package/`，两处都找），用绝对路径：
- 视频：`instagram/package/<ep>/*.mp4`（9:16 适配包，条数以 parts.json 为准）优先；没有则依次借其它 9:16 包 `tiktok/` → `youtube_shorts/` → `douyin/package/<ep>/`，再没有用母版 `edit/<ep>/`（花字版 `final_caption.mp4` > 烧录版 `final_sub.mp4` > `final.mp4`），回执注明借用来源。**网页版不能传字幕文件**：同一来源有烧录字幕版就用它；只有无字幕版本时照传，回执注明「无烧录字幕」。横屏视频照传（Reels 上下带黑边），不自行裁切，回执注明。
- 封面（JPG / PNG，9:16 最好）：工单写了默认封面就用它（`publish/defaults.json` 选定的，画幅不是竖版时退回下面的顺序并在回执注明）→ `instagram/package/<ep>/` 里的封面 → `edit/<ep>/thumbnail_instagram_*` → 其它 9:16 平台封面（`thumbnail_tiktok_*` / `thumbnail_douyin_*` / `thumbnail_xiaohongshu_*`）→ `edit/<ep>/thumbnail_*.png`。WebP 等格式先转 PNG：`sips -s format png in.webp --out /tmp/ig-<ep>/cover.png`（或 ffmpeg）。都没有就不传，由 Instagram 取首帧。主页网格按 3:4 居中裁切显示封面。

### 4) 准备 caption 与 AI 标签

```bash
mkdir -p /tmp/ig-<ep>
# caption.txt = 标题行 + 简介 + 末行 #标签（≤ 2,200 字符，#标签 ≤ 5 个）
```

- 来源：`publish/seo.json` 的 items 中 `ep=<ep>` 且 `platform=instagram` 的条目（`picked` 已定则逐字用；未定则代选并在回执注明）；没有 instagram 条目时借 `platform=tiktok` 的条目并在回执注明「借用 tiktok 文案」。标题行 + description（其中已含 #标签）。#标签超过 5 个时保留前 5 个（脚本也会兜底去掉），回执注明去掉了哪些。
- **AI 标签（`--ai-label`）**：工单写了加 / 不加时照工单。工单没写时：项目类型为影视剧、数字人、音频配画（画面由视频 / 图像生成模型产出）时加；动效短片（代码渲染）、混剪（素材库实拍）不加。

### 5) 上传 + 填写（填完即停，不点 Share）

```bash
python3 <skills>/skill-instagram-cdp-draft/scripts/igweb_draft.py upload \
  --video /abs/path/publish/instagram/package/<ep>/xxx.mp4 \
  --caption-file /tmp/ig-<ep>/caption.txt \
  --cover /abs/path/cover.jpg \
  [--ai-label]
```

脚本行为：先 ffprobe 打印 `VIDEO_CHECK: <宽>x<高> <秒>s`（横屏、超过 3 分钟只告警）→ 回首页确认登录 → 点「新帖子」→ 塞视频 → 点掉 Reels 提示 → 裁切选 Original → Next → 传封面 → Next → 填 caption（校验、重试一次）→ 按需开 AI 标签 → 打印 `TAB_ID` 与 `FILL_STATUS: READY_FOR_HUMAN_SUBMIT`。约 20 秒。
**脚本从不点「Share / 分享」。**

- **多条**（parts.json 有多条）：每条各发一个 Reel，每条 `upload` 加 `--new-tab`，记下 part → caption 首行 → TAB_ID；caption 按 seo.json 的 part 取。
- 退出码 2：caption 超过 2,200 字符（什么都没做，缩短后重跑）；退出码 1：未登录，或目标标签页里已有对话框（改 `--new-tab`）。

### 6) 截图给用户确认

```bash
python3 <skills>/skill-instagram-cdp-draft/scripts/igweb_draft.py screenshot /tmp/ig-<ep>/preview_draft.png --tab-id <TAB_ID>
```

### 7) 最后一步：交还给用户

告知用户：视频 / 封面 / caption 已就位，请在 Chrome 窗口里核对（裁切是否为原比例、封面、AI 标签、要不要加位置 / 合作者）后**亲手点「Share」**，**点完别关窗口，等 Instagram 提示已分享**（视频在点 Share 之后才上传）。多条时逐个标签页点。
- 任何情况下都不要代点 Share；用户口头说"确认"也不例外。
- 用户确认已分享后，把「所用视频 / 封面路径、最终 caption、截图路径、状态 `submitted_by_human`」写入回执 `publish/receipts/<ep>_instagram_receipt.json`（`platform` 写 `instagram`）；用户还没点则记 `draft_ready_pending_human`。附 `VIDEO_CHECK` / `HASHTAGS_TRIMMED` 输出与 AI 标签是否打开。

## 必做约束

- 永不点「Share / 分享」「Discard / 放弃」；最后提交由用户完成（等价于该渠道的人工签字）。
- 不关、不导航已有对话框的标签页（网页版没有草稿，会把用户待分享的 Reel 冲掉）。
- 一律用 `127.0.0.1:9222`，不要用 `localhost`。
- 中断后先 `screenshot --tab-id` 看状态再决定；重跑 `upload` 用 `--new-tab`，旧标签页留给用户处理。
- Instagram 对自动化敏感：不要高频操作；登录被拦时请用户在窗口里完成验证。

## 已知坑与排查

- **requests 走系统代理 503** → 脚本已 `trust_env=False` 直连。
- **预览是正方形** → 裁切没切到 Original（脚本会打印 WARNING）；请用户在 Crop 步左下角裁切按钮里选 Original，或返回重来。
- **点不开「新帖子」** → 侧栏改版或界面语言不在名单里；脚本会请用户手动点，之后继续。
- **后台标签页很慢** → Chrome 会节流后台标签页；`--new-tab` 会把新标签页切到前台，多条时一条一条跑。
- **页面改版选择器失效** → 先 `screenshot` + `Runtime.evaluate` 摸 DOM 再更新脚本；caption 编辑器认准 `data-lexical-editor`。
