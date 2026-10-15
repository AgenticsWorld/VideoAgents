---
name: youtube-cdp-draft
description: |
  用「用户本机的 Google Chrome（专用自动化 profile + CDP 9222）」把一集成片视频 + 标题/简介/标签/缩略图填充成 YouTube Studio 的**上传草稿**，推进到「公开范围」页后停下，由用户人工选择可见性并点击「保存/发布」完成提交（本 skill 永不代点）。
  适用场景：收到「发布 <ep> 到 youtube」工单后，基于 data/projects/<slug>/publish/youtube/package/<ep>/ 的发布包（mp4 + thumbnail + srt）与 publish/seo.json、publish/metadata.json 的本集条目自动完成视频上传与 meta 信息填写、登录态检查、CDP 截图确认。
metadata:
  trigger: 发布某集成片到 YouTube（半自动，用户点最后一步保存/发布）
  builds_on: skills/skill-xhs-cdp-draft（共用 launch_cdp_chrome.sh 与专用 Chrome profile）
---

# youtube-cdp-draft — 用本机 Chrome 半自动上传 YouTube 视频草稿

把「一集成片视频 + 标题/简介/标签/缩略图」填进 studio.youtube.com 的上传对话框，推进到「公开范围」页停住，**由用户亲手点「保存」或「发布」**。
自动化脚本自包含：`scripts/ytstudio_draft.py`（直接走 CDP，不依赖 XiaohongshuSkills）。

## 浏览器策略（与 skill-xhs-cdp-draft 完全一致）

- 共用同一个 launcher 与专用 profile：`skill-xhs-cdp-draft/scripts/launch_cdp_chrome.sh`，
  profile = `~/Google/Chrome/XiaohongshuProfiles/default`（一个自动化 profile 装所有平台登录态）。
- Google 登录态持久化在该 profile：**首次由用户在弹出的 Chrome 窗口手动登录 Google/YouTube，之后免登**。
  > Google 有时会对自动化浏览器拦截登录（"此浏览器可能不安全"）。launcher 已带
  > `--disable-blink-features=AutomationControlled`，一般可正常登录;若仍被拦，
  > 让用户先在手机/日常浏览器确认账号活动，或换「使用手机登录」方式。
- 脚本挑 tab 的规则：优先复用已有 studio.youtube.com 标签页 → 空白页 → 新开标签页，
  **不会碰其它流程占用的标签页**（如小红书草稿页）。`check-login` / `upload` 复用前会探测该标签：
  开着上传对话框（上一集停在「公开范围」页等用户点保存;URL 带 `d=ud` 或页面有 `ytcp-uploads-dialog`）
  或 5 秒内不响应（如卡在「离开网站？」弹窗）的标签一律跳过、留给用户，改用空白页或新开标签。
  `screenshot` 不跳过（它要截的正是这种标签）。
- 每次运行都会打印 `TAB_ID: <id>`（本次附着的标签）;`--new-tab` 强制新开标签，`--tab-id <id>` 指定标签,
  两者互斥;选项写在子命令前后均可。脚本只断开连接、**从不关闭标签**（用户要在里面点保存）。

## 标准操作流程

> 下面命令默认在仓库根目录执行；`<skills>` = `agents/12-publishing/publisher/skills`。

### 1) 启动本机 Chrome（CDP 127.0.0.1:9222）

```bash
bash <skills>/skill-xhs-cdp-draft/scripts/launch_cdp_chrome.sh
# 已在 9222 跑着会直接复用、不重启。
```

### 2) 确认登录态（未登录 → 请用户手动登录）

```bash
python3 <skills>/skill-youtube-cdp-draft/scripts/ytstudio_draft.py check-login
```

- 判定标准：导航 studio.youtube.com 后 URL 未跳去 accounts.google.com。
- 未登录时**停下来，明确告知用户**「已打开 Chrome 窗口，请登录 Google/YouTube 账号」，等用户完成后重跑 `check-login` 直到 `Login confirmed`。
- 首次使用的频道若弹「创建频道」引导，也由用户在窗口里点完。

### 3) 选定成片视频、缩略图与字幕

