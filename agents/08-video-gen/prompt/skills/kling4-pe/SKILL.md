---
name: kling4-pe
description: Use when writing or optimizing a group-level video prompt for Kling 4.0 hosted on Fal (kling/v4 text-to-video, image-to-video first/last frame, reference-to-video with images and videos). Applies Kling's official prompt structure (asset roles, overall settings, time-stamped sequence of events, audio), its descriptive-language and minimal-negative rules, and the Fal endpoint limits.
metadata:
  skill_version: 0.1.0
  owner: videoagents
  source: "Kling 4.0 User Guide (kling.ai/quickstart/kling-4-model-user-guide, 2026-10-14) + Fal kling/v4 OpenAPI (fetched 2026-10-26)"
  tags:
    - kling
    - prompt-optimization
    - multimodal-video
  supported_runtimes: []
  required_capabilities:
    filesystem_read: false
    filesystem_write: false
    tool_use: false
    network: false
    binary_outputs: false
  io_contract:
    output_kind: text
    primary_outputs:
      - optimized_prompt
  exports: []
---

# Kling 4.0 提示词优化(kling4-pe)

## 来源与适用

- 依据:可灵官方《Kling 4.0 User Guide》(kling.ai,2026-10-14 发布),以及 Fal 托管端点 `kling/v4/text-to-video`、`kling/v4/image-to-video`、`kling/v4/reference-to-video` 的 OpenAPI 参数表(2026-10-26 拉取)。
- 本文件**不是可灵官方发布的 skill**:「二、三」两节是官方手册要点的中文整理(英文原句照抄的地方用引号标出),「一」是 Fal 端点实测参数,「四」是本仓库的落地对照。
- 适用:生效视频模型为 Fal 托管的 Kling 4.0(模型 ID `kling/v4`,genmedia 家族 `kling4`)。
- 不适用:Kling 3.0 / O1 / O3(Fal 上只有首尾帧,不收参考素材);RunningHub 的 `kling-v4/*` 端点(参考写法与上限按 RH 模型目录,未核实)。

## 一、端点能力与硬限(Fal kling/v4)

| 项目 | 限制 | 说明 |
|---|---|---|
| 生成方式 | 文生 / 首尾帧 / 参考生 | genmedia 按输入自动选:无素材 = 文生;`--first-frame`(可加 `--last-frame`)= 首尾帧;`--ref` / `--video-ref` = 参考生。首帧与参考素材互斥 |
| 时长 | 3–30 秒,整数 | 由 genmedia `--duration` 传,不写进正文 |
| 分辨率 | 720p / 1080p | 4K 只在可灵官网有,Fal 没有 |
| 画幅 | 文生 16:9 / 9:16 / 1:1 / 21:9;参考生另有 auto;首尾帧随首帧 | 由命令行参数传 |
| 参考图 | ≤10 张;单张 ≤30MB、宽高 ≥300px、宽高比 0.4–2.5 | 首帧也占参考图名额 |
| 参考视频 | ≤5 段;每段 3–30 秒,合计 ≤30 秒;宽高都 ≥700px;23.9–60fps;宽高比 0.4–2.5 | 白模 camera.mp4(960×540)由 genmedia 提交前自动放大到 1280×720;不足 3 秒的尾段视频交不上去 |
| 参考音频 | **不收** | Fal 端点没有音频参考入口(官网的「音色」要先建 Element,本仓库未接),audio_refs 必须为空 |
| 负面词 / seed / 音频开关 | 都没有 | 音画同出默认开启,官方 FAQ:Audio-visual synchronization and Smart Storyboard are enabled by default and have no separate toggles |
| 提示词长度 | 官方 ≤8,000 token;Fal 字段 ≤26,000 字符 | 不设固定字数,以装下必需内容为准 |
| 多镜头 | 默认开 | 官方:Multi-shot is enabled by default, allowing the model to automatically split shots and organize pacing based on the content。要一镜到底必须明写 |

## 二、官方写法原则(手册 2.2 与 FAQ)

1. **描述具体可见的东西,少用抽象词。** 官方原则:Use descriptive language and minimize abstract terms and negative instructions。手册的改写示例:
   - 「高级感」→ 侧逆光勾出物体边缘,背景虚化,表面可见细腻哑光颗粒
   - 「复古感」→ 泛黄的墙面、磨旧的家具边角、暖色白炽灯
   - 「中产家庭厨房」→ 木纹橱柜,台面上有电饭煲和调料罐,冰箱门贴着便利贴
