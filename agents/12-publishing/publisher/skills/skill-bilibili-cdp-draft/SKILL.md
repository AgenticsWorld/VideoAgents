---
name: bilibili-cdp-draft
description: |
  用「用户本机的 Google Chrome（专用自动化 profile + CDP 9222）」把一集成片视频 + 标题/简介/标签/封面填进 B 站创作中心（member.bilibili.com/platform/upload/video/）的投稿表单，填完停住，由用户人工点击「立即投稿」完成提交（本 skill 永不代点）。
  适用场景：收到「发布 <ep> 到 bilibili」工单后，基于 data/projects/<slug>/publish/<ep>/ 的发布包自动完成视频上传与 meta 信息填写、登录态检查、CDP 截图确认。
metadata:
  trigger: 发布某集成片到 bilibili（半自动，用户点最后一步立即投稿）
  builds_on: skills/skill-xhs-cdp-draft（共用 launch_cdp_chrome.sh 与专用 Chrome profile）
---

# bilibili-cdp-draft — 用本机 Chrome 半自动上传 B 站投稿草稿

把「一集成片视频 + 标题/简介/标签/封面」填进 member.bilibili.com 的投稿表单并停住，**由用户亲手点「立即投稿」**。
自动化脚本自包含：`scripts/blbl_draft.py`（直接走 CDP，不依赖 XiaohongshuSkills）。

## 浏览器策略（与 skill-xhs-cdp-draft 完全一致）

- 共用同一个 launcher 与专用 profile：`skill-xhs-cdp-draft/scripts/launch_cdp_chrome.sh`，
  profile = `~/Google/Chrome/XiaohongshuProfiles/default`（一个自动化 profile 装所有平台登录态）。
- B 站登录态持久化在该 profile：**首次由用户在弹出的 Chrome 窗口用哔哩哔哩 App 扫码登录，之后免登**。
- 脚本挑 tab 的规则：优先复用已有 member.bilibili.com 标签页 → 空白页 → 新开标签页，不碰其它流程的标签页。
- 登录判定：URL 停留在 `member.bilibili.com`（未登录会被 302 到 `passport.bilibili.com/login`）。

## DOM 要点（创作中心是 class 混淆的前端应用，无稳定 id）

选择器一律用**语义锚点**：
- 视频入口：上传页的 `input[type=file]`（进入页面后唯一的文件输入），塞入后上传在浏览器后台继续，可先填表单。
- 标题：`input[placeholder*=标题]` 或 `maxLength===80` 的可见 input。**上传开始后 B 站会用视频文件名回填标题**（和 TikTok 同一个坑），所以脚本填完会校验一次、不一致就重填；用原生 value setter + `input` 事件（受控组件，直接赋值会被吞）。
- 简介：优先找 placeholder 含「简介/描述」的可见 `textarea`（原生 setter + input 事件）；改版成 contenteditable 时回退「focus + 全选 + `Input.insertText`」键盘级注入。
- 标签：placeholder 含「标签/回车/Enter」的可见 input，`Input.insertText` 逐个输入 + 回车确认（B 站标签是 chip 式，**不能**像抖音那样内联写在简介里）。
- 封面：B 站会自动截帧生成默认封面。自定义封面点「更改封面」（按元素**自有文本节点**精确匹配 `更改封面/上传封面/编辑封面`，避免命中外层容器）→ 弹窗内 `input[type=file][accept*=image]` 直接 setFileInputFiles；找不到 input 时用 `Page.setInterceptFileChooserDialog` 拦截原生选择框兜底 → 循环点「完成/确定/确认/保存」直到弹窗关闭。
- 上传完成信号：页面出现「上传完成/上传成功」**且全文无 `\d+%`**。百分比还在变说明仍在传，脚本按「进度停滞 180s」判超时。

## 标准操作流程

> 下面命令默认在仓库根目录执行；`<skills>` = `agents/12-publishing/publisher/skills`。

### 1) 启动本机 Chrome（CDP 127.0.0.1:9222）

```bash
bash <skills>/skill-xhs-cdp-draft/scripts/launch_cdp_chrome.sh
```

### 2) 确认登录态（未登录 → 请用户扫码）

```bash
python3 <skills>/skill-bilibili-cdp-draft/scripts/blbl_draft.py check-login
```

未登录时**停下来，明确告知用户**「已打开 Chrome 窗口，请用哔哩哔哩 App 扫码登录 member.bilibili.com」，完成后重跑直到 `Login confirmed`。

### 3) 选定素材

