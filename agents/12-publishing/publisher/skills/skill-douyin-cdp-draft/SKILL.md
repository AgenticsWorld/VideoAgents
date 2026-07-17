---
name: douyin-cdp-draft
description: |
  用「用户本机的 Google Chrome（专用自动化 profile + CDP 9222）」把一集成片视频 + 标题/简介(含#话题)/封面填进抖音创作者中心（creator.douyin.com）的发布表单，填完停住，由用户人工点击「发布」完成提交（本 skill 永不代点）。
  适用场景：收到「发布 <ep> 到抖音」工单后，基于 data/projects/<slug>/publish/<ep>/ 的发布包自动完成视频上传与 meta 信息填写、登录态检查、CDP 截图确认。
metadata:
  trigger: 发布某集成片到抖音（半自动，用户点最后一步发布）
  builds_on: skills/skill-xhs-cdp-draft（共用 launch_cdp_chrome.sh 与专用 Chrome profile）
---

# douyin-cdp-draft — 用本机 Chrome 半自动上传抖音视频草稿

把「一集成片视频 + 标题/简介/封面」填进 creator.douyin.com 的发布表单并停住，**由用户亲手点「发布」**。
自动化脚本自包含：`scripts/dycreator_draft.py`（直接走 CDP，不依赖 XiaohongshuSkills）。

## 浏览器策略（与 skill-xhs-cdp-draft 完全一致）

- 共用同一个 launcher 与专用 profile：`skill-xhs-cdp-draft/scripts/launch_cdp_chrome.sh`，
  profile = `~/Google/Chrome/XiaohongshuProfiles/default`（一个自动化 profile 装所有平台登录态）。
- 抖音登录态持久化在该 profile：**首次由用户在弹出的 Chrome 窗口用抖音 App 扫码登录，之后免登**。
- 脚本挑 tab 的规则：优先复用已有 creator.douyin.com 标签页 → 空白页 → 新开标签页，不碰其它流程的标签页。
- 登录判定：URL 是否进入 `creator.douyin.com/creator-micro/…`（未登录停在营销落地页）。

## DOM 要点（创作者中心是 class 混淆的 React 应用,无稳定 id）

选择器一律用**语义锚点**,这些是实测可用的：
- 视频入口：`input[type=file][accept*=video]`（上传页唯一视频输入）。
- 标题：`input[placeholder*=标题]`（"填写作品标题，为作品获得更多流量"），用原生 value setter + `input` 事件（React 受控组件）。
- 简介：`.editor-kit-container[contenteditable=true]`（data-placeholder"添加作品简介"）。**用键盘级 CDP Input 事件**（全选+Backspace+`Input.insertText`）——execCommand selectAll/insertText 在这个 editor-kit 里会出现部分替换、旧文本残留（实测踩过）;#话题 直接写在简介文本里即可,无需点「#添加话题」走联想选择。
- 封面：点「选择封面」文本 → 弹窗内先点掉「我知道了」引导 → 切「设置横封面」（16:9 母版）→ 可见的「上传封面」磁贴本身不是 input,真正的 semi-design 文件输入在其祖先容器里（脚本向上爬 8 层找 `input[accept*=image]` 打标后 setFileInputFiles,**不走原生文件选择框**）→ 连点「完成」直到弹窗关闭（第一个「完成」可能属于"发文助手"提示,所以要循环）。
- 上传完成信号：页面出现「重新上传」且全文无 `\d+%`。注意「上传中，请勿关闭页面」是**常驻横幅**,不是进度状态,不要当完成/进行判据。

## 标准操作流程

> 下面命令默认在仓库根目录执行；`<skills>` = `agents/12-publishing/publisher/skills`。

### 1) 启动本机 Chrome（CDP 127.0.0.1:9222）

```bash
bash <skills>/skill-xhs-cdp-draft/scripts/launch_cdp_chrome.sh
```

### 2) 确认登录态（未登录 → 请用户扫码）

```bash
python3 <skills>/skill-douyin-cdp-draft/scripts/dycreator_draft.py check-login
```

未登录时**停下来，明确告知用户**「已打开 Chrome 窗口，请用抖音 App 扫码登录创作者中心」，完成后重跑直到 `Login confirmed`。

### 3) 选定素材

都在 `data/projects/<slug>/publish/<ep>/` 下，用绝对路径：
- 视频：`douyin/package/*.mp4`（9:16 适配包）优先;没有则回退 16:9 通用包 `bilibili/package/<ep>_*.mp4`（抖音支持横屏,>40s 建议横版）或母版,回执注明。
- 封面：`douyin/package/thumbnail*.png` → 回退 `bilibili/package/thumbnail.png` / `edit/<ep>/thumbnail_A.png`。16:9 封面走「横封面」裁 4:3;竖封面(3:4)脚本不设,留用户用 AI 推荐或手动补。

### 4) 准备标题 / 简介

```bash
mkdir -p /tmp/dy-<ep>
printf '%s\n' '标题文本' > /tmp/dy-<ep>/title.txt   # ≤30 字
# 简介写入 /tmp/dy-<ep>/desc.txt,#话题 标签直接内联写在文本末尾（≤1000 字）
```

来源：`publish/<ep>/seo.json` douyin 条目（`picked` 已定则逐字用;未定则代选并在回执注明）;话题用 tags 里挑 4–6 个内联进简介。

### 5) 上传 + 填写（填完即停，不点发布）

```bash
python3 <skills>/skill-douyin-cdp-draft/scripts/dycreator_draft.py upload \
  --video /abs/path/publish/<ep>/.../xxx.mp4 \
  --title-file /tmp/dy-<ep>/title.txt \
  --desc-file /tmp/dy-<ep>/desc.txt \
  --cover /abs/path/publish/<ep>/.../thumbnail.png
```

脚本行为：进上传页塞视频（上传在浏览器后台继续）→ 等编辑表单出现 → 填标题/简介 → 走封面弹窗传横封面 → 按停滞超时等上传完成 → 打印 `FILL_STATUS: READY_FOR_HUMAN_SUBMIT`。
**脚本从不点「发布」按钮。**

### 6) 截图给用户确认

```bash
python3 <skills>/skill-douyin-cdp-draft/scripts/dycreator_draft.py screenshot /tmp/dy-<ep>/preview_draft.png
```

### 7) 最后一步：交还给用户

告知用户：视频/封面/文案已就位，请在 Chrome 窗口里核对（含"发文助手"的检测结果、可选的合集/位置/热点/定时发布等字段）后**亲手点「发布」**。
- 任何情况下都不要代点发布;用户口头说"确认"也不例外。
- 用户点完后，把「所用视频/封面路径、最终标题/简介、截图路径、状态 `submitted_by_human`」写入回执 `publish/receipts/`。

## 必做约束

- 永不点「发布」;最后提交由用户在浏览器完成（等价于该渠道的人工签字）。
- 标题 ≤30 字;简介 ≤1000 字,#话题 内联。
- 一律用 `127.0.0.1:9222`，不要用 `localhost`。
- 中断后不要盲目重跑 upload（会重传大文件）:先 `screenshot` 看状态,视频已在传就只补缺的字段（fill_title/fill_desc/upload_cover 都可从 python 里单独调）。
- 抖音风控较严:优先测试号、小流量、人工复核;不要高频操作。

## 已知坑与排查

- **requests 走系统代理 503** → 脚本已 `trust_env=False` 直连。
- **标题填不进** → 必须用原生 value setter + input 事件（React 受控组件,直接赋值会被吞）。
- **封面弹窗层层引导** → 「我知道了」「发文助手的完成」都要先点掉;脚本按钮循环已覆盖,新增引导弹窗失效时先截图看挡着什么。
- **误判上传完成** → 记住「上传中，请勿关闭页面」是常驻文案;只认「重新上传 且无百分比」。
- **页面改版选择器失效** → 先 `screenshot` + `Runtime.evaluate` 摸 DOM（本文件"DOM 要点"一节的探测思路可复用）,再更新脚本。