2. **否定句只留两类:无字幕、无背景音乐。** 官方:Only two types of negative instructions are truly necessary: no subtitles and no background music. The model may add these by default。其余想排除的东西改写成「要看到什么」——例:「打电话时不要露脸」→「侧脸对镜头打电话」。否定句会增加出错概率。
3. **每份素材写明职责和保留范围。** 素材按上传顺序自动编号(`@Image1`、`@Image2`、`@Video1`)。只给编号、不写职责时模型会自行推断,结果不可控。多份素材提供不同信息时,限定每份的作用范围,并点名不该带入的东西。官方示例:
   - `@Image1 defines the scene. Preserve its spatial layout, furnishings, and lighting.`
   - `@Image2 defines the female protagonist. Preserve her facial features, hairstyle, and clothing.`
   - `Reference the fighting moves and attack timing in @Video1. Use @Image1 and @Image2 as the character appearance references.`
   - `Reference the camera path and pacing of @Video1. Replace the element with the car in @Image1 and the background with @Image2.`
4. **用时间戳编排事件。** `0–X s` / `X–Y s` / `Y s–结尾`,每段写画面、动作、情绪,后段补运镜与细节,末段写收尾镜头。
5. **明写运镜。** 推、拉、摇、跟、环绕(push-in / pull-out / pan / tracking / orbit)都要写出来;一镜到底时写明「one continuous shot, no cuts」。
6. **先用一句话讲清主体、场景、动作、情绪,再只补真正影响结果的细节**(运镜、时间、声音、保留要求)。官方 FAQ:提示词不是越长越好,堆形容词不如补关键细节。
7. **风格化:正面描述 + 明确排除。** 指定动画风格时明确排除写实(When specifying an animation style, explicitly exclude photorealism),否则两种风格会混。
8. **数量锁定。** 官方长示例开头写 `SCENE CONTEXT: EXACTLY 1 CHARACTER — NO DUPLICATES`,结尾的正向约束再写一次具体数量(`Exactly one dragon remains visible throughout the sequence`)。
9. **分镜图当参考时只取镜头信息。** 官方:use the storyboard as the director-approved sequence of shots, framing, camera paths, screen direction, and emotional progression … Do not render the storyboard itself;并排除铅笔线、网格、标题栏、文字注释。

## 三、官方提示词结构

### 全能参考(Omni Reference,对应 Fal reference-to-video)

```
[Asset Roles]
@Image1 defines ___; preserve its ___.
@Image2 defines ___; preserve its ___.  Reference the ___ of @Video1.
[Overall Settings]
Scene and texture: ___; visual style: ___; camera tone: ___.
[Sequence of Events]
0–X s: ___ (visuals + action + emotion)
X–Y s: ___ (camera movement + additional details)
Y s–end: ___ (closing shot)
[Audio]
Dialogue/voiceover + ambient sounds + music + sound effects
```

官方长示例(30 秒巨龙、15 秒展厅独白等)在此基础上扩成编号段:SCENE CONTEXT、ACTIVE REFERENCES、LOCATION MAP、FIRST FRAME AND SPATIAL BLOCKING、FORMAT MODE(镜数、每镜起止秒、硬切还是一镜到底)、OPTICS、CAMERA、ACTION TIMING、PHYSICS、LIGHTING、AUDIO、CHARACTER ACTING、STYLE、QUALITY、POSITIVE CONSTRAINTS。复杂组可按需取用其中几段,不必全写。

### 白模动作参考(Fal 官方提示词指南的 white-model 示例)

白模视频只给动作和机位节奏,成片外观由参考图决定:`Preserve the movement and camera timing of the supplied white-model video, replacing the unfinished surfaces with the appearance established by the images.`

### 首帧 / 首尾帧(对应 Fal image-to-video)

- 首帧:`Use @Image1 as the first frame. [Describe what happens next.]`
- 首尾帧:`Use @Image1 as the first frame and @Image2 as the last frame`,再写中间的过程。
- 上面两句是官网「多帧」入口的写法。Fal 的首尾帧图走 `start_image_url` / `end_image_url` 字段,请求里没有 `@Image` 编号可指,正文不写图片编号:直接从首帧画面接着写动作,并写清结尾要落到的尾帧状态。

### 声音与对白

- 对白写在引号里,并写明语言:`She speaks in natural, clear, slightly slow Spanish: "…"`。
- 要特定嗓音时写年龄、音色、语速、情绪:`… has the voice of a … woman. She speaks in English with sincerity and emotional engagement: "…"`,可再写重音落在哪个词。
- 多人对白:每人只说一种语言、各有不同嗓音;一人说话时其他人保持沉默,不打断、不重叠(When one person is speaking, the others remain silent. No interruptions or overlapping dialogue);口型与对应语言同步。
- 支持语言:中文、英语、日语、韩语、西班牙语、葡萄牙语、德语、法语、印地语;方言与口音:四川话、粤语、北京口音、东北口音、台湾腔,英语美式 / 英式 / 印度口音。
- 背景音乐和音效不能上传,由模型按画面生成,要求写进声音段。

## 四、本仓库落地对照(冲突时以 SOUL.md 与团队锚点为准)