都在 `data/projects/<slug>/publish/<ep>/` 下，用绝对路径：
- 视频：`bilibili/package/<ep>_bilibili.mp4`（16:9 平台包）优先；没有则回退母版 `edit/<ep>/<ep>_final.mp4`，回执注明。
- 封面：`bilibili/package/thumbnail.png` → 回退 `edit/<ep>/thumbnail_A.png`。B 站封面裁 16:10 展示，16:9 图会被上下轻裁，无需预处理。

### 4) 准备标题 / 简介 / 标签

```bash
mkdir -p /tmp/blbl-<ep>
printf '%s\n' '标题文本' > /tmp/blbl-<ep>/title.txt   # ≤80 字
# 简介写入 /tmp/blbl-<ep>/desc.txt（≤2000 字）；标签不进简介，走 --tags
```

来源：`publish/<ep>/seo.json` bilibili 条目（`picked` 已定则逐字用；未定则代选并在回执注明）；tags 逐字取 seo.json 的 tags（≤10 个）。

### 5) 上传 + 填写（填完即停，不点投稿）

```bash
python3 <skills>/skill-bilibili-cdp-draft/scripts/blbl_draft.py upload \
  --video /abs/path/publish/<ep>/bilibili/package/<ep>_bilibili.mp4 \
  --title-file /tmp/blbl-<ep>/title.txt \
  --desc-file /tmp/blbl-<ep>/desc.txt \
  --tags "标签1,标签2,标签3" \
  --cover /abs/path/publish/<ep>/bilibili/package/thumbnail.png
```

脚本行为：进上传页塞视频（上传在浏览器后台继续）→ 等编辑表单出现 → 填标题（校验文件名回填并重填）→ 简介 → 逐个回车加标签 → 走「更改封面」弹窗传封面 → 按停滞超时等上传完成 → 打印 `FILL_STATUS: READY_FOR_HUMAN_SUBMIT`。
**脚本从不点「立即投稿」（也不点「存草稿」）。**

### 6) 截图给用户确认

```bash
python3 <skills>/skill-bilibili-cdp-draft/scripts/blbl_draft.py screenshot /tmp/blbl-<ep>/preview_draft.png
```

### 7) 最后一步：交还给用户

告知用户：视频/封面/文案/标签已就位，请在 Chrome 窗口里核对（**创作声明**（必选下拉，脚本不代填）、**分区**（B 站会自动猜一个，可能不对）、参加的活动、合集、定时发布等字段）后**亲手点「立即投稿」**。
- 任何情况下都不要代点投稿；用户口头说"确认"也不例外。
- 用户点完后，把「所用视频/封面路径、最终标题/简介/标签、截图路径、状态 `submitted_by_human`」写入回执 `publish/receipts/`。

## 必做约束

- 永不点「立即投稿」/「存草稿」；最后提交由用户在浏览器完成（等价于该渠道的人工签字）。
- 标题 ≤80 字；简介 ≤2000 字；标签 ≤10 个、chip 式逐个回车，不内联进简介。
- 创作声明/分区等合规字段不代填，留给用户核对（声明选错会被打回）。
- B 站会**自动追加**内容识别标签（实测：传 2 个标签后页面变成 5 个），自动标签也占 10 个上限，`--tags` 传 4–6 个即可。
- 一律用 `127.0.0.1:9222`，不要用 `localhost`。
- 中断后不要盲目重跑 upload（会重传大文件）：先 `screenshot` 看状态，视频已在传就只补缺的字段（fill_title/fill_desc/add_tags/upload_cover 都可从 python 里单独调）。

## 已知坑与排查

- **requests 走系统代理 503** → 脚本已 `trust_env=False` 直连。
- **标题被视频文件名覆盖** → B 站在上传开始后用文件名回填标题；脚本填后 2s 校验一次并重填，仍不一致会打 WARNING，让用户在浏览器里改。
- **标题/简介填不进** → 必须用原生 value setter + input 事件（受控组件，直接赋值会被吞）。
- **封面点击命中外层容器** → 「更改封面」按钮要按元素自有文本节点精确匹配；整段 innerText 匹配会点到包裹容器上导致无响应。
- **误判上传完成** → 只认「上传完成/上传成功 且全文无百分比」；百分比在变要滚动续期，不要用固定总超时（大文件必超）。
- **页面改版选择器失效** → 先 `screenshot` + `Runtime.evaluate` 摸 DOM（本文件"DOM 要点"一节的探测思路可复用），再更新脚本。
