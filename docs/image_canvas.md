# 画板(/preview/canvas)

对一张已有图片做精确修改的独立页面:圈出位置写修改内容、附参考图、选图像模型出图;放大;裁剪 / 翻转 / 旋转 / 缩放;所有结果保留为历史版本,选一张设为最终版。

核心规则:**编辑、放大、调整的结果只进这张图自己的历史;点「设为最终版」才动生产链路。**

## 入口

各处「✏️ 修改」后面的「🖌 画板」按钮(`apps/web/static/canvas-entry.js`,`CanvasEntry.btn({url, kind, label, oid})`),在当前窗口跳到
`/preview/canvas?project=&file=<项目内相对路径>&kind=&label=&oid=`(桌面端不允许开新窗口;画板页左上角「<」返回)。

| 页面 | 图片 | kind |
|---|---|---|
| 场景预览 | 场景图 `assets/concepts/scenes/<SCN>/*.png` | scene |
| 场景预览 | 分镜背景图 `…/plates/<key>.png` | plate |
| 人物预览 | 人物图 `assets/concepts/characters/<id>/…` | character |
| 人物预览 | 服装图(服装板块里已出图的 sheet) | costume |
| 生物 / 道具预览 | 概念图 | creature / prop |
| 故事板 | 草图 `assets/storyboard/<ep>/<键>.png` | sketch |
| 成片发布 | 封面(`edit/<ep>/` 下文件名带 thumb 的图) | cover |

最终版怎么落**只按路径判**(`modules/image_canvas.locate`):分镜背景图、草图、其余固定路径三种;页面传的 kind 只细化显示与设定归属(服装 / 封面)。

## 存放

```
assets/canvas/<原图相对路径压平>.<sha8>/
    history.json        台账:versions[] / final / disk / refs[](参考图托盘)/ jobs[](最近任务号)/ final_log[] / ui{label, oid}
    v000.png …          各版本(扩展名随原图;内容按文件头——本库 .png 多为 JPEG 字节)
    v003.raw.png        圈外锁定前模型返回的整图
    v003.thumb.jpg      缩略图
    refs/NN_<名>        用户上传的参考图(从项目库选的只记路径,不复制)
    jobs/<任务号>/      job.json、source.png(底图)、marked.jpg(带圈标注图)、mask.png、region_N.jpg、result.png、prompt.txt
    trash/              被「删除」的版本文件(不真删)
assets/canvas/index.json    aliases:分镜背景图登记出去的 <key>_revN 文件 → 原图的画板(从 _revN 打开看到同一份历史)
```

不放在资产目录里,因为预览页递归列出 `assets/concepts/<类>/<id>/` 下所有图片。

版本记录:`id / file / thumb / op(original|external|edit|upscale|transform)/ parent / created_at / width / height / bytes / encoding / sha256`,
编辑另有 `provider / model / prompt / text / regions / refs / seed / model_size / lock{applied, drift, warn, raw}`,放大有 `upscale{mode, target, model_size, factor, upscaler}`,
调整有 `transform{op, …}`,登记进背景图库的版本有 `plate_key`。

- 第一次打开把当前图存为 `v000`(original)。
- 之后原路径上的图与所有版本都对不上(别的 Agent 重出、页面上裁剪翻转过)时,打开即收为一个 `external` 版本并成为当前最终版。
- 固定路径的图:`final` = 原路径上那一版。分镜背景图:`final` = 最近一次设为最终版的那一版。

## 编辑

页面发 `POST /api/v1/projects/<p>/canvas/edit`:`{file, parent, regions[], text, refs[参考图编号], provider, model, lock, mode: agent|direct, preview}`。