都在 `data/projects/<slug>/publish/` 下（WORKFLOW §2 项目级布局：平台包在 `<platform>/package/<ep>/`；单集老项目可能直接放在 `<platform>/package/`，两处都找），用绝对路径：
- 视频：`youtube/package/<ep>/<slug>_<ep>_youtube.mp4`（文件名以 parts.json 为准）（platform-adapter 出的 YouTube 包；没有才回退母版 `edit/<ep>/<ep>_final*.mp4`，回执注明）。
- 缩略图：`youtube/package/<ep>/*cover*.jpg|thumbnail*.png`（回退 `edit/<ep>/thumbnail_A.png`）。频道未验证时缩略图会设不上，脚本按非致命告警跳过。
- 字幕：`youtube/package/<ep>/*.srt`（含时间戳），在 Video elements 步自动上传；前置条件是视频语言已设（`--language`,默认 zh-CN）。

### 4) 准备标题 / 简介 / 标签

```bash
mkdir -p /tmp/yt-<ep>
printf '%s\n' '标题文本' > /tmp/yt-<ep>/title.txt   # ≤100 字符
# 简介写入 /tmp/yt-<ep>/desc.txt（可多行,含话题 tag 行）
```

- 来源：`publish/seo.json` 的 items 中 `ep=<ep>` 且 `platform=youtube` 的条目——`picked` 已定则**逐字用 picked 版**；未定则停下来问用户挑哪条，或按工单指示选用并在回执注明"非 picked、代选 t-xxx"。
- 简介直接用 seo.json youtube `description`；标签用 `tags`（逗号分隔传给 `--tags`）。
- 元数据口径（made_for_kids 等）以 `publish/metadata.json` 中 episodes[] 的 `ep=<ep>` 条目为准（platform_fields.youtube 为平台字段）（`rating_made_for_kids: false` → 不加 `--made-for-kids`）。

### 5) 上传 + 填写（自动推进到「公开范围」页，不保存）

```bash
python3 <skills>/skill-youtube-cdp-draft/scripts/ytstudio_draft.py upload \
  --video /abs/path/publish/youtube/package/<ep>/<slug>_<ep>_youtube.mp4 \
  --title-file /tmp/yt-<ep>/title.txt \
  --desc-file /tmp/yt-<ep>/desc.txt \
  --thumbnail /abs/path/publish/youtube/package/<ep>/<slug>_<ep>_youtube_cover.jpg \
  --subtitles /abs/path/publish/youtube/package/<ep>/<slug>_<ep>_youtube.srt \
  --tags "时之门,一千零一夜,奇幻动画"
```

脚本行为：打开上传对话框 → 塞视频文件（上传在浏览器后台继续）→ 等详情表单出现 → 填标题/简介（execCommand 方式,Polymer 组件能收到真实输入事件）→ 选「非面向儿童」→ 展开"显示更多"设**视频语言**（`--language`,默认 zh-CN,语言菜单项按 `test-id`=BCP-47 码选取,跨语言稳定）并填标签 → 传缩略图 → 点「下一步」进 **Video elements** 页,`--subtitles` 给了就自动传字幕（点 Add subtitles → Upload file → With timing → Continue,原生文件选择框用 `Page.setInterceptFileChooserDialog` 拦截喂文件,**触发点击必须带 userGesture,否则 Chrome 直接无视**;完成后点 Done 关编辑器）→ 再点两次「下一步」推进到「公开范围」页 → **把可见性预选为 Private**（Studio 会记住上次选择,实测可能默认停在 Public,预选 Private 防误发;`--visibility` 可改,`none` 表示不动）→ 按停滞超时监控上传进度直到传完 → 打印 `FILL_STATUS: READY_FOR_HUMAN_SUBMIT` 与草稿链接。
- `--stop-at details` 可改为停在详情页（不点任何「下一步」）。
- **脚本从不点 #done-button（保存/发布）**。

### 6) 截图给用户确认

```bash
python3 <skills>/skill-youtube-cdp-draft/scripts/ytstudio_draft.py screenshot /tmp/yt-<ep>/preview_draft.png \
  --tab-id <upload 打印的 TAB_ID>
```

