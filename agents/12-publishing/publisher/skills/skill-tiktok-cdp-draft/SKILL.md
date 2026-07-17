---
name: tiktok-cdp-draft
description: |
  用「用户本机的 Google Chrome（专用自动化 profile + CDP 9222）」把一集成片视频 + caption(标题行+简介+#标签)/封面填进 TikTok Studio（tiktok.com/tiktokstudio/upload）的发布表单，填完停住，由用户人工点击「Post/发布」完成提交（本 skill 永不代点）。
  适用场景：收到「发布 <ep> 到 tiktok」工单后，基于 data/projects/<slug>/publish/<ep>/ 的发布包自动完成视频上传与 meta 信息填写、登录态检查、CDP 截图确认。
metadata:
  trigger: 发布某集成片到 TikTok（半自动，用户点最后一步 Post）
  builds_on: skills/skill-douyin-cdp-draft（同构；共用 skill-xhs-cdp-draft 的 launch_cdp_chrome.sh 与专用 Chrome profile）
---

# tiktok-cdp-draft — 用本机 Chrome 半自动上传 TikTok 视频草稿

把「一集成片视频 + caption + 封面」填进 TikTok Studio 上传表单并停住，**由用户亲手点「Post」**。
自动化脚本自包含：`scripts/ttstudio_draft.py`。

## 浏览器策略（与其它平台技能完全一致）

- 共用 launcher 与专用 profile：`skill-xhs-cdp-draft/scripts/launch_cdp_chrome.sh`，
  profile = `~/Google/Chrome/XiaohongshuProfiles/default`。
- TikTok 登录态持久化在该 profile：**首次由用户在弹出的 Chrome 窗口手动登录（二维码/账号），之后免登**。
  TikTok 对自动化环境相对敏感，被拦时换「二维码登录」或先在手机 App 确认账号活动。
- 登录判定：导航 `tiktok.com/tiktokstudio/upload` 后 URL 未跳去 `/login`。
- 脚本挑 tab：优先复用已有 tiktok.com 标签页 → 空白页 → 新开标签页,不碰其它流程的标签页。

## DOM 要点（实测,UI 可能是中/英文,选择器不依赖界面语言）

- **没有独立标题字段**：常规视频只有 caption——标题放 caption 首行,#标签 内联写在末尾。
- 视频入口：`input[type=file]`（accept="video/*",上传页唯一）。
- **caption 编辑器是 DraftJS**：`.public-DraftEditor-content[contenteditable=true]`（库类名,不受 TikTok 自己的 class 混淆影响）。两个实测坑：
  1. **Cmd/Ctrl+A 键盘事件会被 Studio 吞掉**（选不了全），但浏览器级选区 API `execCommand('selectAll')` 有效——选区做好后 `Input.insertText` 会**替换选区**且是 DraftJS 认可的真实输入;
  2. **TikTok 会在编辑器出现后异步把视频文件名预填进 caption**——填完必须校验全文,不一致就重填一次（否则文件名会粘在最后一个 #标签 上,如 `#悬疑短剧ep01_bilibili`）。脚本 `fill_caption` 已内置"selectAll+insertText+校验+重试"。
- 封面：点「Edit cover/编辑封面」→ 编辑器内直接有图片输入（父级类名 `ImageUpload__uploadArea`,`input[accept*=image]`,**无需原生文件选择框**）→ 喂图后点右上「Save」（**精确匹配,别碰旁边的 Save draft**）。
- 上传完成信号：正文出现 `Uploaded（xx MB）`/上传成功;进度百分比会在多个阶段间跳动（上传/处理）,停滞判超时可正常处理。

## 标准操作流程

> `<skills>` = `agents/12-publishing/publisher/skills`。

### 1) 启动本机 Chrome（CDP 127.0.0.1:9222）

```bash
bash <skills>/skill-xhs-cdp-draft/scripts/launch_cdp_chrome.sh
```

### 2) 确认登录态（未登录 → 请用户手动登录）

```bash
python3 <skills>/skill-tiktok-cdp-draft/scripts/ttstudio_draft.py check-login
```

### 3) 选定素材

都在 `data/projects/<slug>/publish/<ep>/` 下，用绝对路径：
- 视频：`tiktok/package/*.mp4`（9:16 适配包）优先;没有则回退 16:9 通用包 `bilibili/package/<ep>_*.mp4` 或母版,回执注明。
- 封面：`tiktok/package/thumbnail*.png` → 回退 `bilibili/package/thumbnail.png` / `edit/<ep>/thumbnail_A.png`。

### 4) 准备 caption

```bash
mkdir -p /tmp/tt-<ep>
# caption.txt = 标题行 + 简介 + 末行内联 #标签（总长 ≤4000 字符）
```

来源：`publish/<ep>/seo.json` tiktok 条目（`picked` 已定则逐字用;未定则代选并在回执注明）,标题行 + description（其中已含 #标签 行）。

### 5) 上传 + 填写（填完即停，不点 Post）

```bash
python3 <skills>/skill-tiktok-cdp-draft/scripts/ttstudio_draft.py upload \
  --video /abs/path/publish/<ep>/.../xxx.mp4 \
  --caption-file /tmp/tt-<ep>/caption.txt \
  --cover /abs/path/publish/<ep>/.../thumbnail.png
```

脚本行为：进上传页塞视频（上传后台继续）→ 等 DraftJS 编辑器出现 → 填 caption（含校验重试）→ Edit cover 弹窗传封面并 Save → 按停滞超时等 `Uploaded` → 打印 `FILL_STATUS: READY_FOR_HUMAN_SUBMIT`。
**脚本从不点「Post/发布」。**

### 6) 截图给用户确认

```bash
python3 <skills>/skill-tiktok-cdp-draft/scripts/ttstudio_draft.py screenshot /tmp/tt-<ep>/preview_draft.png
```

### 7) 最后一步：交还给用户

告知用户：视频/封面/caption 已就位，请在 Chrome 窗口里核对（When to post 默认 Now,可改 Schedule;Location 会自动带地理位置,不要就删掉）后**亲手点「Post」**。
- 任何情况下都不要代点 Post;用户口头说"确认"也不例外。
- 用户点完后，把「所用视频/封面路径、最终 caption、截图路径、状态 `submitted_by_human`」写入回执 `publish/receipts/`。

## 必做约束

- 永不点「Post/发布」「Discard」;最后提交由用户完成（等价于该渠道的人工签字）。
- caption ≤4000 字符,#标签 内联。
- 一律用 `127.0.0.1:9222`，不要用 `localhost`。
- 中断后不要盲目重跑 upload（会重传大文件）:先 `screenshot` 看状态,缺什么用 fill_caption/upload_cover 单独补。
- TikTok 对自动化敏感:优先测试号、小流量、人工复核;不要高频操作。

## 已知坑与排查

- **requests 走系统代理 503** → 脚本已 `trust_env=False` 直连。
- **caption 末尾多出文件名 / 内容翻倍** → 就是"文件名异步预填 + Cmd+A 被吞"两个坑,确认用的是最新 `fill_caption`（selectAll 选区 + insertText 覆盖 + 校验重试）。
- **封面 Save 点错** → 必须精确匹配按钮文本 `Save`,页面上同时有 `Save draft`。
- **上传进度乱跳** → 正常（上传/处理多阶段）,完成只认 `Uploaded`。
- **登录被风控拦** → 换二维码登录 / 手机 App 确认;确保用 launcher 启动（带 `--disable-blink-features=AutomationControlled`）。
- **页面改版选择器失效** → 先 `screenshot` + `Runtime.evaluate` 摸 DOM 再更新脚本;caption 编辑器认准 DraftJS 库类名。