- **圈选**:矩形 / 椭圆 / 套索 / 画笔,页面只传几何(0..1 归一化坐标),蒙版与带圈标注图由服务端用 PIL 画(`render_mask` / `render_marked`)。每个区域带编号与一条说明,最多 12 个。
- **参考图**:托盘里每张有编号 n 和注释;文字里写 `@图n`(非中文界面 `@refn`)点名,拼提示词时换成 `[Image N]`。一次最多带 8 张。
- **图序固定**:`[Image 1]` 底图、`[Image 2]` 带圈标注图(有圈选时)、其后参考图。
- **发送方式**
  - 经 Agent(默认):建任务(状态 `drafting`)→ 派画板修图 Agent(`06-art/image-retouch`,智能分配固定低档)→ Agent 看图写一条英文提示词 → 跑 `code/canvas_edit.py submit --prompt-file …`。
  - 直接发送:宿主按模板拼提示词(`compose_prompt`:用户原话套进固定英文框架),后台线程跑 `canvas_edit.py submit --template`。
  - 发送前先看提示词(两种方式都可勾):任务停在 `awaiting_confirm`,页面显示提示词可改,确认后宿主跑 `submit --confirmed`;Agent 那条 submit 命令在这种任务上只存提示词、不出图。
- **出图**(`run_edit`):按任务里定的渠道 / 模型,请求尺寸 = 底图尺寸收进模型约束(`genmedia.fit_image_request`);模型返回的图按底图尺寸对位——**编辑不改分辨率**。
- **圈外锁定**(有圈选时默认开,`lock_outside`):只取圈内(蒙版扩一圈 + 羽化)贴回底图,圈外保持底图内容(多一次按原编码的重存,JPEG 质量 95)。
  同时算圈外平均差 `drift`;超过 `DRIFT_WARN`(0.06,启发式)标「圈外漂移」——模型把整张图挪了位置时圈内外接缝会对不上,页面提供「改用整图」另存一版(`unlock_version`)。
  模型返回图与底图画幅差超过 3% 时无法对位,不合成,版本标「画幅变了」。
- 用量:接口返回了 usage 的渠道(目前只有火山 / BytePlus)记在版本的 `usage` 上,历史里显示;其余渠道不显示。任务目录里的 `usage_ledger.jsonl` 与别处同一格式。
- 同一张图同时只跑一个任务(`active_job`);Agent 结束却没交提示词、或任务长时间没进展,下次读状态时记为失败。

## 放大

`POST …/canvas/upscale`:`{file, parent, mode: redraw|fidelity, provider, model, upscaler, width}`(高按画幅推)。宿主直接跑 `canvas_edit.py upscale`,不经 Agent。

- **重绘放大**:图像模型以底图为唯一参考图 + 固定提示词(`UPSCALE_PROMPT`)重出。模型会重画,人脸 / 文字 / 纹理可能走样。
- **保真超分**(`genmedia.upscale_image`):`local` 本机 Lanczos 插值(不新增细节),或 Fal 的专用超分端点(SeedVR2 / Topaz Precision / Real-ESRGAN / AuraSR / Bria,需要 Fal Key)。
  模型返回的图比目标大时宿主缩到目标尺寸;比目标小按实际尺寸存。
- 模型返回的图没有比底图大(面积 < 1.05 倍)判失败,不拿插值凑数。
- **该模型最大宽高**(`GET …/canvas/limits` → `genmedia.image_size_limits`):

| 渠道 / 模型 | 约束 | 依据 |
|---|---|---|
| 火山 / BytePlus Seedream 5.0 Pro | 总像素 ≤ 4,624,220(5.x 出图下限 3,686,400) | 实测 |
| 火山 / BytePlus Seedream 5.0 / 5.0 Lite / 4.5 / 4.0 | 总像素 ≤ 4096×4096(5.x 出图下限 3,686,400) | 实测 |
| Fal Seedream 5.0 Pro / Flash | 总像素 1024² 至 2048² | 参数表 |
| Fal Seedream 5.0 Lite / 4.5 | 总像素 ≤ 4096×4096 | 参数表 |
| Fal GPT Image 2 / 2.5 | 16 的倍数、长边 ≤ 3840、长宽比 ≤ 3:1、总像素 ≤ 8,294,400 | 参数表 |
| Fal Ideogram V4.5 | 32 的倍数、总像素 ≤ 2048² | 参数表 |
| Fal Nano Banana | 1K / 2K / 4K 档(按请求长边取档) | 参数表 |
| Fal FLUX.3、OpenRouter 目录给了分辨率档的模型 | 分辨率档(按请求面积取档) | 参数表 / 在线目录 |
| 其余(MiniMax、ComfyUI、Agentics、RH、目录没给档的 OpenRouter 模型、方舟自定义模型) | 未知 | — |