团队结构锚点(`Overall visual style:` 开头、`Shot N:` 段、`Global constraints:` 结尾、`[Image N]` / `[Video N]` 引用)、宿主机器写入的固定段(Whitebox reference / Scene presence / Shot plates / 场景激活句 / 时间段标签)、上游逐字片段(风格串、光照 prompt_fragment_en、站位 space_fragment_en、道具 prompt_token、服装 visual_en)与冻结版台词一律不动。本技能只决定这些锚点之间的内容怎么写。

| 官方段落 | 写在团队正文的位置 | 写法 |
|---|---|---|
| Asset Roles | `Overall visual style:` 之后、`Shot 1:` 之前 | 每张角色 / 生物图的主体定义句 `<角色>@Image N:<性别词+特征>` 后补一句保留范围(「保留其五官、发型与服装」/ preserve facial features, hairstyle and clothing);场景空间图、道具图、前组尾帧各写一句职责和排除项;白模视频的职责句在宿主写的 Whitebox reference 段里,不重复、不改写;用户上传的参考视频按 SOUL.md 写一句用途 |
| Overall Settings | `Overall visual style:` 段 + 光照片段 | 场景质感、视觉风格、镜头基调;抽象词按原则 1 换成具体的光线、材质、颜色(风格串本身逐字保留,不改) |
| Sequence of Events | 各 `Shot N:` 段 | 时间段标签(`0-3s:` / `0-3秒：`)由 `code/sync_shot_timing.py --write` 写在段头后,本岗不手写秒数;每段写画面 + 动作 + 情绪 + 本镜唯一运镜 + 结束状态;组内要一镜到底时在 Shot 1 写明 continuous shot、不切镜,否则多镜头默认开启、模型会自己切 |
| Audio | 对白写在对应 `Shot N:` 段内;环境声 / 音效写在各段动作后 | 见下方「对白」与「音乐」两条 |
| POSITIVE CONSTRAINTS | `Global constraints:` | 数量锁定(`exactly two characters — no duplicates`)、身份与服装全程一致;团队必带的负面句(style.json 负面清单、无字幕、无背景音乐、No whitebox look 等)照 SOUL.md 保留,不再追加通用负面词 |

**素材编号。** 正文按团队约定写 `[Image N]` / `@Image N` / `[Video N]`(1-based,与 refs / video_refs 顺序严格一致)。genmedia 提交 Fal Kling 4.0 时把它们改写成官方的 `@Image1` / `@Video1`,本岗不要自己改成无空格写法,也不要写「图片1」「Image One」等变体。

**对白。** 台词照团队规则写在 `{}` 里(一句一个 `{}`,与剧本逐字一致)。genmedia 提交时把 `{台词}` 换成官方要求的引号 `"台词"`,本岗不要在 `{}` 外再加引号(performance_bound_check 会把引号里的台词词判为重复念)。每句台词前写清:说话人 + 语言(中文 / 英语 / 方言)+ 嗓音(年龄、音色、语速、情绪)+「说」,例如 `塔尔用低沉、略慢的中文说：{雪化了是清的。}`。同一角色在各组的嗓音描述保持逐字一致——Fal 上不收参考音频,嗓音一致只能靠这句描述。多人对白的组写明一人说话时其他人沉默、不抢话不重叠。

**参考音频。** audio_refs 一律留空:Fal Kling 4.0 端点不收参考音频,genmedia 遇到 `--audio-ref` 直接报错。对白组「每个说话角色挂 voiceprint 样本」的要求(audioref_bound)对本模型不适用,在回执 notes 写明「Kling 4.0 不收参考音频,嗓音按文字描述」。

**音乐与字幕。** 本仓库的配乐、字幕都在后期做,模型又可能默认加上这两样:`Global constraints:` 里的 no subtitles or on-screen text、no background music or musical score 两句必须保留(官方唯一推荐保留的两类否定)。画内有乐器演奏等剧情内声源时,按 audio_cues 写成本镜的同步声音,不写成配乐。

**参数分离。** 画幅、时长、分辨率由 genmedia 命令行参数传,不写进正文;官方长示例里写在正文的「15 seconds, aspect ratio 16:9」在本仓库不写。

## 五、自检清单(回执 checklist 至少取其中 3 条)

1. 每份实际挂上的参考图 / 参考视频都写了职责和保留范围,没有只给编号的素材。
2. 抽象形容词已换成具体的光线、材质、颜色、动作。
3. 否定句只剩团队必带项与无字幕 / 无背景音乐,其余排除改写成正面描述。
4. 每个 Shot 段有画面、动作、情绪、唯一运镜和结束状态;一镜到底的组明写了不切镜。
5. 对白:`{}` 台词前写了说话人、语言、嗓音;多人对白写了不重叠;同一角色嗓音描述与前组一致。
6. audio_refs 为空;参考图 ≤10、参考视频 ≤5 且合计 ≤30 秒。
7. `Global constraints:` 写了人数 / 主体数量锁定与身份一致。
