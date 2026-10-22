# 🖥️ 演示视频(项目类型 `code-video`)

**不用视频生成模型,让语言模型写代码出片。** 片子是一个网页:`codevideo/index.html` 里的
`window.render(t)` 按时间画出任意一帧(Canvas 2D / WebGL / three.js),宿主用无头浏览器逐帧截图、
ffmpeg 编码;旁白走 TTS(旁白声线卡),配乐由 Agent 写作曲脚本用采样乐器与合成器渲染,
拟音按页面导出的事件时刻程序化合成,混音脚本出成片声轨,两遍 loudnorm 到 −14 LUFS。

用户只给三样:**主题 / 文稿**、**风格**(新建向导画廊里选:43 种完整风格,或 98 种 OPUSCAR 98 轻量风格)、
几项**设置**(片长、画幅、旁白、字幕、先审故事板、配乐来源:代码作曲 / 音乐模型 / 项目音乐库)。适合产品演示、讲解、发布片、品牌短片、知识科普、
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
| `styleboard/opuscar98_styles.json`(98 条风格提示,三期) | `agents/workflows/codevideo/styles/opuscar98/styles.json`(不带分镜与导演说明——那是 OPUSCAR 98 的主角 Clawd;另加宿主算的 3 个相近完整风格)+ 画廊 `apps/web/static/styles/code/opuscar98.json`、`frames98/` | ✓ |
| 采样乐器库(5 套,70–400 MB)、HDRI | 按需下载到 `data/.videoagents/codevideo/lemo/` | ✗ |
| 样片源码与海报 | 写完 TREATMENT.md 后 `codevideo.py demo` 按需下载 | ✗ |

与其它项目类型的差异:

| | 影视剧 film | 音频配画 audio-to-video | 演示视频 code-video |
|---|---|---|---|
| 画面来源 | 视频生成模型 | 视频生成模型 | **语言模型写的网页,逐帧截图** |
| 声音来源 | 模型原生 + TTS + 音乐模型 | 用户母带 | TTS 旁白 + **代码作曲**(或音乐模型 / 曲库,自动对拍)+ **程序化拟音** |
| 时长事实源 | 剧本 / 分镜 | 母带实测 | **timeline.json**(代码作曲:cue map 定拍、画面锁拍;外来音乐:实测节拍网格、时间轴对拍) |
| 单位 | 多集 | 一个音频 = ep01 | 一部片 = ep01 |
| 修改 | 重生成(计费) | 重生成 | **改代码,只重渲改动段(免费)** |
| 生成费用 | 视频模型按次计费 | 同左 | 只有 TTS(配乐选音乐模型时另计);画面、代码作曲、音效为零 |

---

## 运行前提