- 只开着一个 Studio 标签时可省略 `--tab-id`;同时开着多集草稿时必须带，否则可能截到别集（脚本会告警）。

### 多集连续上传（同一频道一次发多集时）

- 每集各占一个标签：每集 `upload` 加 `--new-tab`（upload 自身会校验登录;前一集若停在「公开范围」页，不加也会被自动跳过，加上更明确），
  记下每集打印的 `TAB_ID`,截图用 `screenshot --tab-id <该集 TAB_ID>`。
- 前一集等用户点保存期间，不要对它的标签再跑 `upload` / `check-login`（会导航走对话框或被「离开网站？」弹窗卡住）。
- 交还用户时逐集列出标签对应关系（集号 → 标题 → TAB_ID），请用户逐个标签核对后点「保存/发布」,保存后标签由用户自行关闭。

### 7) 最后一步：交还给用户

告知用户：视频已上传、meta 已填好、停在「公开范围」页（默认私享），请在 Chrome 窗口里核对后**亲手选择可见性并点「保存/发布」**。
- 任何情况下都不要代点保存/发布;用户口头说"确认"也不例外。
- 用户点完后，把「所用视频/缩略图路径、最终标题/简介、草稿链接、截图路径、状态 `submitted_by_human`」写入回执 `publish/receipts/`。

## 必做约束

- 永不点 `#done-button`；最后提交由用户在浏览器完成（等价于该渠道的人工签字）。
- 标题 ≤100 字符;「面向儿童」必选项由脚本按 metadata.json 口径设置,Next 才会解禁。
- 一律用 `127.0.0.1:9222`，不要用 `localhost`。
- 中断后**不要盲目重跑 upload**（会重传大文件、产生重复草稿）:先 `screenshot` 看页面状态,视频已在传的话,缺什么用 CDP 补什么;重复的废稿请用户在 Studio 内容页删除。

## 已知坑与排查

- **Google 拒绝在自动化浏览器登录**：见上「浏览器策略」;确保用 launcher 启动（带 `--disable-blink-features=AutomationControlled`），必要时换登录方式。
- **选择器**：脚本用的是 Studio 跨语言稳定的元素 id（`#title-textarea #textbox`、`#next-button`、`tp-yt-paper-radio-button[name=VIDEO_MADE_FOR_KIDS_NOT_MFK]` 等）,不依赖按钮文字;页面改版失效时先 `screenshot` + `Runtime.evaluate` 摸 DOM 再更新脚本。
- **上传进度监控**：按「停滞 180s」判卡（改 `UPLOAD_STALL_TIMEOUT`）;监控放弃不等于上传失败,浏览器里还在传,看窗口即可。
- **每日上传上限 / 版权检查**：Studio 侧限制,脚本无法绕过;检查(Checks)步骤可以在后台跑,不阻塞用户保存。
- **缩略图设置失败**：频道未通过验证(手机号)时没有自定义缩略图权限,脚本会告警跳过,让用户在页面里手动处理或先去验证频道。
- **字幕入口是灰的**：视频语言没设,Add subtitles 不解禁——确认 `--language` 生效（Details 页 Show more 里能看到已选语言）。
- **点了 Continue 没弹文件选择框、也没报错**：触发点击缺 user gesture,Chrome 静默忽略——脚本已用 `evaluate(..., user_gesture=True)`;手动调试时记得 `Runtime.evaluate` 带 `userGesture: true`。
- **字幕编辑器上残留"file type"弹窗**：脚本会自动点 Cancel 关掉再点 Done;字幕分段在编辑器里可见即已载入成功。
- **`Page.enable: no response within 30s` / 上一集对话框被导航走**：附着到了停在上传对话框的旧标签（旧版脚本只按 URL 复用）。现行脚本会自动跳过这类标签;仍遇到时改用 `--new-tab`。
- **Done 按钮没找到**：该按钮无稳定 id,按文本匹配（Done/完成）;Studio UI 是其它语言时把对应词加进 `add_subtitles` 的 names 列表。
