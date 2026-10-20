# 🖥️ 演示视频(项目类型 `code-video`)

**不用视频生成模型,让语言模型写代码出片。** 片子是一个网页:`codevideo/index.html` 里的
`window.render(t)` 按时间画出任意一帧(Canvas 2D / WebGL / three.js),宿主用无头浏览器逐帧截图、
ffmpeg 编码;旁白走 TTS(旁白声线卡),配乐由 Agent 写作曲脚本用采样乐器与合成器渲染,
拟音按页面导出的事件时刻程序化合成,混音脚本出成片声轨,两遍 loudnorm 到 −14 LUFS。

用户只给三样:**主题 / 文稿**、**风格**(43 种,新建向导画廊里选)、几项**设置**(片长、画幅、
旁白、字幕、先审故事板、配乐来源)。适合产品演示、讲解、发布片、品牌短片、知识科普、
绘本 / 水墨 / 版画等风格化短片——30–60 秒最合适,超过两三分钟时间与 token 成倍增加。

导演与制作方法来自 **lemo-opuscar**(MIT,[github.com/lemomo-ai/lemo-opuscar](https://github.com/lemomo-ai/lemo-opuscar)):
每个风格一份 `STYLE.md`(这个风格的固定规律:材质、色彩、字体、运动、镜头语法、声音调色板、媒介原生手法、
易犯错误)和一部样片。`scripts/sync_lemo_opuscar.py` 按固定 commit 把它同步进本仓库:

| 上游 | 本仓库 | 随包 |
|---|---|---|
| `styles/<slug>/STYLE.md` `DEMO.md` `style.json`(43 个) | `agents/workflows/codevideo/styles/<slug>/` | ✓ |
| `core/lib.js` `core/three/post.js` `core/post/crt.js` `core/fonts/` | `modules/codevideo/lib/core/` | ✓ |
| `core/audio/sampler.py` `sfx.py` `pluck.py` | `modules/codevideo/lib/core/audio/`(sampler 打了两处补丁:采样库目录可指定、缺 soxr 退回 scipy) | ✓ |
| three.js r170 | `modules/codevideo/lib/vendor/three/`(页面里仍写 `/node_modules/three/…`) | ✓ |
| 新建向导画廊封面与名称 | `apps/web/static/styles/code/` | ✓ |
| 采样乐器库(5 套,70–400 MB)、HDRI | 按需下载到 `data/.videoagents/codevideo/lemo/` | ✗ |
| 样片源码与海报 | 写完 TREATMENT.md 后 `codevideo.py demo` 按需下载 | ✗ |

与其它项目类型的差异:

| | 影视剧 film | 音频配画 audio-to-video | 演示视频 code-video |
|---|---|---|---|
| 画面来源 | 视频生成模型 | 视频生成模型 | **语言模型写的网页,逐帧截图** |
| 声音来源 | 模型原生 + TTS + 音乐模型 | 用户母带 | TTS 旁白 + **代码作曲** + **程序化拟音** |
| 时长事实源 | 剧本 / 分镜 | 母带实测 | **timeline.json**(cue map 定拍,画面锁拍) |
| 单位 | 多集 | 一个音频 = ep01 | 一部片 = ep01 |
| 修改 | 重生成(计费) | 重生成 | **改代码,只重渲改动段(免费)** |
| 生成费用 | 视频模型按次计费 | 同左 | 只有 TTS;画面、配乐、音效为零 |

---

## 运行前提

| 要求 | 说明 |
|---|---|
| **宿主工具链** `code/codevideo.py` + `modules/codevideo/` | 随主程序发布;首节点 cv0-ingest 跑 `codevideo.py doctor`,不通即停 |
| **Chromium(Playwright)** | 桌面端运行时自带;开发环境 `python3 -m playwright install chromium`。macOS 用 Metal 做 WebGL(`CODEVIDEO_ANGLE` 可改);软件渲染时 3D / 着色器风格会慢数倍,doctor 给「!」提示 |
| **ffmpeg / ffprobe** | 要 libx264、aac、loudnorm、ebur128、blackdetect;桌面端设置里装,开发环境 `brew install ffmpeg` |
| **Python 依赖** | numpy、scipy、soundfile、Pillow(soxr 可选,没有时采样器退回 scipy 重采样)。`pluck.py`(物理建模拨弦)要 numba,桌面端没有——不要用 |
| **TTS 渠道** | 只有开了旁白才需要;旁白声线由项目旁白声线卡固定(同主流程) |
| **引擎** | 画面与配乐都是写代码:引擎能力直接决定成片质量。cv1 / cv3 节点建议用智能分配高档;上游风格按 Claude Opus 调过,换别的模型不一定做得出来 |

## 流程

```
cv0 立项与风格     film-director:codevideo.py doctor → style(锁定风格)→ brief.json
cv1 导演方案       film-director:TREATMENT.md(三个候选结构、对标、镜头表、节拍、cue map、声音设计表、字幕设计)
                    + timeline.json + lines.json → codevideo.py sync(写 directing/ep01/)
                   scene-coder:正式绘制代码出风格帧 / model sheet(cv1-look)
                    → 关键镜 6–9 个用真代码渲进故事板页(cv1-board,codevideo.py board)
                   └─ gc1 [CVH1 故事板签字] — project.storyboard_review=false 时跳过
cv2 旁白与配乐     narrator:codevideo.py voice(逐句 TTS,旁白关时跳过)
                   ∥ code-composer:score.py → genmedia.py music --provider code --script
cv3 画面编程       scene-coder:引擎(cv3-engine)→ 每段 sections/<段 id>.js(cv3-sec,按组扇出)
                    → codevideo.py render(按段出片,指纹未变跳过)+ readcheck + events(cv3-render)
cv4 混音与成片     sound-designer:mix.py → codevideo.py mix → CREDITS → codevideo.py master
cv5 终审           code-video-qa:codevideo.py check --stage all + 人工级抽检
                   └─ gc2 [CVH2 成片签字]
```

条件节点:`storyboard_review=false` → cv1-board、gc1 skipped;`narration=false` → cv2-voice skipped。
`music_source=model` → cv2-score 按「生成模型」页音乐设置生成(不写作曲脚本)。

## 页面契约(与 lemo-opuscar 完全一致)

| 全局 | 必需 | 含义 |
|---|---|---|
| `window.READY = true` | 是 | 字体与图片加载完再置 true(宿主最多等 180 秒) |
| `window.render(t)` | 是 | 画出第 t 秒的那一帧;**确定性**:不用 `Date.now()`、不用无种子随机(`/core/lib.js` 有 `mulberry`、`hash`)、不依赖上一帧的状态——任何帧可单独、乱序、由任何 worker 渲染 |
| `window.DUR` | 是 | 片长(秒),须与 timeline.json 的 dur 相差 ≤ 1 帧 |
| `window.EV = [{t, type, …}]` | 拟音用 | 声音 / 击点事件,`codevideo.py events` 导出给 mix.py |
| `window.TEXTS(t)` | 阅读检查用 | t 时刻画面上每段文字 `{id, text, x0, y0, x1, y1}`;打字机效果也要报**全文** |

- 库文件用绝对路径:`/core/lib.js`、`/core/three/post.js`、`/core/post/crt.js`、`/core/fonts/fonts.css`、
  importmap `"three": "/node_modules/three/build/three.module.js"`、`"three/addons/": "/node_modules/three/examples/jsm/"`、
  `/core/assets/polyhaven/…`(HDRI 与 CC0 模型,缺失时宿主自动下载)。自己的文件用相对路径。
- 视口 = 渲染尺寸(`codevideo.py info` 的 size,按画幅 × 成片分辨率算),布局一律从 `innerWidth × innerHeight` 取。
- **渲染时外网请求一律被拦**:字体放 `codevideo/fonts/` 用 `@font-face` 加载(中文字体按用到的字裁子集)。
- **按段代码**:每段的画面写在 `codevideo/sections/<段 id>.js`(或 `js/sections/<段 id>.js`)。只改这个文件 = 只有这一段过期;
  改其余文件(引擎、timeline.json、voices/dur.json、字体、图片)= 全部段过期。
- **字幕**:页面自己画(字幕设计是风格的一部分),驱动字幕的时间线同时导出为 `codevideo/cues.json` = `[{t0, t1, text}]`,
  master 据此写 `edit/ep01/subtitles.srt`。字幕关时不写 cues.json。

## timeline.json(`codevideo_timeline/1`,唯一事实源)

```json
{"schema": "codevideo_timeline/1", "title": "片名", "fps": 24, "size": [1920, 1080], "dur": 54.0,
 "bpm": 96, "meter": 4, "poster_t": 50.5,
 "sections": [{"id": "open", "title": "开场", "t0": 0, "t1": 8, "bpm": 96, "note": "…"}],
 "shots": [{"id": "s1", "section": "open", "t0": 0, "t1": 3.5, "size": "wide", "angle": "top",
            "move": "push", "content": "画面内容", "why": "为什么这样拍", "key": true}],
 "hits": [{"id": "stamp", "t": 52.1, "what": "印章落下", "cue": "D 大调终止的强拍"}],
 "lines": [{"id": "l01", "t": 2.0, "section": "open"}],
 "black_ok": [{"t": 30.0}]}
```

校验(`codevideo.py sync` / `check --stage timeline`):首段 t0 = 0、各段首尾相接、末段 t1 = dur;镜头落在所属段内且段内首尾相接
覆盖;id 唯一且只用 `[A-Za-z0-9_-]`;fps ∈ {24, 25, 30};size 为偶数;dur ≤ 600。`poster_t` 选海报帧,`black_ok` 放行刻意的黑场。

编号:第 i 段 → 场次号 `S{i*10:03d}`、组号 `grp{i*10:04d}`;全片第 j 镜 → `sh{j*10:04d}`。帧边界累计取整
`round(t × fps)`,各段帧数相加恰等于整片帧数,拼接不丢不多。

## 目录布局

```
codevideo/                    ← 就是一个 lemo-opuscar 片子工程目录
  brief.json                  cv0 规范化需求(主题、风格、片长、画幅与尺寸、fps、语言、各开关、用户素材与权利声明)
  style/                      cv0 锁定的风格副本 STYLE.md style.json + lock.json{slug, sha256, lemo_rev};
                              DEMO.md 只在写完 TREATMENT.md 后由 `codevideo.py demo` 放进来(先方案后样片,宿主强制)
  TREATMENT.md                cv1 导演方案
  timeline.json lines.json    cv1 时间轴与旁白台词 [{id, text, say?, delivery?}]
  index.html main.js js/ fonts/ sections/<段 id>.js     cv3 片子页面
  voices/<id>.wav dur.json    cv2-voice
  music/score.py score.wav score.meta.json score.wav.credits.json     cv2-score
  events.json cues.json       cv3-render 导出
  mix.py out/mix.wav out/stems/{voice,music,sfx,ambience}.wav out/mix.json     cv4-mix
  stills/ sheets/             静帧与联系表(自检、审片)
  render.json                 按段渲染指纹台账(宿主写)
  notes.json                  代码预览页注释
  CREDITS build.sh            交付(build.sh 由 master 写)
directing/ep01/shot_list.json storyboard.json   codevideo.py sync 生成(段 = 场次 = 组)
assets/storyboard/ep01/       codevideo.py board:关键镜 <S010-01>.jpg + index.json(provider=code)+ board_sheet.jpg
assets/clips/ep01/<grp>.mp4   codevideo.py render:每段一条无音轨片段 + .meta.json(boundary_map)+ .last_frame.png
assets/audio/final/ep01.wav   codevideo.py mix 复制
edit/ep01/final.mp4 subtitles.srt poster.jpg    codevideo.py master
```

## 宿主 CLI(`python3 code/codevideo.py <子命令> --project <slug>`)

| 子命令 | 作用 | 谁用 |
|---|---|---|
| `doctor` | Chromium / WebGL / ffmpeg / Python 依赖 / 宿主库 / 风格库自检;退出 0 = 可用 | film-director(cv0) |
| `style [--slug S]` | 锁定风格到 `codevideo/style/` | film-director(cv0) |
| `demo` | 写完 TREATMENT.md 才能下载本风格样片源码与海报;未写退出码 3 | film-director / scene-coder |
| `sync` | 校验 timeline.json → 写 directing/ep01/ | film-director |
| `still T… [--range a:b:step] [--sheet] [--prefix p]` | 静帧到 `codevideo/stills/`,`--sheet` 拼联系表到 `codevideo/sheets/` | scene-coder、QA |
| `board [--keys id,…]` | 关键镜真代码帧进故事板页 + 联系表 | scene-coder(cv1-board) |
| `events` | 导出 `codevideo/events.json` | scene-coder |
| `readcheck` | 文字阅读时长:汉字 ÷ 4.5 + 其它字符 ÷ 15 + 1.5 s(≥ 1.5 s);退出 1 = 有不够读的,2 = 页面没有 TEXTS | scene-coder |
| `voice [--force]` | lines.json 逐句 TTS → voices/ + dur.json,只补新句 / 改过的句 | narrator(cv2-voice) |
| `render [--section ID…] [--force] [--workers N]` | 按段渲染,指纹没变的段跳过 | scene-coder |
| `mix` | 跑 `codevideo/mix.py`,校验 mix.wav 与分轨 | sound-designer |
| `master [--grain N]` | 拼段 + 两遍 loudnorm(−14 LUFS,真峰值 −1.2 dB)→ final.mp4 + srt + 海报 + build.sh;有段过期即拒绝 | sound-designer |
| `check --stage contract,timeline,clips,final,delivery\|all` | 机检(下表) | 各工位自检、QA |
| `build` | sync → events → voice → score → render → mix → master 一键重建 | 用户 / 修改师 |
| `info` | 状态总览 JSON(含渲染尺寸、各段是否过期) | 任何人 |
| `packs [--fetch NAME \| --remove NAME]` | 采样库 / HDRI 包状态与下载 | code-composer |

退出码统一:0 成功;1 失败(原因在 stderr);2 用法错误。

### 机检(`codevideo.py check`)

| 阶段 | 项 | 要求 |
|---|---|---|
| contract | `page_globals` `deterministic` `no_external_requests` `no_page_errors` | READY / DUR / render 存在;同一时刻正序、倒序各渲一次截图一致;渲染时无外网请求;页面无报错 |
| timeline | `timeline_valid` `directing_synced` `page_dur_matches` | 时间轴合规;shot_list 已同步;window.DUR 与 dur 差 ≤ 1 帧 |
| clips | `clip_<段 id>` | 每段 mp4 存在、帧数 = 段帧数、无音轨、尺寸一致、指纹新鲜 |
| final | `final_frames` `final_has_audio` `no_black_frames` `loudness` `true_peak` | 帧数 = 整片帧数;无 ≥ 0.5 s 黑场(`black_ok` 除外);−14 ± 1 LUFS;真峰值 ≤ −1 dBTP |
| delivery | `credits_present` `credits_fonts` `credits_instruments` `build_sh` `no_library_signoff` | CREDITS 写全字体与采样署名;build.sh 在;项目产物里没有 LemoLab / Opus 署名 |

## 代码作曲 / 代码音效 / 混音的脚本契约

**代码作曲**(cv2-score,也是「生成模型」页音乐的默认渠道):

```bash
python3 modules/genmedia.py music --provider code --prompt "<英文一句描述>" \
  --output codevideo/music/score.wav --script codevideo/music/score.py --duration <片长>
```

脚本是普通 Python:按环境变量 `CODE_AUDIO_OUT` 写 48 kHz 立体声 wav,可读 `CODE_AUDIO_DURATION`;
`sys.path.insert(0, os.environ['LIB'])` 后 `from core.audio import sampler as S`、`from core.audio import sfx`。
采样器用法见 `modules/codevideo/lib/core/audio/INSTRUMENTS.md`(`S.note / S.hit / S.chord / S.render / S.room`,
事件元组 `(t, 乐器, 音高, 时值, 力度, 声像[, 增益])`)。缺采样库时宿主按「生成模型」页代码作曲的「缺采样库时自动下载」开关
下载(或 `codevideo.py packs --fetch instruments-<库>`);宿主另写 `<输出>.meta.json`(provider=code、脚本 sha256)与
`<输出>.credits.json`(用到的乐器署名行,CREDITS 必收)。

**代码音效**:`python3 modules/genmedia.py sfx --provider code --prompt "…" --output x.wav --script fx.py [--duration] [--loop]`,
契约同上(`CODE_AUDIO_LOOP=1` 表示要无缝循环)。演示视频的拟音一般直接写在 mix.py 里,不单独出文件。

**混音**:`codevideo/mix.py` 由 `codevideo.py mix` 运行。环境变量 `CODE_AUDIO_OUT` = `codevideo/out/mix.wav`、
`CODEVIDEO_STEMS` = `codevideo/out/stems/`、`CODEVIDEO_FILM` = `codevideo/`。必须写 mix.wav(≥ 片长);应写
`stems/{voice,music,sfx,ambience}.wav`(有哪层写哪层,与 mix 同长,后期页声音轨读它们)。输入:`events.json`、
`voices/dur.json` 与 `voices/<id>.wav`、`music/score.wav`。

## 审片循环(lemo DIRECTOR §8 / §11)

1. 联系表:全片每 1–2 秒一帧(`still --range 0:<dur>:1.5 --sheet`),至少看两遍;
2. 关键动作 0.2 秒抽帧条(交接、落下、击打、转场);
3. 成片:响度、黑场、帧数由 `check --stage final` 管;
4. 带声音完整看一遍。

常见失败:主体太小 / 贴边;太暗、同色叠同色;字幕压主体;笑点 / 失败来不及看懂;转场里出现空帧;
跟随特效(光圈、放大、聚光)在镜头运动后脱离主体(一律经同一个 world→screen 函数换算)。

## 代码预览页(`/preview/code`)

控制台「预览」菜单 →「🖥️ 代码预览」。片子本来就是网页,所以不用渲染就能实时看:

- 拖动进度条即调 `render(t)`,播放时与 `codevideo/out/mix.wav` 同步;叠加段落 / 镜头 / 击点标记,可显示 TEXTS 文字框;
- 页面在单独的端口(与控制台不同源)、带 CSP 的 sandbox iframe 里运行,读不到控制台接口;
- 各段渲染状态(最新 / 过期 / 未渲染),「渲染改动段」只重渲过期段;
- 按时刻写注释 →「📨 提交注释」整批发修改师(kind=codevideo,代行工位 scene-coder、film-director),改完只重渲改动段。

## 团队

**新增 4 个**(类别 `18-code-video`「演示视频」)+ 1 个 QA,另复用 `09-audio/narrator`、`00-orchestration/reviser`:

| Agent | 职责 | 主要产物 |
|---|---|---|
| `film-director` | 自检与锁定风格、导演方案、时间轴、旁白台词 | `codevideo/brief.json`、`TREATMENT.md`、`timeline.json`、`lines.json` |
| `scene-coder` | 风格帧、故事板关键帧、页面引擎、逐段画面、渲染、阅读检查、事件与字幕导出 | `codevideo/index.html` 等、`assets/clips/ep01/`、`assets/storyboard/ep01/` |
| `code-composer` | 按 cue map 写作曲脚本,配器取风格的声音调色板 | `codevideo/music/score.py` + `score.wav` |
| `sound-designer` | 程序化拟音、环境床、混音、CREDITS、出成片 | `codevideo/mix.py`、`out/`、`edit/ep01/final.mp4` |
| `11-qa/code-video-qa` | 全量机检 + 人工级抽检 + 不重复样片 + 版权核对 | `qa/reports/ep01/codevideo.json` |

## 版权红线(lemo DIRECTOR §12)

- 对标作品与样片只学语法:不抄角色、设计、旋律、镜头、logo、字体设计;
- 现成作品、游戏、品牌、真实活动不进片,除非用户拥有或有权使用——用户交来的 logo、照片、素材,用户声明有权使用才用,否则画虚构替身;
- 部分上游样片是真实事件或角色的非官方致敬片:只取风格,名称、标志、角色一律不带进用户的片子;
- 只用 CC0 / CC BY / OFL 素材,逐条写进 `codevideo/CREDITS`;
- **用户的片子里没有 LemoLab / Claude / Opus 署名**,也不复刻上游样片的片尾卡(机检 `no_library_signoff`;画进图里的字 grep 不到,QA 看片尾最后几秒)。

## 已知限制

- 一个项目只做一部片(ep01);多集请建多个项目;
- 语音转写核对不做(用户规定),旁白读错由人工试听发现;
- 采样乐器库与 HDRI 按需下载(GitHub Release),离线环境只能用合成音色;
- `pluck.py` 需要 numba,桌面端运行时不带;
- 渲染速度取决于页面复杂度与 GPU:2D 页面在 Apple 芯片上约每秒 15–20 帧(1080×1920,2 个 worker 实测),3D 页面更慢。