| 要求 | 说明 |
|---|---|
| **宿主工具链** `code/codevideo.py` + `modules/codevideo/` | 随主程序发布;首节点 cv0-ingest 跑 `codevideo.py doctor`,不通即停 |
| **Chromium(Playwright)** | 桌面端运行时自带;开发环境 `python3 -m playwright install chromium`。macOS 用 Metal 做 WebGL(`CODEVIDEO_ANGLE` 可改);软件渲染时 3D / 着色器风格会慢数倍,doctor 给「!」提示 |
| **ffmpeg / ffprobe** | 要 libx264、aac、loudnorm、ebur128、blackdetect;桌面端设置里装,开发环境 `brew install ffmpeg` |
| **Python 依赖** | numpy、scipy、soundfile、Pillow(soxr 可选,没有时采样器退回 scipy 重采样)。`pluck.py`(物理建模拨弦)要 numba,桌面端没有——不要用 |
| **TTS 渠道** | 只有开了旁白才需要;旁白声线由项目旁白声线卡固定(同主流程) |
| **引擎** | 画面与配乐都是写代码:引擎能力直接决定成片质量。本类型 4 个创作成员(18-code-video/*)智能分配默认高档,终审 11-qa/code-video-qa 随审核类低档(services/runtime/core.py AM_CATEGORY_TIERS);上游风格按 Claude Opus 调过,换别的模型不一定做得出来 |

## 流程

```
cv0 立项与风格     film-director:codevideo.py doctor → style(锁定风格)→ brief.json
cv1 导演方案       film-director:TREATMENT.md(三个候选结构、对标、镜头表、节拍、cue map、声音设计表、字幕设计)
                    + timeline.json + lines.json → codevideo.py sync(写 directing/ep01/)
                   scene-coder:正式绘制代码出风格帧 / model sheet(cv1-look)
                    → 关键镜 6–9 个用真代码渲进故事板页(cv1-board,codevideo.py board)
                   └─ gc1 [CVH1 故事板签字] — project.storyboard_review=false 时跳过
cv2 旁白与配乐     narrator:codevideo.py voice(逐句 TTS,旁白关时跳过)
                   ∥ code-composer:代码作曲 score.py → genmedia.py music --provider code --script
                                    或 外来音乐(音乐模型 raw / 项目音乐库 / 用户音乐)→ codevideo.py music-fit
                   → film-director:codevideo.py snap 把时间轴对到拍上(cv2-beatsync,只在外来音乐时派)
cv3 画面编程       scene-coder:引擎(cv3-engine)→ 每段 sections/<段 id>.js(cv3-sec,按组扇出、各段并行,每单全新会话)
                    → codevideo.py render(按段出片,指纹未变跳过)+ readcheck + events(cv3-render)
cv4 混音与成片     sound-designer:mix.py → codevideo.py mix → CREDITS → codevideo.py master
cv5 终审           code-video-qa:codevideo.py check --stage all + 人工级抽检
                   └─ gc2 [CVH2 成片签字]
```

条件节点:`storyboard_review=false` → cv1-board、gc1 skipped;`narration=false` → cv2-voice skipped;
`music_source=code`(默认)→ cv2-beatsync skipped。`music_source=model` → cv2-score 按「生成模型」页音乐设置生成
(那里选的若是「代码作曲」,cv0-ingest 停下请用户换成音乐模型);`music_source=library` → 从项目音乐库 / 用户音乐选曲。
两种外来音乐都经 `music-fit` 适配片长并测拍,cv3 画面在 cv2-beatsync 对拍之后才开工。

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
- **时刻从 timeline.json 读**:段 / 镜 / 击点 / 台词的时刻运行时读 `codevideo/timeline.json`(或由它生成的文件),页面代码里
  不手抄秒数;段内动作写成相对本段 / 本镜起点的比例或拍数。这样 `snap` 对拍、日后改时间轴都不用改代码。

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
  style/                      cv0 锁定的风格副本 STYLE.md style.json + lock.json{slug, light, sha256, lemo_rev, related?};
                              轻量风格的 STYLE.md / style.json 由宿主按风格提示生成;
                              DEMO.md 只在写完 TREATMENT.md 后由 `codevideo.py demo` 放进来(先方案后样片,宿主强制)
  TREATMENT.md                cv1 导演方案
  timeline.json lines.json    cv1 时间轴与旁白台词 [{id, text, say?, delivery?}]
  index.html main.js js/ fonts/ sections/<段 id>.js     cv3 片子页面
  voices/<id>.wav dur.json    cv2-voice
  music/score.py score.wav score.meta.json score.wav.credits.json     cv2-score(代码作曲)
  music/raw.<ext> score.wav beats.json                                 cv2-score(外来音乐:raw = 音乐模型原始输出)
  timeline.before-snap-<时间>.json                                     cv2-beatsync 对拍前的备份
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
assets/audio/bgm/ep01/cue_sheet.json ep01_bgm_01.wav    codevideo.py music-fit(外来音乐的使用记录,同步项目音乐库)
edit/ep01/final.mp4 subtitles.srt poster.jpg    codevideo.py master
```

## 宿主 CLI(`python3 code/codevideo.py <子命令> --project <slug>`)

| 子命令 | 作用 | 谁用 |
|---|---|---|
| `doctor` | Chromium / WebGL / ffmpeg / Python 依赖 / 宿主库 / 风格库自检;退出 0 = 可用 | film-director(cv0) |
| `style [--slug S]` | 锁定风格到 `codevideo/style/` | film-director(cv0) |
| `demo [--slug S]` | 写完 TREATMENT.md 才能下载样片源码与海报;未写退出码 3。轻量风格没有样片,须 `--slug` 指定一个完整风格 | film-director / scene-coder |
| `sync` | 校验 timeline.json → 写 directing/ep01/ | film-director |
| `still T… [--range a:b:step] [--sheet] [--prefix p]` | 静帧到 `codevideo/stills/`,`--sheet` 拼联系表到 `codevideo/sheets/` | scene-coder、QA |
| `board [--keys id,…]` | 关键镜真代码帧进故事板页 + 联系表 | scene-coder(cv1-board) |
| `events` | 导出 `codevideo/events.json` | scene-coder |
| `readcheck` | 文字阅读时长:汉字 ÷ 4.5 + 其它字符 ÷ 15 + 1.5 s(≥ 1.5 s);退出 1 = 有不够读的,2 = 页面没有 TEXTS | scene-coder |
| `voice [--force]` | lines.json 逐句 TTS → voices/ + dur.json,只补新句 / 改过的句 | narrator(cv2-voice) |
| `render [--section ID…] [--force] [--workers N]` | 按段渲染,指纹没变的段跳过 | scene-coder |
| `mix` | 跑 `codevideo/mix.py`,校验 mix.wav 与分轨 | sound-designer |
| `master [--grain N]` | 拼段 + 两遍 loudnorm(−14 LUFS,真峰值 −1.2 dB)→ final.mp4 + srt + 海报 + build.sh;有段过期即拒绝 | sound-designer |
| `music-fit (--track MUS-NNNN \| --file <音频>) [--start auto\|秒] [--fade 1.5] [--mood 文本]` | 外来配乐适配片长 → `music/score.wav` + `beats.json`;写 cue sheet、同步项目音乐库 | code-composer(cv2-score) |
| `beats [--audio <文件>]` | 只测节拍网格 → `music/beats.json` | 排查用 |
| `snap [--sections downbeat\|beat\|off] [--shots beat\|off] [--hits beat\|off] [--dry-run]` | 时间轴对到节拍网格,改前备份,改后自动 sync | film-director(cv2-beatsync) |
| `check --stage contract,timeline,beats,clips,final,delivery\|all` | 机检(下表) | 各工位自检、QA |
| `build` | sync → events → voice → score → render → mix → master 一键重建 | 用户 / 修改师 |
| `info` | 状态总览 JSON(含渲染尺寸、各段是否过期) | 任何人 |
| `packs [--fetch NAME \| --remove NAME]` | 采样库 / HDRI 包状态与下载 | code-composer |

退出码统一:0 成功;1 失败(原因在 stderr);2 用法错误。

### 机检(`codevideo.py check`)

| 阶段 | 项 | 要求 |
|---|---|---|
| contract | `page_globals` `deterministic` `no_external_requests` `no_page_errors` | READY / DUR / render 存在;同一时刻正序、倒序各渲一次截图一致;渲染时无外网请求;页面无报错 |
| timeline | `timeline_valid` `directing_synced` `page_dur_matches` | 时间轴合规;shot_list 已同步;window.DUR 与 dur 差 ≤ 1 帧 |
| beats | `beats_match_score` `hits_on_beat` `sections_on_beat` | 只在外来音乐(有 `music/beats.json`)时有项:网格按当前 score.wav 测的;每个击点、每个段边界离最近拍点 ≤ 1 帧 |
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

## 外来配乐与节拍对齐(三期)

代码作曲是「先网格后画面」:作曲脚本按 timeline.json 算拍点,画面与音乐天然对齐。配乐选**音乐模型**(`music_source=model`)
或**项目音乐库 / 用户音乐**(`library`)时音乐是现成的,反过来让画面贴音乐:

1. **选段适配** `codevideo.py music-fit`(cv2-score):从强拍起(跳过开头静音)截一段等于片长的音乐;不够长就在两个强拍之间
   按小节无缝循环(首尾 30 ms 交叉淡化);开头 10 ms 淡入、结尾 1.5 s 淡出(`--fade`)→ `codevideo/music/score.wav`(48 kHz 立体声)。
   同时测出节拍网格写 `codevideo/music/beats.json`(`bpm` `beats[]` `downbeats[]` `meter` `confidence` `audio_sha256` `license`),
   写 `assets/audio/bgm/ep01/cue_sheet.json` 一条 cue 并同步项目音乐库——曲库曲目记使用记录(派生片段不另入库),
   模型生成的新曲按「每集新增 BGM 入库」的老规矩入库。
2. **对拍** `codevideo.py snap`(cv2-beatsync,film-director):
   - 段边界 → 最近的强拍(容差半小节);半小节内没有强拍就找最近拍点(容差半拍);都没有就不动;
   - 段边界的移动用**分段线性时间映射**带动段内一切时刻(镜头起止、台词时点、击点、海报帧),**片长不变**;
   - 段内镜头切点 → 最近拍点(容差半拍,两侧都保住 ≥ 0.25 s 的镜长,镜头首尾相接不变);击点 → 最近拍点(容差半拍);
   - 每类可单独 `off`(刻意不对拍的段落),`--dry-run` 只出报告不写盘;写盘前备份 `timeline.before-snap-<时间>.json`,写后自动 sync。
3. **机检** `check --stage beats`:网格与当前 score.wav 一致(`music-fit` 之后不得再改 score.wav,换曲就重跑 music-fit);
   击点与段边界离最近拍点 ≤ 1 帧。某类用了 `off` 时对应项会 FAIL,按 cv2-beatsync 回执的理由登记 waiver。

测拍只用 numpy / scipy(`modules/codevideo/beats.py`):对数频带谱通量作起音包络 → 自相关估速度(以 120 BPM 为中心的
对数高斯先验)→ Ellis 2007 动态规划跟拍 → 低频起音定强拍相位。用合成鼓点、拨弦与慢起音弦乐在 72–140 BPM 实测:速度误差
< 0.1 BPM,拍点误差 ≤ 25 ms(24 fps 一帧 42 ms),强拍相位正确。节奏不清楚的曲子(`confidence` 低)对拍会不准——
cv2-score 回执要写出 confidence,film-director 据此决定段边界用 `beat` 还是 `off`。

`music_source=model` 时 cv0-ingest 先看 `genmedia.py info` 的 music 行:若是「代码作曲」,停下请用户到「🎨 生成模型」页换成
一个音乐模型(Agent 不改用户设置,也不擅自改走代码作曲)。

## 轻量风格(OPUSCAR 98,三期)

lemo-opuscar 的《OPUSCAR 98》用 98 种风格给 98 部最佳影片各画了一段,每段公开了一条**可复用的风格提示**
(中英各一段:画面、色值、镜头与转场规律、「避免」条款)。它们作为轻量风格进了新建向导画廊的第二组(id `o98-01` … `o98-98`,
按 10 个章节筛选)。与 43 个完整风格的区别:

| | 完整风格 | 轻量风格 |
|---|---|---|
| 规范 | 十一节 STYLE.md(材质、色彩、字体、运动、镜头语法、声音调色板、原生手法、陷阱…) | 一段风格提示 + 混合手法 + 渲染类型(2D / 2.5D / 3D)+ 参考作品 |
| 样片源码 | 有(写完方案后 `demo` 下载) | 没有;`demo --slug <完整风格>` 拉相近风格的样片学技法 |
| 立项锁定 | 复制 STYLE.md / style.json | 宿主按提示生成 `codevideo/style/STYLE.md` 与 `style.json`,`lock.json` 带 `related`(3 个相近完整风格,按文本相似度算,仅供参考) |
| 方案 | 照 STYLE.md 导演 | TREATMENT.md 多一节「**风格补全**」:材质 / 色彩补充 / 字体与字幕 / 运动质感 / 镜头语法 / 声音调色板 / 原生手法,逐项写取舍与依据(从提示与参考作品推出) |

生成的 STYLE.md 结构:① 风格提示原文(中英,不变量,全片照做)② 构成(渲染、色调、混合手法)③ 参考(只学语法)
④ 这段提示原来怎么用的(只作说明)⑤ 轻量风格须知(补全口径、技法参考、版权红线)。
**版权**:提示与参考里点名的电影、导演、作品只学语法——片中不得出现它们的角色、情节、镜头复刻、片名、logo 或配乐;
不得使用 OPUSCAR 98 的角色(包括 Clawd);片子里不带 LemoLab / OPUSCAR 署名。上游的 shots / direction 字段写的是 Clawd 的表演,
同步时整段不带。

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
| `film-director` | 自检与锁定风格、导演方案(轻量风格含「风格补全」)、时间轴、旁白台词、外来音乐对拍(cv2-beatsync) | `codevideo/brief.json`、`TREATMENT.md`、`timeline.json`、`lines.json` |
| `scene-coder` | 风格帧、故事板关键帧、页面引擎、逐段画面、渲染、阅读检查、事件与字幕导出 | `codevideo/index.html` 等、`assets/clips/ep01/`、`assets/storyboard/ep01/` |
| `code-composer` | 代码作曲:按 cue map 写作曲脚本,配器取风格的声音调色板;外来音乐:音乐模型生成或曲库选曲 + `music-fit` | `codevideo/music/score.py` + `score.wav`(外来音乐:`score.wav` + `beats.json`) |
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
- 节拍对齐只认均匀拍(单一速度、固定拍号);渐快渐慢、变拍子的曲子测出的网格会漂,cv2-beatsync 应对相应段落用 `off`;
- 轻量风格没有完整规范与样片,成片质量更依赖导演的「风格补全」与引擎能力;相近完整风格只是按文本相似度推荐的技法参考;
- `pluck.py` 需要 numba,桌面端运行时不带;
- 渲染速度取决于页面复杂度与 GPU:2D 页面在 Apple 芯片上约每秒 15–20 帧(1080×1920,2 个 worker 实测),3D 页面更慢。