分档的模型实际像素由模型定,页面给的是「为落进该档而请求的尺寸」;上限未知的模型可以试,没按请求尺寸出图即失败。

## 调整(非 AI)

`POST …/canvas/transform`:`crop {x,y,w,h}`(页面用最后一个矩形区域)/ `flip_h` / `flip_v` / `rotate {deg: 90|180|270}` / `resize {width,height}`,各记为一个 `transform` 版本,按原编码写。

## 设为最终版

`POST …/canvas/adopt`:`{file, version, repoint, sync_setting}`。页面先用 `GET …/canvas/impact` 给出影响清单。

- **固定路径的图**(人物 / 服装 / 生物 / 道具 / 场景图 / 封面):把该版本写回原路径;原路径上的图若不在历史里先收为 `external`。
- **草图**:同上,并把草图台账该镜改为 `mode: canvas`、`canvas_version`。
- **分镜背景图**:原图与原条目不动——登记为 `<key>_revN`(`shot_plates.adopt_canvas_plate`:条目 `revised=True`、`pano_ref.kind='revision'`、`created_by.source='canvas'`,机位事实照抄原条目,`size` 按实际宽高),
  把引用原图(以及本画板此前登记过的各版)的分镜条目改指过去并 sync 组 prompt;`repoint=false` 只登记。选回已登记过的版本不新建条目;选原图那一版 = 改回原图。
- **影响清单**(`impact`):谁在用(分镜背景图查各集 `shot_plates.json`,其余查 `assets/prompts/ep*/grp*.json` 的 `refs`)+ 提示(已出视频不变 / 人像库需重新入库 / 动态样片需重出 / 发布包封面是副本)。
- **不重跑下游**。有分镜 / 组在用且确实换了版本时,写一条变更记录 `runs/revisions/canvas-<时间>.json`(`rerun_downstream=false`),总制片下次被唤醒时按它只标脏。
- **同步设定文字**(勾选,仅人物 / 服装 / 场景图 / 分镜背景图 / 生物 / 道具):把这次修改链的说明(`describe_changes`)发给修改师,只改设定文字、不重出图。

## 接口

`services/api/app.py` → `services/runtime/core.py api_canvas_*`:

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/projects/<p>/canvas?file=` | 页面数据(信息、版本、参考图、任务、渠道清单、保真超分可选项) |
| GET | `…/canvas/limits` | 所选模型在这张图画幅下的尺寸上限 |
| GET | `…/canvas/library` | 从项目库选参考图的候选 |
| GET | `…/canvas/impact` | 设为最终版的影响清单 |
| POST | `…/canvas/edit` `…/confirm` `…/upscale` `…/transform` `…/version` `…/adopt` | 见上 |
| POST | `…/canvas/refs`、`…/canvas/refs/upload` | 参考图托盘:选库图 / 改注释 / 移出;上传(请求体即图片字节) |

页面在有任务时每 2.5 秒轮询一次 GET。用户可见的错误文案中文界面出中文、其余出英文(`CanvasError.text`)。

## 没做 / 没验证

- 没有任何真实出图:各图像模型对带圈标注图的遵从程度、圈外漂移阈值 0.06 是否合适、Fal 各超分端点的实际返回,都没有实测。
- 真蒙版重绘(把 mask 发给支持的模型)、局部裁切重绘再贴回、扩图、多候选、套用到同场景其它背景图——未做。
- 分镜预览页的背景图 / 参考图、全景图没有接入口。
- 登记出去的 `_revN` 不带 master / grid9 标记:之后新跑的集自动选图仍选原图(与「✏️ 修改」重出的修订图同一局限)。
