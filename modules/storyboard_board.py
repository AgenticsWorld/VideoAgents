"""故事板(2026-09-11):分镜层(07-directing/storyboard)产物 storyboard.json 的归一化视图 + 分镜草图台账。

页面 /preview/board 与草图 CLI code/storyboard_sketch.py 共用本模块:
- load_board(base, ep):把各历史版本 storyboard.json(字段名多次演化)归一成同一结构,
  并按 shot_list.json 的 storyboard_ref 把定稿镜号/时长/台词对回草案镜;
- 草图台账 assets/storyboard/<ep>/index.json(schema storyboard_sketches/1.0):
  shots[<S01-01>] = {file, status(queued|running|done|failed), error, prompt, note, provider, model, refs, updated_at}
  (mode=hand_ai = 手绘稿经 AI 按草图风格重绘,layout 记手绘原稿路径,2026-09-19;mode=hand / provider=hand_drawn 表示故事板页「✍️ 手绘」手机画布直接落盘的草图,2026-09-16,不经 AI;重出时渠道回落偏好)
  图片 assets/storyboard/<ep>/<S01-01>.png;台账写入经 flock 串行,宿主后台任务与 Agent 的 CLI 进程可并发;
- build_prompt / collect_refs:草图提示词(铅笔手绘分镜风格,英文风格句 + 原文画面内容)与参考图
  (只带出场人物 sheet,缩到 512px 长边;不带场景图——俯视布局图会误导图像模型,场景只靠文字描述;
  也不用风格参考图,铅笔风格全靠提示词——2026-09-11 用户拍板);
  **人物优先**(2026-09-12 用户拍板;2026-09-29 起背景改「简化但空间准确」、画法改传统电影故事板,见 SKETCH_STYLE_PROMPT 注释:没有合适的场景参考图,草图弱化场景展现,主要按分镜表现
  镜头机位、人物比例、神态、动作):风格句要求人物线稿清晰、按景别画对人物比例、表情与肢体可读,背景只
  两三笔示意或留白;地点只留一句短提示放在最后,不再拼场景卡描述;从 content/sketch/action 文字里自动推导
  「Camera:」(角度/高度/镜头/朝向)与「Expressions:」(神态/视线)两句英文关键词加进提示词(camera_hint / expression_hint);
  **人物姿态/动作(2026-09-14 用户拍板)**:每镜再加一句「Body poses and actions:」——优先用分镜层结构化字段
  `shots_draft[].poses`(`{CHAR-id: {pose: stand|sit|lie|kneel|crouch|prone, action: "挥剑"}}`,pose 受控枚举由宿主映射成
  英文,action 中文直通不翻译),存量项目没有该字段时退回从 content/action/sketch 文字按关键词表推导(pose_hint);
  之前草图里人物站/坐/躺、奔跑/挥剑/闪躲全靠中文长散文,姿态词淹没在句中、宫格模式还会被截掉,模型基本不听;
- 宫格批量(2026-09-11 用户拍板,2026-09-12 由 3×3 降为 2×2):单集标题行「出草图」一次出一张 2×2 宫格图
  (≤4 镜,按集内顺序分批),split_grid 切成小图落到各镜的 <S01-01>.png(台账记 mode=grid + grid.file/cell);
  宫格原图存 _grids/。单镜「出图/重出」仍是单张出图。宫格切出的小图(约 1230×690)与单张(1280 长边)接近,
  动态样片按画布等比缩放统一。降到 2×2 的原因:3×3 每格约 820×460,画不出可读的表情,且逐格文字预算太小。
只读 storyboard.json / shot_list.json / bible 与概念图,不改任何分镜文件。
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from pathlib import Path

from modules.entity_ids import CHAR_ID_PAT, is_creature_id

ROOT = Path(__file__).resolve().parent.parent

SCHEMA_VERSION = "storyboard_sketches/1.0"
STORYBOARD_AGENT = "07-directing/storyboard"
DIRECTOR_AGENT = "07-directing/director"
SKETCH_AGENT = "07-directing/storyboard-sketch"
SKETCH_DIR_REL = "assets/storyboard/{ep}"
HAND_DRAWN_PROVIDER = "hand_drawn"           # 台账 provider 记号:故事板页「✍️ 手绘」直接落盘的草图(mode=hand),不是图像模型出的
REF_MAX_EDGE = 512           # 参考图缩放长边(草图只需形象/空间提示,不需要高清)
MAX_CAST_REFS = 3            # 人物参考图上限(多了反而稀释风格参考)
STATUSES = ("queued", "running", "done", "failed")

# 2026-09-12 用户拍板:人物优先——草图只为看机位、人物比例、神态、动作。
# 2026-09-29 用户拍板(草图向传统电影故事板靠拢):画法从「速写本」改为专业实拍电影分镜师的铅笔+灰马克笔
# (简化人物、脸几笔、2–3 级灰+少量纯黑分层与示光);背景从「留白」改为「简化但空间准确」——地平线/透视/
# 前中后景大块面交代机位高度与焦段,不画细节装饰。附图人物 sheet 只取发型/服装轮廓/体型,不学其渲染画风。
SKETCH_STYLE_PROMPT = (
    "Professional live-action film storyboard panel, drawn by an experienced studio storyboard artist in pencil "
    "and grey marker on white paper: fast, economical, confident gestural lines; figures built from simple solid "
    "shapes with correct anatomy; faces simplified to a few marks (brows, eyes, mouth) that still read the "
    "emotion clearly; two or three flat grey marker values plus a few spot blacks separate foreground, midground "
    "and background and show where the light comes from; silhouettes stay readable at thumbnail size. "
    "FIGURES FIRST: correct figure size and body cropping for the stated shot size, clear eye lines and body "
    "gestures. Pose every figure exactly as stated (standing, sitting, kneeling, crouching, lying down, running, "
    "swinging a weapon, dodging…); the body state is as important as the face. "
    "ENVIRONMENT SIMPLIFIED BUT SPATIALLY CORRECT: a clear horizon line and perspective that match the stated "
    "camera height and lens, big simple shapes for the main structures and any foreground framing element, depth "
    "staging readable at a glance; no small detail, no ornament, no texture work, no props unless mentioned. "
    "The framing must show the camera angle, camera height and lens exactly as described (eye level, high "
    "angle, low angle, over-the-shoulder, profile, from behind). "
    "Strictly black-and-white, no color. A single frame, no panel borders, no text, no captions, no speech "
    "bubbles, no watermark. It must look like a page from a feature film's storyboard, not a manga, anime or "
    "finished illustration. "
)
# 风格句结尾按有无参考图二选一(2026-09-17):有参考图 → 附图是人物设定;无参考图(comfyui 纯文生图)→ 人物按文字画
SKETCH_REFS_SENTENCE = (
    "The attached images are the project's official character designs — take from them only what identifies "
    "each character (hairstyle, costume shape, build, signature props); do not copy their rendering style, "
    "colors or facial detail — draw everyone in the simplified storyboard manner above. The location is "
    "described in text only and is drawn as simple shapes."
)
# 纯文生图整句风格提示(2026-09-17):不用 SKETCH_STYLE_PROMPT——Z-Image 这类 cfg=1 的本地模型不吃 negative,
# 会把风格句里列举的姿态(standing, sitting, kneeling…)和「no panel borders」之类否定句照字面画成多格姿势表;
# 这里只写正向描述、不列举姿态、不出现「panel/sheet/grid」字样。
SKETCH_TEXT_ONLY_TAIL = ("Characters are drawn from the text description alone (age, build, hairstyle, "
                         "outfit as stated); the location is described in text only and is drawn as simple shapes.")
# 上面这类渠道带参考图时的结尾(2026-09-19):同样只写正向句;逐张点名是谁(Qwen-Image-Edit 按 Picture N 认图),
# 并写单实例——设定 sheet 多视角同框,不写会把一个人画成几个。{who} 由 ref_sentence_plain 填。
SKETCH_REFS_SENTENCE_PLAIN = (
    "The attached pictures are character design sheets: {who}. Each sheet shows one single person from several "
    "views; in the drawing that person appears exactly once, recognisable by the hairstyle, costume shape and "
    "build of the sheet, drawn in the simplified storyboard manner described here, in the pose and framing "
    "described here. The location is described in text only and is drawn as simple shapes."
)
SKETCH_STYLE_PROMPT_TEXT_ONLY = (
    "One professional live-action film storyboard drawing filling the whole page, pencil and grey marker on white "
    "paper, drawn by an experienced studio storyboard artist: fast, economical, confident gestural lines; figures "
    "built from simple solid shapes with correct anatomy; faces simplified to a few marks that still read the "
    "emotion; two or three flat grey values plus a few spot blacks separate foreground, midground and background "
    "and show the light direction. It is a single moment from a film, seen through the camera once, drawn as one "
    "picture. FIGURES FIRST: correct figure size and body cropping for the stated shot size, clear eye lines and "
    "body gestures; each figure holds exactly the body position the shot describes. The environment is simplified "
    "but spatially correct: a clear horizon line and perspective matching the stated camera height and lens, big "
    "simple shapes for structures and foreground framing, plain surfaces. The framing shows the stated camera "
    "angle, camera height and lens. Strictly black-and-white, clean storyboard linework. " + SKETCH_TEXT_ONLY_TAIL
)
# 参考图按「图生图是否配置」决定的渠道(2026-09-19 用户拍板,取代 09-17/09-18 的一律纯文生图):
# comfyui 的单图原图重绘(img2img)模板,「参考图」是初始画面,传人物 sheet 会把构图
# 锁死成设定稿——仍不传、纯文生图;选了真正收参考图的图生图工作流(RunningHub 的 Qwen-Image-Edit / FLUX.2,
# 或本地 / Comfy Cloud 的多 LoadImage 参考链模板 comfy/image-flux2-dev-fp8-ref10-api.json:多个 LoadImage
# 作条件输入、空 latent)才传人物 sheet,张数以模板 LoadImage 个数封顶。agentics 同理:图生图 profile 收几张传几张,
# 没配或取不到详情走文生图 profile。容量判定见 genmedia.image_ref_capacity。
REF_CONDITIONAL_SKETCH_PROVIDERS = ("comfyui", "agentics")
# 手绘稿 AI 加工(2026-09-19,故事板页「✍️ 手绘」勾选「AI 加工」):手绘稿排在参考图**最后一张**作构图底
# (人物 sheet 的 Picture N 编号不变),按草图风格重绘;只写正向句,各渠道通用。
SKETCH_LAYOUT_SENTENCE = (
    "One exception among the attached pictures: the last one is not a character design but the director's own rough "
    "hand-drawn layout for this shot. Keep its composition "
    "exactly — framing, camera angle, where each figure sits in the frame, figure sizes and poses — and redraw it "
    "cleanly in the pencil storyboard style described above, with correct anatomy and readable faces; it is a "
    "layout guide only, its wobbly line quality is not the target."
)
# 白模机位构图底(2026-09-29):已导出白模 camera.mp4 的镜,可取本镜首帧作最后一张参考图(--whitebox-layout),
# 机位/焦段/地平线/人物画内位置与大小全按白模;彩色假人按身份色逐个点名。只写正向句,各渠道通用。
SKETCH_WHITEBOX_SENTENCE = (
    "One exception among the attached pictures: the last one is not a character design but a flat grey 3D blocking "
    "render of this exact camera setup{grid}. Keep its framing exactly — camera angle, camera height, lens, "
    "horizon position, where each figure stands in the frame and how big it is. The coloured mannequins are "
    "placeholders for the characters{who}. Replace every mannequin with that character drawn in the storyboard "
    "style, in the pose and action stated below, and turn the grey blocks into simple sketched environment "
    "shapes; its flat CG look is not the target."
)
HAND_DIR_REL = "assets/storyboard/{ep}/_hand"   # 手绘原稿存档(AI 加工的构图底;加工失败时回落为该镜草图)
# 宫格批量退化为逐镜单张的渠道:只有 comfyui(本地模型跟不了严格 2×2 排版);agentics 文生图仍出宫格(不带参考图)
SINGLE_ONLY_SKETCH_PROVIDERS = ("comfyui",)
SKETCH_NEGATIVE = ("color, colorful, photo, photorealistic, 3d render, cgi, painting, ink wash, anime cel, "
                   "manga, anime eyes, chibi, finished illustration, highly detailed rendering, dense cross-hatching, "
                   "screentone, ornate detail, "
                   "text, letters, caption, watermark, logo, speech bubble, comic panel grid, multiple panels, "
                   "border, frame lines, cluttered environment, architectural rendering, "
                   "interior design, landscape painting, scenery without people")
# 无人物出场的镜(空镜/定场)负面词去掉这段,否则把定场镜也压成必须有人
SKETCH_NEGATIVE_PEOPLE_TAIL = ", landscape painting, scenery without people"

GRID_MAX_PANELS = 4          # 一张宫格图最多 4 镜(2×2;2026-09-12 由 3×3/9 镜降下来:每格更大才画得出表情,逐格文字预算也更宽)
GRID_MAX_CAST_REFS = 4       # 宫格模式人物参考图上限(4 镜的出场并集,按出场次数取前几位)
GRID_CELL_TRIM = 0.02        # 切分时每格四边各裁掉 2%,去掉模型画的格线/留白
GRID_DIR_REL = "assets/storyboard/{ep}/_grids"
GRID_STYLE_PROMPT = (
    "A page from a feature film's storyboard: a strict {cols}x{rows} grid of {n} equal-size panels on one white page, "
    "{cols} columns and {rows} rows, separated only by thin straight black gutter lines, panels read left to right, "
    "top to bottom, every panel filling its cell edge to edge with the same {aspect} framing. Every panel is drawn by "
    "an experienced studio storyboard artist in pencil and grey marker: fast, economical, confident gestural lines; "
    "figures built from simple solid shapes with correct anatomy; faces simplified to a few marks that still read the "
    "emotion; two or three flat grey values plus a few spot blacks separate foreground, midground and background and "
    "show the light direction. FIGURES FIRST in every panel: correct figure size and body cropping for that panel's "
    "shot size, clear eye lines and body gestures; pose every figure exactly as that panel states (standing, "
    "sitting, kneeling, crouching, lying down, running, swinging, dodging…). ENVIRONMENT SIMPLIFIED BUT SPATIALLY "
    "CORRECT in every panel: a clear horizon line and perspective matching that panel's camera height and lens, big "
    "simple shapes for structures and foreground framing, no small detail or ornament. Each panel must show its "
    "camera angle, height and lens exactly as described. Strictly black-and-white, no color; storyboard drawing, not "
    "manga, anime or finished illustration. No text, no numbers, no captions, no speech bubbles, no watermark inside "
    "the panels. {refs}{blank}"
)
# 宫格风格句结尾按有无参考图二选一(2026-09-18):有参考图 → 附图是人物设定;无参考图(agentics 文生图)→ 人物按文字画
GRID_REFS_SENTENCE = (
    "The attached images are the project's official character designs — take from them only what identifies each "
    "character (hairstyle, costume shape, build, signature props) and keep it consistent in every panel; do not copy "
    "their rendering style, colors or facial detail. Locations are described in text only and are drawn as simple shapes."
)
GRID_TEXT_ONLY_SENTENCE = (
    "Characters are drawn from the text description alone (age, build, hairstyle, outfit as stated) and stay "
    "consistent across panels; locations are described in text only and are drawn as simple shapes."
)
GRID_NEGATIVE = ("color, colorful, photo, photorealistic, 3d render, cgi, painting, ink wash, anime cel, "
                 "manga, anime eyes, chibi, finished illustration, highly detailed rendering, dense cross-hatching, "
                 "screentone, ornate detail, "
                 "text, letters, numbers, caption, watermark, logo, speech bubble, uneven panels, overlapping panels, "
                 "panels of different sizes, decorative border, cluttered environment, "
                 "architectural rendering, interior design")

# ---------------- 草图画风包(2026-09-30 用户拍板) ----------------
# 故事板页顶栏「画风」下拉,存项目 settings.json output.sketch_style(默认 film);CLI --style 可临时覆盖。
# film    = 铅笔灰马克(实拍电影分镜,上面的 SKETCH_* / GRID_* 常量,2026-09-29 版);
# konte   = 铅笔彩铅:日式动画絵コンテ,铅笔快线 + 竖向平行排线铺调子 + 少量茶色/赭石彩铅点染,人物为简化动画造型;
# ink     = 粗犷线稿(2026-09-30):好莱坞实拍动作片分镜,黑铅笔/签字笔快速硬朗线、出轮廓的笔触、密集斜排线/交叉排线
#           堆重黑、强对比、写实成人比例,无灰调无彩色;
# digital = 灰调重点色(2026-09-30):平板数字速写,深灰数字铅笔线 + 2–3 级平涂灰,人物简化(点眼、一笔嘴)但动作清楚,
#           全图只有一处重点色落在本镜叙事关键物(道具/液体/火/血/光),其余灰阶;参考图里的运动箭头与手写字不让模型画。
#   四种都只用文字描述画风特征,不写作者/工作室/片名,不传任何风格参考图(用户给的扫描页只作画风参考,不进 refs)。
SKETCH_STYLES = ("film", "konte", "ink", "digital")
SKETCH_STYLE_LABELS = {"film": "铅笔灰马克", "konte": "铅笔彩铅", "ink": "粗犷线稿", "digital": "灰调重点色"}
DEFAULT_SKETCH_STYLE = "film"
_KONTE_LOOK = (
    "soft graphite pencil on white paper, drawn by a veteran animation director: quick, lively, confident pencil "
    "lines, slightly rough and searching; characters in a clean simplified anime layout style with correct anatomy "
    "and clearly readable expressions; tone laid in with loose parallel vertical pencil hatching instead of smooth "
    "shading; a few light touches of warm sepia / ochre colored pencil on shadows, skin and key objects, everything "
    "else graphite"
)
KONTE_STYLE_PROMPT = (
    "Japanese animation production storyboard (e-konte) panel, " + _KONTE_LOOK + ". "
    "FIGURES FIRST: correct figure size and body cropping for the stated shot size, clear eye lines and body "
    "gestures. Pose every figure exactly as stated (standing, sitting, kneeling, crouching, lying down, running, "
    "swinging a weapon, dodging…); the body state is as important as the face. "
    "ENVIRONMENT SKETCHED BUT SPATIALLY CORRECT: a horizon line and perspective that match the stated camera height "
    "and lens; trees, walls, rocks and ground indicated with a few loose lines and vertical hatching; no finished "
    "rendering, no props unless mentioned. "
    "The framing must show the camera angle, camera height and lens exactly as described (eye level, high "
    "angle, low angle, over-the-shoulder, profile, from behind). "
    "A single frame, no panel borders, no text, no handwriting, no cut numbers, no captions, no speech bubbles, "
    "no watermark. It must look like a hand-drawn animation storyboard page, not a finished illustration, "
    "painting or colored manga. "
)
KONTE_REFS_SENTENCE = (
    "The attached images are the project's official character designs — keep each character's likeness, "
    "hairstyle, costume shape and signature props, redrawn in the loose pencil storyboard manner above (graphite "
    "with a little sepia pencil, no full color). The location is described in text only and is drawn as simple shapes."
)
KONTE_STYLE_PROMPT_TEXT_ONLY = (
    "One Japanese animation production storyboard drawing (e-konte) filling the whole page, " + _KONTE_LOOK + ". "
    "It is a single moment from a film, seen through the camera once, drawn as one picture. FIGURES FIRST: correct "
    "figure size and body cropping for the stated shot size, clear eye lines and body gestures; each figure holds "
    "exactly the body position the shot describes. The environment is sketched but spatially correct: a horizon "
    "line and perspective matching the stated camera height and lens, trees, walls and ground indicated with loose "
    "lines and vertical hatching. The framing shows the stated camera angle, camera height and lens. Graphite "
    "pencil with light sepia accents, clean hand-drawn storyboard look. " + SKETCH_TEXT_ONLY_TAIL
)
KONTE_NEGATIVE = ("full color, saturated colors, color painting, photo, photorealistic, 3d render, cgi, painting, "
                  "ink wash, digital illustration, finished illustration, cel shading, screentone, clean vector lineart, "
                  "text, letters, handwriting, numbers, caption, watermark, logo, speech bubble, comic panel grid, "
                  "multiple panels, border, frame lines, cluttered environment, architectural rendering, "
                  "interior design" + SKETCH_NEGATIVE_PEOPLE_TAIL)
KONTE_GRID_STYLE_PROMPT = (
    "A page from a Japanese animation production storyboard (e-konte): a strict {cols}x{rows} grid of {n} equal-size "
    "panels on one white page, {cols} columns and {rows} rows, separated only by thin straight black gutter lines, "
    "panels read left to right, top to bottom, every panel filling its cell edge to edge with the same {aspect} "
    "framing. Every panel: " + _KONTE_LOOK + ". FIGURES FIRST in every panel: correct figure size and body cropping "
    "for that panel's shot size, clear eye lines and body gestures; pose every figure exactly as that panel states "
    "(standing, sitting, kneeling, crouching, lying down, running, swinging, dodging…). ENVIRONMENT SKETCHED BUT "
    "SPATIALLY CORRECT in every panel: horizon and perspective matching that panel's camera height and lens, loose "
    "lines and vertical hatching, no finished rendering. Each panel must show its camera angle, height and lens "
    "exactly as described. Graphite pencil with only light sepia accents, no full color; hand-drawn storyboard, not "
    "a finished illustration. No text, no handwriting, no numbers, no captions, no speech bubbles, no watermark "
    "inside the panels. {refs}{blank}"
)
KONTE_GRID_REFS_SENTENCE = (
    "The attached images are the project's official character designs — keep each character's likeness, hairstyle, "
    "costume shape and signature props consistent in every panel, redrawn in the loose pencil storyboard manner "
    "(graphite with a little sepia pencil, no full color). Locations are described in text only and are drawn as simple shapes."
)
KONTE_GRID_NEGATIVE = ("full color, saturated colors, color painting, photo, photorealistic, 3d render, cgi, painting, "
                       "ink wash, digital illustration, finished illustration, cel shading, screentone, clean vector "
                       "lineart, text, letters, handwriting, numbers, caption, watermark, logo, speech bubble, uneven "
                       "panels, overlapping panels, panels of different sizes, decorative border, cluttered environment, "
                       "architectural rendering, interior design")


def _style_pack(intro: str, look: str, env: str, env_plain: str, finish: str, finish_plain: str,
                refs_manner: str, negative: str, grid_negative: str, accent: bool = False) -> dict:
    """按「画法 look / 环境 env / 收尾 finish」拼一套画风包(单张/纯文生图/宫格三种风格句 + 两种参考图句 + 负面词)。
    纯文生图版(text_only)只用正向句 env_plain / finish_plain(cfg=1 蒸馏模型不吃否定句)。"""
    figures = ("correct figure size and body cropping for the stated shot size, clear eye lines and body gestures")
    return {
        "head": (f"{intro} panel, {look}. FIGURES FIRST: {figures}. Pose every figure exactly as stated (standing, "
                 "sitting, kneeling, crouching, lying down, running, swinging a weapon, dodging…); the body state is as "
                 f"important as the face. ENVIRONMENT {env}; no props unless mentioned. The framing must show the camera "
                 "angle, camera height and lens exactly as described (eye level, high angle, low angle, over-the-shoulder, "
                 "profile, from behind). A single frame, no panel borders, no text, no handwriting, no captions, no arrows, "
                 f"no speech bubbles, no watermark. {finish} "),
        "refs": ("The attached images are the project's official character designs — take from them only what identifies "
                 "each character (hairstyle, costume shape, build, signature props); do not copy their rendering style, "
                 f"colors or facial detail — draw everyone in the {refs_manner} described above. The location is described "
                 "in text only and is drawn as simple shapes."),
        "text_only": (f"One {intro} drawing filling the whole page, {look}. It is a single moment from a film, seen through "
                      f"the camera once, drawn as one picture. FIGURES FIRST: {figures}; each figure holds exactly the body "
                      f"position the shot describes. The environment is {env_plain}. The framing shows the stated camera "
                      f"angle, camera height and lens. {finish_plain} " + SKETCH_TEXT_ONLY_TAIL),
        "negative": negative + SKETCH_NEGATIVE_PEOPLE_TAIL,
        "grid_head": ("A page of " + intro + " panels: a strict {cols}x{rows} grid of {n} equal-size panels on one white "
                      "page, {cols} columns and {rows} rows, separated only by thin straight black gutter lines, panels read "
                      "left to right, top to bottom, every panel filling its cell edge to edge with the same {aspect} framing. "
                      f"Every panel: {look}. FIGURES FIRST in every panel: {figures}; pose every figure exactly as that panel "
                      "states (standing, sitting, kneeling, crouching, lying down, running, swinging, dodging…). ENVIRONMENT "
                      f"in every panel {env}. Each panel must show its camera angle, height and lens exactly as described. "
                      f"{finish} No text, no handwriting, no numbers, no captions, no arrows, no speech bubbles, no watermark "
                      "inside the panels. {refs}{blank}"),
        "grid_refs": ("The attached images are the project's official character designs — take from them only what identifies "
                      "each character (hairstyle, costume shape, build, signature props) and keep it consistent in every "
                      f"panel; do not copy their rendering style, colors or facial detail — use the {refs_manner}. Locations "
                      "are described in text only and are drawn as simple shapes."),
        "grid_negative": grid_negative,
        "accent": accent,       # 逐镜重点色(灰调重点色画风):每镜/每格另拼 accent_sentence
    }


_INK_PACK = _style_pack(
    intro="live-action action-movie storyboard",
    look=("drawn by a veteran Hollywood storyboard artist in black pencil and felt-tip pen on white paper: fast, "
          "aggressive, angular gestural strokes that overshoot their contours, bold heavy blacks built from dense diagonal "
          "and cross hatching, strong contrast, realistic adult proportions, faces rendered in a few confident strokes, "
          "energetic and cinematic"),
    env=("DRAWN WITH ENERGY BUT SPATIALLY CORRECT: strong perspective matching the stated camera height and lens, sets and "
         "machinery indicated with quick hard strokes and dark hatched masses, depth read through contrast"),
    env_plain=("drawn with energy and spatially correct: strong perspective matching the stated camera height and lens, "
               "sets indicated with quick hard strokes and dark hatched masses"),
    finish=("Pure black line and hatching on white, no grey wash, no color; a raw professional storyboard drawing, not a "
            "finished illustration, manga or anime."),
    finish_plain="Pure black pencil and ink line with hatching on white, raw professional storyboard energy.",
    refs_manner="realistic proportions and rough ink storyboard manner",
    negative=("color, colorful, grey wash, soft shading, photo, photorealistic, 3d render, cgi, painting, manga, anime, "
              "anime eyes, chibi, finished illustration, clean vector lineart, screentone, text, letters, handwriting, "
              "numbers, caption, watermark, logo, speech bubble, arrows, comic panel grid, multiple panels, border, "
              "frame lines, interior design"),
    grid_negative=("color, colorful, grey wash, soft shading, photo, photorealistic, 3d render, cgi, painting, manga, anime, "
                   "anime eyes, chibi, finished illustration, clean vector lineart, screentone, text, letters, handwriting, "
                   "numbers, caption, watermark, logo, speech bubble, arrows, uneven panels, overlapping panels, panels of "
                   "different sizes, decorative border"),
)
_DIGITAL_PACK = _style_pack(
    intro="digital film storyboard sketch",
    look=("drawn by a film storyboard artist on a tablet: loose dark-grey digital pencil lines, flat grey marker tones in "
          "two or three values, simplified cartoon-like figures with minimal faces (dot eyes, a single line for the mouth) "
          "whose poses and gestures read instantly, everything greyscale except at most one accent color placed only on "
          "the element the shot's Accent line names"),
    env=("SIMPLE BUT SPATIALLY CORRECT: walls, doorways, floors and furniture as flat grey planes with clean perspective "
         "matching the stated camera height and lens, depth readable at a glance"),
    env_plain=("simple and spatially correct: walls, doorways and floors as flat grey planes with clean perspective "
               "matching the stated camera height and lens"),
    finish=("Greyscale apart from the stated accent; a clean, readable digital storyboard, not a painting or a "
            "finished illustration."),
    finish_plain="Greyscale drawing apart from the stated accent, clean and readable digital storyboard.",
    refs_manner="simplified digital storyboard manner",
    negative=("full color, colorful, multiple accent colors, rainbow colors, photo, photorealistic, 3d render, cgi, "
              "painting, detailed rendering, anime eyes, manga, screentone, text, letters, handwriting, numbers, caption, "
              "watermark, logo, speech bubble, arrows, comic panel grid, multiple panels, border, frame lines, "
              "cluttered environment"),
    grid_negative=("full color, colorful, multiple accent colors, rainbow colors, photo, photorealistic, 3d render, cgi, "
                   "painting, detailed rendering, anime eyes, manga, screentone, text, letters, handwriting, numbers, "
                   "caption, watermark, logo, speech bubble, arrows, uneven panels, overlapping panels, panels of "
                   "different sizes, decorative border"),
    accent=True,
)

STYLE_PACKS = {
    "film": {"head": SKETCH_STYLE_PROMPT, "refs": SKETCH_REFS_SENTENCE, "text_only": SKETCH_STYLE_PROMPT_TEXT_ONLY,
             "negative": SKETCH_NEGATIVE, "grid_head": GRID_STYLE_PROMPT, "grid_refs": GRID_REFS_SENTENCE,
             "grid_negative": GRID_NEGATIVE},
    "konte": {"head": KONTE_STYLE_PROMPT, "refs": KONTE_REFS_SENTENCE, "text_only": KONTE_STYLE_PROMPT_TEXT_ONLY,
              "negative": KONTE_NEGATIVE, "grid_head": KONTE_GRID_STYLE_PROMPT, "grid_refs": KONTE_GRID_REFS_SENTENCE,
              "grid_negative": KONTE_GRID_NEGATIVE},
    "ink": _INK_PACK,
    "digital": _DIGITAL_PACK,
}


def normalize_sketch_style(v) -> str:
    v = str(v or "").strip().lower()
    return v if v in SKETCH_STYLES else DEFAULT_SKETCH_STYLE


def resolve_sketch_style(base: Path) -> str:
    """项目草图画风(settings.json output.sketch_style);缺省/非法 = film。"""
    cfg = _read_json(base / "settings.json") or {}
    return normalize_sketch_style((cfg.get("output") or {}).get("sketch_style"))


_REF_RE = re.compile(r"^(.+?)(?:/shots_draft)?/order:(\d+)(?:/split:[^/]+)?$")
_DLG_RE = re.compile(r"^\s*(?:S\d+[A-Za-z]?\s*[/·:\-]\s*)?(" + CHAR_ID_PAT + r"|NARRATOR|[^:：/·「」]{1,12})\s*[:：]\s*(.+?)\s*$")   # slug 编号见 entity_ids(#117)


# ---------------- 通用 ----------------

def _read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def shot_key(scene_no: str, order) -> str:
    """草图台账键 / 文件名:S01-01(scene_no 去空白,序号两位)。"""
    sn = re.sub(r"[^\w\-]", "", str(scene_no or "S0"))
    try:
        return f"{sn}-{int(order):02d}"
    except Exception:
        tail = re.sub(r"[^\w]", "", str(order))
        return f"{sn}-{tail}"


def sketch_dir(base: Path, ep: str) -> Path:
    return base / SKETCH_DIR_REL.format(ep=ep)


def index_path(base: Path, ep: str) -> Path:
    return sketch_dir(base, ep) / "index.json"


def load_index(base: Path, ep: str) -> dict:
    d = _read_json(index_path(base, ep)) or {}
    if not isinstance(d.get("shots"), dict):
        d = {"schema": SCHEMA_VERSION, "ep": ep, "shots": {}}
    d.setdefault("schema", SCHEMA_VERSION)
    d.setdefault("ep", ep)
    return d


def update_index(base: Path, ep: str, key: str, patch: dict | None, remove: bool = False) -> dict:
    """读-改-写台账一条(flock 串行;宿主线程与 Agent 的 CLI 进程可并发)。返回改后的整份台账。"""
    p = index_path(base, ep)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = p.with_suffix(".lock")
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            d = load_index(base, ep)
            if remove:
                d["shots"].pop(key, None)
            else:
                rec = d["shots"].get(key) or {}
                rec.update(patch or {})
                rec["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                d["shots"][key] = rec
            d["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1))
            os.replace(tmp, p)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    return d


# ---------------- 用户注释(2026-09-15):故事板页「🗒 注释」,分镜设计的参考 ----------------
NOTES_REL = "directing/{ep}/storyboard_notes.json"
NOTES_SCHEMA = "storyboard_notes/1.0"
NOTE_MAX_CHARS = 2000
_NOTE_KEY_RE = re.compile(r"^(\*|[A-Za-z0-9_\-]{1,40})$")     # "*" 整集 / "S01" 场次 / "S01-03" 镜


def notes_path(base: Path, ep: str) -> Path:
    return base / NOTES_REL.format(ep=ep)


def note_key_ok(key: str) -> bool:
    return bool(_NOTE_KEY_RE.match(key or ""))


def note_level(key: str) -> str:
    """注释键的层级:episode(*)/ scene(S01)/ shot(S01-03)。"""
    if key == "*":
        return "episode"
    return "shot" if "-" in key else "scene"


def load_notes(base: Path, ep: str) -> dict:
    """{schema, ep, notes: {key: {text, level, updated_at, scene_no?, order?, shot_id?, content?}}}。文件缺失/坏 → 空。"""
    d = _read_json(notes_path(base, ep)) or {}
    if not isinstance(d.get("notes"), dict):
        d = {"schema": NOTES_SCHEMA, "ep": ep, "notes": {}}
    d.setdefault("schema", NOTES_SCHEMA)
    d.setdefault("ep", ep)
    d["notes"] = {k: v for k, v in d["notes"].items() if isinstance(v, dict) and (v.get("text") or "").strip()}
    return d


def update_note(base: Path, ep: str, key: str, text: str, meta: dict | None = None) -> dict:
    """写/改/删一条注释(flock 串行,与台账同一套路);text 空 = 删除。返回改后的整份文件内容。
    meta(scene_no/order/shot_id/content 等)随条目存盘:分镜重做后镜序可能变,Agent 靠这些字段对回原镜。"""
    if not note_key_ok(key):
        raise ValueError(f"bad note key: {key!r}")
    text = (text or "").strip()
    if len(text) > NOTE_MAX_CHARS:
        raise ValueError(f"note too long (>{NOTE_MAX_CHARS} chars)")
    p = notes_path(base, ep)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = p.with_suffix(".lock")
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            d = load_notes(base, ep)
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            if not text:
                d["notes"].pop(key, None)
            else:
                rec = d["notes"].get(key) or {"created_at": now}
                rec.update({k: v for k, v in (meta or {}).items() if v not in (None, "")})
                rec.update({"text": text, "level": note_level(key), "updated_at": now})
                d["notes"][key] = rec
            d["updated_at"] = now
            d["_readme"] = ("用户在「📋 故事板」页写的注释,按键分三级:* = 整集,S01 = 场次,S01-03 = 场次-草案镜序。"
                            "分镜师(storyboard)重做本集分镜、镜头表工位(shot-planning)定稿镜头表、修改师改分镜时,"
                            "必须先读本文件,把每条注释当作用户对分镜设计的意见/约束:能落实的落实,不能落实的在汇报里说明原因。"
                            "镜级条目带 scene_no/order/shot_id/content(写注释时那一镜的内容摘要),重拆镜后镜序变了就按 content 对回原镜。")
            if not d["notes"]:
                p.unlink(missing_ok=True)
                d["notes"] = {}
            else:
                tmp = p.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1))
                os.replace(tmp, p)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    return d


def clear_notes(base: Path, ep: str) -> int:
    """「提交注释」后清空本集全部注释(2026-09-26):删 storyboard_notes.json(flock 串行),返回清掉的条数。
    注释正文已随修改单进入修改师的对话记录,文件不留副本。"""
    p = notes_path(base, ep)
    lock = p.with_suffix(".lock")
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            n = len(load_notes(base, ep)["notes"])
            p.unlink(missing_ok=True)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    return n


def note_meta(board: dict, key: str) -> dict:
    """按注释键从归一化故事板取定位元数据(镜级:scene_no/order/shot_id/content 摘要;场级:scene_no/location)。"""
    if key == "*":
        return {"title": board.get("title") or ""}
    for sc in board.get("scenes") or []:
        if key == sc.get("scene_no"):
            return {"scene_no": sc["scene_no"], "scene_id": sc.get("scene_id") or "", "location": sc.get("location") or ""}
        for sh in sc.get("shots") or []:
            if sh.get("key") == key:
                return {"scene_no": sc["scene_no"], "order": sh.get("order"), "shot_id": sh.get("shot_id") or "",
                        "content": (sh.get("content") or "")[:80]}
    return {}


# ---------------- 资产索引(人物/场景/生物/道具 名字 + 缩略图) ----------------

def _first_image(d: Path, prefer: tuple[str, ...] = ()) -> Path | None:
    if not d.is_dir():
        return None
    for name in prefer:
        f = d / name
        if f.is_file():
            return f
    for f in sorted(d.iterdir()):
        if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") \
                and not f.name.startswith(("_", ".")):
            return f
    return None


def scene_text(base: Path, sid: str) -> str:
    """草图提示词用的场景文字(替代场景图):取场景卡 architecture.json 的建筑语言/空间类型/材质/细节,
    拼成一段 ≤360 字;没有建筑卡返回空(调用方回落 index 的 description)。"""
    a = _read_json(base / "bible" / "scenes" / sid / "architecture.json") or {}
    if not a:
        return ""
    parts = []
    for v in (a.get("arch_style"), (a.get("space_type") or {}).get("detail") if isinstance(a.get("space_type"), dict) else a.get("space_type"),
              a.get("form"), a.get("scale")):
        if isinstance(v, str) and v.strip():
            parts.append(v.strip().rstrip("。;;"))
    mats = [m for m in (a.get("materials") or []) if isinstance(m, str)][:5]
    if mats:
        parts.append("材质: " + "、".join(mats))
    dets = [d for d in (a.get("details") or []) if isinstance(d, str)]
    if dets:
        parts.append(dets[0])
    return "; ".join(parts)[:360]


def asset_catalog(base: Path) -> dict:
    """{characters:{id:{name,file}}, scenes:{...}, creatures:{...}, props:{...}};file 为项目相对路径(缺图为 None)。"""
    out: dict[str, dict] = {"characters": {}, "scenes": {}, "creatures": {}, "props": {}}
    cidx = _read_json(base / "bible" / "characters" / "index.json") or {}
    for c in cidx.get("characters", []) or []:
        if isinstance(c, dict) and c.get("id"):
            out["characters"][c["id"]] = {"name": c.get("canonical_name") or c.get("name") or c["id"]}
    sidx = _read_json(base / "bible" / "scenes" / "index.json") or {}
    for s in sidx.get("scenes", []) or []:
        if isinstance(s, dict) and s.get("id"):
            out["scenes"][s["id"]] = {"name": s.get("name") or s["id"], "type": s.get("type") or "",
                                     "description": scene_text(base, s["id"]) or str(s.get("description") or "")[:200]}
    cr = _read_json(base / "bible" / "creatures" / "index.json") or {}
    for c in cr.get("creatures") or cr.get("entries") or []:      # 有的项目(liaozhai3)生物库顶层键是 entries
        if isinstance(c, dict) and c.get("id"):
            out["creatures"][c["id"]] = {"name": c.get("name") or c["id"]}
    pr = _read_json(base / "bible" / "props.json") or {}
    plist = pr.get("props") if isinstance(pr, dict) else pr
    for p in plist or []:
        if isinstance(p, dict) and p.get("id"):
            out["props"][p["id"]] = {"name": p.get("name") or p["id"]}
    conc = base / "assets" / "concepts"
    for kind, sub, prefer in (("characters", "characters", ("sheet.png",)),
                              ("creatures", "creatures", ("sheet.png",)),
                              ("props", "props", ("main_01.png", "main.png")),
                              ("scenes", "scenes", ("layout_top.png", "grid_9views.png"))):
        d = conc / sub
        if not d.is_dir():
            continue
        for sd in sorted(d.iterdir()):
            if not sd.is_dir() or sd.name.startswith("."):
                continue
            f = None
            if kind == "scenes":
                # 场景缩略:优先该场景库的分镜背景图(实拍视角)→ 俯视布局图 → 九宫格
                pidx = _read_json(sd / "plates" / "index.json") or {}
                for p in pidx.get("plates") or []:
                    if isinstance(p, dict) and p.get("file") and (base / p["file"]).is_file():
                        f = base / p["file"]
                        break
            f = f or _first_image(sd, prefer)
            rec = out[kind].setdefault(sd.name, {"name": sd.name})
            rec["file"] = f.relative_to(base).as_posix() if f else None
    for kind in out:
        for rec in out[kind].values():
            rec.setdefault("file", None)
    return out


# ---------------- storyboard.json 归一化 ----------------

def _first(d: dict, *keys, default=None):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return [x for x in v if x not in (None, "")]
    return [v]


def _line_delivery(ln: dict) -> dict:
    """台词行的演法三项(2026-10-03 故事板页台词后显示):情绪 emotion、语速档 pace(fast|medium|slow,
    缺则取 delivery.pace)、估时 est_s(est_duration_s)。缺的项为 None,前端只显示有的。"""
    dv = ln.get("delivery") if isinstance(ln.get("delivery"), dict) else {}
    return {"emotion": ln.get("emotion") or dv.get("emotion") or None,
            "pace": ln.get("pace") or dv.get("pace") or None,
            "est_s": ln.get("est_duration_s", ln.get("est_s"))}


def parse_dialogue_ref(v, names: dict) -> list[dict]:
    """草案镜 dialogue_ref 的几种历史写法 → [{speaker, name, text}] / [{ref}]:
    "S01/CHAR-0002:施主,请留步!" · "S01 · CHAR-0010:「食馎饦否?」(est 1.5s)" · "S04-D01" / "ep01-s01-d001"(仅编号)"""
    rows = []
    for item in _as_list(v):
        if isinstance(item, dict):
            sp = item.get("speaker") or item.get("char") or ""
            txt = item.get("text") or item.get("line") or ""
            if txt:
                rows.append({"speaker": sp, "name": names.get(sp, sp), "text": txt, **_line_delivery(item)})
            elif item.get("id") or item.get("ref"):
                rows.append({"ref": item.get("id") or item.get("ref")})
            continue
        s = str(item).strip()
        m = _DLG_RE.match(s)
        if m and (m.group(1).startswith("CHAR-") or m.group(1) == "NARRATOR" or m.group(1) in names.values()):
            sp = m.group(1)
            txt = re.sub(r"\s*[((]est\s*[\d.]+s[))]\s*$", "", m.group(2)).strip()
            txt = re.sub(r"^[「『\"]|[」』\"]$", "", txt)
            sid = sp if sp.startswith("CHAR-") or sp == "NARRATOR" else next((k for k, n in names.items() if n == sp), sp)
            rows.append({"speaker": sid, "name": names.get(sid, sp), "text": txt})
        elif re.match(r"^[\w\-]+$", s):
            rows.append({"ref": s})
        else:
            rows.append({"speaker": "", "name": "", "text": s})
    return rows


def _shot_list_map(sl: dict) -> dict[tuple[str, int], list[dict]]:
    """shot_list shots[] 按 storyboard_ref "S01/order:1"(含 /split:a 与 /shots_draft/ 变体)→ 草案镜 (scene_no, order)。"""
    m: dict[tuple[str, int], list[dict]] = {}
    for s in sl.get("shots") or []:
        if not isinstance(s, dict):
            continue
        r = _REF_RE.match(str(s.get("storyboard_ref") or ""))
        if not r:
            continue
        m.setdefault((r.group(1), int(r.group(2))), []).append(s)
    return m


def board_scene_no(sc: dict, i: int) -> str:
    """storyboard.json 场块的场次号(S01 式):scene_no 缺则回落 screenplay_ref / no / 序号。"""
    return str(_first(sc, "scene_no", "screenplay_ref", "no", default=f"S{i + 1:02d}"))


def _scene_drafts(sc: dict) -> list:
    return sc.get("shots_draft") if isinstance(sc.get("shots_draft"), list) else \
        (sc.get("shots") if isinstance(sc.get("shots"), list) else [])


def _draft_order(d: dict, j: int) -> int:
    order = d.get("order") if d.get("order") is not None else j + 1
    try:
        return int(order)
    except Exception:
        return j + 1


def board_targets(sb: dict) -> tuple[list[str], set[str]]:
    """跨预览页跳转用(2026-09-12):storyboard.json 里实际存在的 (场次号列表, 草案镜键集合 S01-03);
    剧本/分镜预览只对存在的目标显示「📋 故事板」链接。"""
    raw = sb.get("scenes") if isinstance(sb.get("scenes"), list) else []
    nos: list[str] = []
    keys: set[str] = set()
    for i, sc in enumerate(raw):
        if not isinstance(sc, dict):
            continue
        no = board_scene_no(sc, i)
        nos.append(no)
        for j, d in enumerate(_scene_drafts(sc)):
            if isinstance(d, dict):
                keys.add(shot_key(no, _draft_order(d, j)))
    return nos, keys


def load_board(base: Path, ep: str, catalog: dict | None = None) -> dict:
    """归一化的故事板:{title, scenes[], totals, has_storyboard, shot_list}。scenes[].shots[] 是页面表格的行。"""
    sb = _read_json(base / "directing" / ep / "storyboard.json") or {}
    sl = _read_json(base / "directing" / ep / "shot_list.json") or {}
    cat = catalog or asset_catalog(base)
    names = {k: v["name"] for k, v in cat["characters"].items()}
    names.update({k: v["name"] for k, v in cat["creatures"].items()})
    slmap = _shot_list_map(sl)
    scenes = []
    raw_scenes = sb.get("scenes") if isinstance(sb.get("scenes"), list) else []
    for i, sc in enumerate(raw_scenes):
        if not isinstance(sc, dict):
            continue
        scene_no = board_scene_no(sc, i)
        sid = sc.get("scene_id") or ""
        drafts = _scene_drafts(sc)
        shots, cast_union, groups = [], [], sc.get("groups_draft") if isinstance(sc.get("groups_draft"), list) else []
        creatures, props = [], []
        # 组草案的出场角色 → 按 shot_orders/shot_ids 落到镜(有的项目如 liaozhai2 只在组上写 characters,镜上没有 cast)
        grp_cast_by_order: dict[int, list] = {}
        grp_cast_by_id: dict[str, list] = {}
        for g in groups:
            if isinstance(g, dict):
                for c in _as_list(g.get("creatures")) + _as_list(g.get("creatures_union")):
                    if isinstance(c, str) and c not in creatures:
                        creatures.append(c)
                gcast = [c for c in _as_list(_first(g, "characters", "cast", "cast_ids", default=[])) if isinstance(c, str)]
                for o in _as_list(g.get("shot_orders")):
                    try:
                        grp_cast_by_order.setdefault(int(o), []).extend(gcast)
                    except Exception:
                        pass
                for sid_ in _as_list(_first(g, "shot_ids", "shots", default=[])):
                    if isinstance(sid_, str):
                        grp_cast_by_id.setdefault(sid_, []).extend(gcast)
        for j, d in enumerate(drafts):
            if not isinstance(d, dict):
                continue
            order = _draft_order(d, j)
            cast = [c for c in _as_list(_first(d, "cast", "characters", "cast_ids", default=[])) if isinstance(c, str)]
            finals = slmap.get((scene_no, order)) or []
            # 本场出场生物(2026-09-17):组草案之外,镜草案 / 镜头表定稿镜上登记的也算(有的项目只在镜上写 creatures)
            for c in _as_list(d.get("creatures")) + [x for f in finals for x in _as_list(f.get("creatures"))]:
                if isinstance(c, str) and c not in creatures:
                    creatures.append(c)
            # 镜草案显式写了空的出场名单(键存在、值为 [])= 刻意的空镜/插入镜,不回退到镜头表或组草案的人物(#105)
            no_people = not cast and any(isinstance(d.get(k), list) and not d[k] for k in ("cast", "characters", "cast_ids"))
            if not cast and not no_people:   # 镜上没写 → 镜头表定稿镜的 characters → 所属组草案的 characters
                for f in finals:
                    for c in _as_list(_first(f, "characters", "cast", default=[])):
                        if isinstance(c, str) and c not in cast:
                            cast.append(c)
            if not cast and not no_people:
                for c in grp_cast_by_order.get(order, []) + grp_cast_by_id.get(str(d.get("shot_id") or ""), []):
                    if c not in cast:
                        cast.append(c)
            for c in cast:
                if c not in cast_union:
                    cast_union.append(c)
            for a in _as_list(_first(d, "asset_refs", "props", default=[])):
                pid = a.get("id") if isinstance(a, dict) else a
                if isinstance(pid, str) and pid.startswith("PROP-") and pid not in props:
                    props.append(pid)
            dialogue = []
            for f in finals:
                for ln in f.get("dialogue_lines") or []:
                    if isinstance(ln, dict) and ln.get("text"):
                        sp = ln.get("speaker") or ""
                        dialogue.append({"speaker": sp, "name": names.get(sp, sp), "text": ln["text"],
                                         **_line_delivery(ln)})
            if not dialogue:
                dialogue = parse_dialogue_ref(_first(d, "dialogue_ref", "dialogue", "lines", default=None), names)
            key = shot_key(scene_no, order)
            shots.append({
                "key": key, "order": order,
                "shot_id": d.get("shot_id") or d.get("shot_id_ref") or "",
                "size_hint": _first(d, "size_hint", "size", "size_code", default=""),
                "content": str(_first(d, "content", "subject_action", "description", default="")),
                "action": str(_first(d, "action", default="")),
                "sketch": str(_first(d, "sketch", "composition_sketch", "camera_intent", "camera_movement_intent", default="")),
                "duration_hint_s": _first(d, "duration_hint_s", "duration_s", default=None),
                "cast": cast,
                "poses": normalize_poses(_first(d, "poses", "figure_states", default=None)),
                "extras": str(_first(d, "extras", default="")),
                # NPC 参与构图(2026-10-09,modules/npc_staging):无名氛围层 [{layer: fg|mg|bg, what}],与剧本点名的群演 extras 分开
                "npc": _npc_rows(d),
                "panel_en": str(_first(d, "panel_en", default="") or ""),
                "accent": d.get("accent"),
                "dialogue": dialogue,
                "narration_ref": [str(x) for x in _as_list(_first(d, "narration_ref", "narration_refs", "narrator_ref", default=[]))],
                "beat": str(_first(d, "beat", default="")),
                "notes": str(_first(d, "notes", "note", default="")),
                "view_tile": d.get("view_tile"),
                "final": [{"shot_id": f.get("shot_id"), "duration_s": f.get("duration_s"),
                           "size": f.get("size") or f.get("size_code"), "group": None}
                          for f in finals],
            })
        # 定稿组号:generation_groups shots[] 反查
        grp_of = {}
        for g in sl.get("generation_groups") or []:
            if isinstance(g, dict):
                for s_id in g.get("shots") or []:
                    grp_of[s_id] = g.get("group_id")
        for s in shots:
            for f in s["final"]:
                f["group"] = grp_of.get(f["shot_id"])
        alloc = _first(sc, "unit_alloc_s", "alloc_s", "allocated_duration_s", "scene_duration_anchor_s", "duration_budget_s")
        draft_sum = _first(sc, "draft_sum_s", "shots_sum_s")
        if draft_sum is None:
            try:
                draft_sum = round(sum(float(s["duration_hint_s"] or 0) for s in shots), 1)
            except Exception:
                draft_sum = None
        final_sum = None
        try:
            fs = [float(f["duration_s"]) for s in shots for f in s["final"] if f.get("duration_s") is not None]
            final_sum = round(sum(fs), 1) if fs else None
        except Exception:
            pass
        scenes.append({
            "scene_no": scene_no, "scene_id": sid,
            "scene_name": (cat["scenes"].get(sid) or {}).get("name") or "",
            "scene_description": (cat["scenes"].get(sid) or {}).get("description") or "",
            "location": str(_first(sc, "location", "scene_name", "name", "display_name", default="")),
            "time_of_day": str(_first(sc, "time_of_day", default="")),
            "screen_direction": str(_first(sc, "screen_direction_en", "screen_direction", "axis_note", default="") or ""),
            "alloc_s": alloc, "draft_sum_s": draft_sum, "final_sum_s": final_sum,
            "note": str(_first(sc, "unit_note", "scene_note", "scene_intent", "beat", default="")),
            "color": str(_first(sc, "color_segment", "color_ref", default="")),
            "cast": _scene_cast(sc, cast_union, shots),
            "creatures": creatures, "props": props,
            "group_count": len(groups),
            "npc_applied": sc.get("npc_applied") if isinstance(sc.get("npc_applied"), dict) else None,
            "shots": shots,
        })
    narration = load_narration(base, ep)
    narr_summary = attach_narration(scenes, narration, sl, names)
    return {
        "has_storyboard": bool(raw_scenes),
        "title": (sb.get("_meta") or {}).get("episode_title") or sb.get("title") or "",
        "storyboard_meta": {k: (sb.get("_meta") or {}).get(k) for k in ("task_id", "written_at", "status", "agent")},
        "scenes": scenes,
        "narration": narr_summary,
        "totals": {"scenes": len(scenes), "shots": sum(len(s["shots"]) for s in scenes),
                   "groups": sum(s["group_count"] for s in scenes),
                   "draft_sum_s": round(sum(float(s["draft_sum_s"] or 0) for s in scenes), 1) if scenes else None,
                   "final_total_s": sl.get("total_duration_s"), "budget_s": sl.get("budget_s"),
                   "final_shots": sl.get("shot_count"), "final_groups": sl.get("group_count")},
    }


def _npc_rows(d: dict) -> list[dict]:
    from modules.npc_staging import shot_npc
    return shot_npc(d)


_NPC_LAYER_EN = {"fg": "foreground", "mg": "midground", "bg": "background"}


def npc_sentence(shot: dict, grid: bool = False, max_chars: int | None = None) -> str:
    """草图里的 NPC 氛围层(2026-10-09):按层逐条,画成无名的松散人形/物体,不画成登记角色。"""
    rows = [x for x in shot.get("npc") or [] if isinstance(x, dict) and x.get("layer") in _NPC_LAYER_EN and x.get("what")]
    if not rows:
        return ""
    body = "; ".join(f"{_NPC_LAYER_EN[x['layer']]}: {clean_prose(x['what'])}" for x in rows)
    if max_chars:
        body = _short(body, max_chars)
    return (f"NPC: {body}." if grid else
            f"Anonymous NPC figures and objects for depth (loose, unnamed, never one of the named characters): {body}.")


def _scene_cast(sc: dict, cast_union: list, shots: list) -> list:
    """场次出场人物:场上 cast_ids/cast(分镜师显式写的)优先,否则各镜/各组并集;再补台词说话人。"""
    out = [c for c in _as_list(_first(sc, "cast_ids", "cast", "characters", default=[])) if isinstance(c, str)] or list(cast_union)
    for sh in shots:
        for ln in sh.get("dialogue") or []:
            sp = ln.get("speaker")
            if isinstance(sp, str) and sp.startswith("CHAR-") and sp not in out:
                out.append(sp)
    return out


# ---------------- 旁白挂镜(2026-09-15) ----------------
# 旁白稿 story/episodes/<ep>/narration.md 在 Phase 1 就定稿,每条只锚到「场次 + 剧本动作行」;精确到镜的挂点有两个来源:
#   ① 分镜师在草案镜写 narration_ref: ["N-01"](2026-09-15 起为必填,机检 narration_ref_ok);
#   ② shot-planning 定稿的 shot_list narration_anchors(H3S 之后才有)。
# 两者都没有(存量项目 / H3S 前)时按锚点文字推定:锚点里「…」引的剧本动作行与各镜 content/action 做字符二元组相似度,
# 「场首/场末」关键词兜底;推不出的挂在场块顶部「未定位」。页面与动态样片都用这里的结果,不各自再猜。

NARRATION_SOURCES = ("storyboard", "shot_list", "anchor_text", "scene", "unplaced")
_NARR_SIM_MIN = 0.25         # 锚点引文二元组被镜文字覆盖的最低比例(liaozhai3 ep01 实测命中 0.4–0.9、误配 <0.15)
_QUOTE_RE = re.compile(r"[「『“\"]([^」』”\"]{2,})[」』”\"]")


def load_narration(base: Path, ep: str) -> list[dict]:
    """本集旁白稿 → [{id, text, est_s, anchor, scene, tone}](复用 script_breakdown 的解析器;md 优先、json 兜底)。"""
    from modules import script_breakdown as sbd
    epdir = base / "story" / "episodes" / ep
    md = sbd.read_text(epdir / "narration.md")
    if md:
        items = sbd.parse_narration_md(md)
    else:
        items = sbd.parse_narration_json(sbd.read_json(epdir / "narration.json") or {})
    return [it for it in items if it.get("id") and it.get("text")]


def _bigrams(s: str) -> set:
    s = re.sub(r"[\s,，。.;；:：、!！?？「」『』“”\"'()（）\[\]…—-]", "", s or "")
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else set()


def _anchor_quote(anchor: str, names: dict) -> str:
    """锚点第四段里「…」引的剧本动作行(多段引文拼一起);CHAR-id 换成人名,便于与镜文字比。"""
    qs = _QUOTE_RE.findall(anchor or "")
    q = "".join(qs) if qs else ""
    for cid, nm in (names or {}).items():
        q = q.replace(cid, nm)
    return q


def _guess_shot(anchor: str, shots: list[dict], names: dict) -> tuple[dict | None, float]:
    """按锚点文字在场内定镜:引文相似度优先,其次 场首/场末 关键词;返回 (镜, 相似度)。"""
    if not shots:
        return None, 0.0
    quote = _anchor_quote(anchor, names)
    best, best_sc = None, 0.0
    if quote:
        qb = _bigrams(quote)
        if qb:
            for sh in shots:
                body = " ".join([sh.get("content") or "", sh.get("action") or "", sh.get("sketch") or ""])
                sc = len(qb & _bigrams(body)) / len(qb)
                if sc > best_sc:
                    best, best_sc = sh, sc
    if best is not None and best_sc >= _NARR_SIM_MIN:
        return best, round(best_sc, 2)
    a = anchor or ""
    if re.search(r"场首|开场|场头|片首|集首|开头|scene\s*(?:start|open|opening|head|top)|(?:start|top|beginning|opening)\s+of\s+(?:the\s+)?(?:scene|episode)|cold\s*open", a, re.I):
        return shots[0], 0.0
    if re.search(r"场末|场尾|收尾|结尾|片尾|集末|末尾|scene\s*(?:end|close|closing|tail|out)|(?:end|ending|close|tail)\s+of\s+(?:the\s+)?(?:scene|episode)|outro", a, re.I):
        return shots[-1], 0.0
    return None, round(best_sc, 2)


def attach_narration(scenes: list[dict], narration: list[dict], sl: dict, names: dict | None = None) -> dict:
    """把旁白条目挂到归一化场/镜上(就地改):每镜 narration[] = [{id, text, est_s, anchor, source, sim}],
    每场 narration_unplaced[](锚到本场但定不到镜),返回集级汇总 {items, placed, by_source, unplaced[]}。
    优先级:草案镜 narration_ref → shot_list narration_anchors → 锚点文字推定 → 场级未定位 → 集级未定位。"""
    names = names or {}
    by_id = {n["id"]: n for n in narration}
    by_scene_no = {sc["scene_no"]: sc for sc in scenes}
    by_scene_id = {sc["scene_id"]: sc for sc in scenes if sc.get("scene_id")}
    for sc in scenes:
        sc["narration_unplaced"] = []
        for sh in sc["shots"]:
            sh["narration"] = []
    placed: dict[str, list] = {}

    def _put(sh: dict, n: dict, source: str, sim: float = 0.0):
        sh["narration"].append({"id": n["id"], "text": n.get("text") or "", "est_s": n.get("est_s"),
                                "anchor": n.get("anchor") or "", "source": source, "sim": sim})
        placed.setdefault(n["id"], []).append(source)

    # ① 草案镜 narration_ref(分镜师明确挂点);引了旁白稿里没有的 id 也照显示(text 空),机检会报
    for sc in scenes:
        for sh in sc["shots"]:
            for nid in sh.get("narration_ref") or []:
                _put(sh, by_id.get(nid) or {"id": nid, "text": "", "est_s": None, "anchor": ""}, "storyboard")
    # ② shot_list narration_anchors:首个挂点镜(定稿 shNNN)反查草案镜
    draft_of_final = {f["shot_id"]: sh for sc in scenes for sh in sc["shots"] for f in sh.get("final") or [] if f.get("shot_id")}
    for a in (sl or {}).get("narration_anchors") or []:
        if not isinstance(a, dict):
            continue
        nid = a.get("narration_id")
        n = by_id.get(nid)
        if not n or nid in placed:
            continue
        for fid in _as_list(a.get("anchor_shots")):
            sh = draft_of_final.get(fid)
            if sh is not None:
                _put(sh, n, "shot_list")
                break
    # ③ 锚点文字推定 / ④ 场级未定位 / ⑤ 集级未定位
    unplaced_ep: list[dict] = []
    for n in narration:
        if n["id"] in placed:
            continue
        sc = by_scene_no.get(str(n.get("scene") or "").upper())
        if sc is None and n.get("anchor"):
            m = re.search(r"SCN-\d+", n["anchor"])
            if m:
                sc = by_scene_id.get(m.group(0))
        rec = {"id": n["id"], "text": n.get("text") or "", "est_s": n.get("est_s"), "anchor": n.get("anchor") or ""}
        if sc is None:
            unplaced_ep.append({**rec, "source": "unplaced"})
            continue
        sh, sim = _guess_shot(n.get("anchor") or "", sc["shots"], names)
        if sh is not None:
            _put(sh, n, "anchor_text", sim)
        else:
            sc["narration_unplaced"].append({**rec, "source": "scene", "sim": sim})
            placed.setdefault(n["id"], []).append("scene")
    by_source: dict[str, int] = {}
    for srcs in placed.values():
        by_source[srcs[0]] = by_source.get(srcs[0], 0) + 1
    return {"items": len(narration), "placed": sum(1 for n in narration if n["id"] in placed),
            "by_source": by_source, "unplaced": unplaced_ep,
            "total_est_s": round(sum(float(n.get("est_s") or 0) for n in narration), 1)}


def check_narration(board: dict, narration: list[dict], strict: bool = False) -> tuple[list[str], list[str]]:
    """旁白挂点机检 narration_ref_ok(2026-09-15):
    - 草案镜 narration_ref 引的 id 必须在旁白稿里(FAIL);同一条挂到多镜 WARN(旁白只在一处起播);
    - 旁白稿每条必须被某镜 narration_ref 引用(缺省 WARN——存量项目靠锚点推定;--strict FAIL,新项目交付前);
    - 引用它的镜所在场与锚点场次不一致(FAIL:挂错场);
    - 该镜(或所在场)时长建议明显装不下 est_duration_s(WARN,定稿窗口由 shot-planning 复核)。"""
    errs, warns = [], []
    by_id = {n["id"]: n for n in narration}
    ref_at: dict[str, list[tuple[dict, dict]]] = {}
    for sc in board["scenes"]:
        for sh in sc["shots"]:
            for nid in sh.get("narration_ref") or []:
                ref_at.setdefault(nid, []).append((sc, sh))
                if nid not in by_id:
                    errs.append(f"{sh['key']}: narration_ref {nid} 不在旁白稿里")
    for nid, locs in ref_at.items():
        if len(locs) > 1:
            warns.append(f"{nid}: 挂到了 {len(locs)} 镜({', '.join(sh['key'] for _, sh in locs)}),旁白只在首镜起播、其余镜应留空")
        n = by_id.get(nid)
        if not n:
            continue
        want = str(n.get("scene") or "").upper()
        for sc, sh in locs:
            if want and sc["scene_no"].upper() != want:
                errs.append(f"{sh['key']}: {nid} 锚在 {want},却挂在 {sc['scene_no']}")
        try:
            est = float(n.get("est_s") or 0)
            win = sum(float(x["duration_hint_s"] or 0) for _, x in locs)
            if est and win and win * 1.15 < est * 0.5:
                warns.append(f"{locs[0][1]['key']}: {nid} 估时 {est:g}s,所挂镜时长建议只有 {win:g}s(定稿窗口由 shot-planning 复核)")
        except Exception:
            pass
    for n in narration:
        if n["id"] not in ref_at:
            msg = f"{n['id']}: 旁白稿条目没有任何镜引用(anchor: {n.get('anchor') or '—'})"
            (errs if strict else warns).append(msg)
    return errs, warns


def find_shot(board: dict, scene_no: str, order=None) -> tuple[dict | None, list[dict]]:
    """按场次(+序号)取归一化场与镜;order 为空时返回该场全部镜。"""
    for sc in board["scenes"]:
        if sc["scene_no"] == scene_no:
            if order is None:
                return sc, list(sc["shots"])
            return sc, [s for s in sc["shots"] if s["order"] == int(order)]
    return None, []


def find_shots_by_keys(board: dict, keys: list[str]) -> list[tuple[dict, dict]]:
    """按草图键(S01-03)在归一化故事板里找 (scene, shot),保持传入顺序;找不到的键跳过。"""
    idx = {sh["key"]: (sc, sh) for sc in board["scenes"] for sh in sc["shots"]}
    return [idx[k] for k in keys if k in idx]


def episode_shots(board: dict, scene_no: str | None = None) -> list[tuple[dict, dict]]:
    """整集(或某场)的 (scene, shot) 按集内顺序。"""
    return [(sc, sh) for sc in board["scenes"] if scene_no is None or sc["scene_no"] == scene_no for sh in sc["shots"]]


# ---------------- 草图提示词 + 参考图 ----------------

# 机位/神态关键词表(中文分镜文字 → 英文提示词短语;按出现顺序拼进 Camera: / Expressions: 句,2026-09-12)。
# 只做关键词命中,不做语义理解:分镜原文本身也整段进提示词,这里是把最影响构图与表演的信息再用英文点一次。
_CAMERA_TERMS = (
    ("俯拍", "high angle"), ("俯视", "high angle"), ("高机位", "high angle"), ("顶拍", "top-down"), ("顶视", "top-down"),
    ("鸟瞰", "bird's-eye view"), ("仰拍", "low angle"), ("仰视", "low angle"), ("低机位", "low angle"), ("低角度", "low angle"),
    ("高俯", "high angle"), ("俯角", "high angle"), ("俯瞰", "high angle"), ("仰角", "low angle"),
    ("平视", "eye level"), ("过肩", "over-the-shoulder"), ("主观", "POV"), ("POV", "POV"), ("正面", "frontal"),
    ("正对", "frontal"), ("侧面", "profile"), ("侧拍", "profile"), ("侧身", "three-quarter view"), ("背影", "from behind"),
    ("背对", "from behind"), ("背后", "from behind"), ("正反打", "shot/reverse shot"), ("反打", "reverse angle"),
    ("广角", "wide lens"), ("长焦", "long lens"), ("鱼眼", "fisheye lens"), ("微距", "macro"),
    ("倾斜", "dutch angle"), ("斜角", "dutch angle"), ("对称", "symmetrical composition"), ("剪影", "silhouette"),
    ("前景", "foreground element framing"), ("门框", "framed by a doorway"), ("窗框", "framed by a window"),
    ("推进", "push-in (draw the start frame)"), ("推近", "push-in (draw the start frame)"), ("拉远", "pull-out (draw the start frame)"),
    ("拉开", "pull-out (draw the start frame)"), ("横移", "lateral tracking"), ("跟拍", "following shot"), ("跟随", "following shot"),
    ("环绕", "orbit"), ("摇镜", "pan"), ("横摇", "pan"), ("摇摄", "pan"), ("升降", "crane"), ("手持", "handheld"),
)
_EXPRESSION_TERMS = (
    ("微笑", "smiling"), ("带笑", "smiling"), ("大笑", "laughing"), ("狂笑", "laughing wildly"), ("冷笑", "sneering"),
    ("苦笑", "wry smile"), ("皱眉", "frowning"), ("蹙眉", "frowning"), ("惊恐", "terrified"), ("惊讶", "surprised"),
    ("震惊", "shocked"), ("吃惊", "surprised"), ("惊", "startled"), ("哭", "crying"), ("泪", "tears"), ("愤怒", "angry"),
    ("怒", "angry"), ("冷漠", "cold and indifferent"), ("冷淡", "cold"), ("紧张", "tense"), ("恐惧", "fearful"),
    ("害怕", "afraid"), ("疑惑", "puzzled"), ("困惑", "confused"), ("不解", "puzzled"), ("警惕", "wary"),
    ("沉默", "silent, lips pressed"), ("悲伤", "sad"), ("难过", "sad"), ("疲惫", "exhausted"), ("得意", "smug"),
    ("严肃", "stern"), ("面无表情", "blank face"), ("木然", "blank face"), ("呆滞", "dazed"), ("茫然", "blank, lost"),
    ("瞪", "glaring"), ("凝视", "staring"), ("盯", "staring"), ("对视", "locking eyes"), ("低头", "head lowered"),
    ("抬头", "looking up"), ("回头", "looking back over the shoulder"), ("侧目", "glancing sideways"),
    ("视线", "clear eye line"), ("目光", "clear eye line"), ("看向", "looking toward"), ("闭眼", "eyes closed"),
    ("咬牙", "jaw clenched"), ("颤抖", "trembling"), ("喘", "panting"), ("屏息", "holding breath"),
    ("犹豫", "hesitant"), ("决绝", "resolute"), ("绝望", "despairing"), ("释然", "relieved"), ("温柔", "gentle"),
    ("挣扎", "struggling"), ("蜷缩", "curled up"), ("瘫", "slumped"), ("僵住", "frozen stiff"), ("僵在", "frozen stiff"),
)

# ---------------- 人物姿态/动作(2026-09-14 用户拍板) ----------------
# 受控枚举:分镜层 shots_draft[].poses[<CHAR-id>].pose 取值;白模关键帧 pose 同一套(modules/whitebox.py POSES),
# 站/坐/躺沿用白模原三态,跪/蹲/趴为本次新增(白模渲染器与包围盒同步支持)。
POSE_ENUM = ("stand", "sit", "lie", "kneel", "crouch", "prone")
POSE_EN = {"stand": "standing", "sit": "sitting", "lie": "lying down", "kneel": "kneeling",
           "crouch": "crouching", "prone": "lying face down"}
POSE_ZH = {"stand": "站", "sit": "坐", "lie": "躺", "kneel": "跪", "crouch": "蹲", "prone": "趴"}
# 各体位在站位片段/动线句里的可辨认写法(机检 pose 与 space_fragment_en 是否相符时用,中英都认)
POSE_WORDS = {"stand": ("站", "立", "stand"), "sit": ("坐", "sit", "seat"), "lie": ("躺", "卧", "lying", "lie", "reclin"),
              "kneel": ("跪", "kneel"), "crouch": ("蹲", "crouch", "squat"), "prone": ("趴", "俯卧", "匍匐", "prone", "face down")}

# 关键词表:中文分镜文字 → 英文短语(与 _CAMERA_TERMS 同款,只做子串命中不做语义理解;
# 单字项只收高精度的,易误命中的一律用双字;action 中文直通的项目也可能在 content 里写英文,英文项按整词匹配 _POSE_TERMS_EN)。
_POSE_EXCLUDE = ("车站", "站台", "站牌", "驿站", "站位", "坐标", "坐落", "卧室", "卧房", "卧榻", "卧铺", "走廊", "走道", "走线",
                 "跑道", "倒影", "倒像", "伏笔", "起伏", "埋伏", "闪光", "闪烁", "闪回", "闪电", "闪现", "闪过", "推进", "推近",
                 "拉远", "拉开", "跟拍", "跟随", "扑面", "扑克", "拍摄", "俯拍", "侧拍", "仰拍", "顶拍", "跳切", "跳接", "跳动")
_POSE_TERMS = (
    # 静态体位
    ("站立", "standing"), ("站着", "standing"), ("站在", "standing"), ("站定", "standing still"), ("站起", "standing up"),
    ("起身", "rising to their feet"), ("直立", "standing upright"), ("伫立", "standing still"), ("肃立", "standing at attention"),
    ("站", "standing"),
    ("坐着", "sitting"), ("坐在", "sitting"), ("坐下", "sitting down"), ("落座", "sitting down"), ("端坐", "sitting upright"),
    ("盘坐", "sitting cross-legged"), ("盘腿", "sitting cross-legged"), ("坐起", "sitting up"), ("坐", "sitting"),
    ("平躺", "lying on the back"), ("仰卧", "lying on the back"), ("侧卧", "lying on one side"), ("侧躺", "lying on one side"),
    ("躺", "lying down"), ("卧", "lying down"),
    ("跪拜", "kowtowing"), ("跪地", "kneeling on the ground"), ("下跪", "kneeling"), ("跪", "kneeling"), ("屈膝", "kneeling"),
    ("半蹲", "half crouch"), ("蹲", "crouching"),
    ("俯卧", "lying face down"), ("趴", "lying face down"), ("匍匐", "crawling on the ground"), ("伏案", "hunched over the desk"),
    ("靠着", "leaning against"), ("靠在", "leaning against"), ("倚着", "leaning against"), ("倚在", "leaning against"),
    ("斜倚", "reclining"), ("弯腰", "bending over"), ("俯身", "bending forward"), ("躬身", "bowing"), ("鞠躬", "bowing"),
    ("仰头", "head tilted back"), ("叉腰", "hands on hips"), ("抱臂", "arms crossed"), ("背手", "hands behind the back"),
    ("握拳", "fists clenched"), ("攥紧", "gripping tightly"),
    # 移动
    ("行走", "walking"), ("踱步", "pacing"), ("走", "walking"), ("狂奔", "sprinting"), ("跑", "running"), ("奔", "running"),
    ("追赶", "chasing"), ("追上", "catching up"), ("冲向", "charging at"), ("冲出", "rushing out"), ("冲进", "rushing in"),
    ("退后", "stepping back"), ("后退", "stepping back"), ("倒退", "backing away"), ("跳起", "jumping up"), ("跳下", "jumping down"),
    ("跃起", "leaping up"), ("一跃", "leaping"), ("飞跃", "leaping over"), ("攀爬", "climbing"), ("爬起", "getting up"),
    ("爬上", "climbing onto"), ("蹒跚", "staggering"), ("踉跄", "staggering"), ("倒下", "collapsing"), ("倒地", "falling to the ground"),
    ("跌", "falling"), ("摔", "falling"), ("转身", "turning around"), ("回身", "turning around"), ("迈步", "striding"),
    ("骑马", "riding a horse"), ("骑着", "riding"), ("上马", "mounting"), ("下马", "dismounting"), ("拖着", "dragging"),
    ("牵着", "leading by hand"), ("扛着", "carrying on the shoulder"), ("背着", "carrying on the back"), ("抬着", "carrying"),
    ("提着", "carrying"), ("拎着", "carrying"), ("撑着", "propping up"),
    # 动作
    ("挥剑", "swinging a sword"), ("挥刀", "swinging a blade"), ("挥手", "waving a hand"), ("挥", "swinging"),
    ("劈", "slashing down"), ("砍", "hacking"), ("刺向", "thrusting at"), ("刺出", "thrusting"), ("拔剑", "drawing a sword"),
    ("拔刀", "drawing a blade"), ("持剑", "holding a sword"), ("握剑", "gripping a sword"), ("举剑", "raising a sword"),
    ("抬手", "raising a hand"), ("举手", "hand raised"), ("伸手", "reaching out"), ("推门", "pushing the door"),
    ("推开", "pushing open"), ("推搡", "shoving"), ("拉住", "grabbing hold"), ("搂住", "holding close"), ("抱", "embracing"),
    ("扶", "supporting"), ("搀", "supporting"), ("指着", "pointing at"), ("指向", "pointing toward"), ("抓住", "grabbing"),
    ("拽", "yanking"), ("掀", "lifting"), ("递", "handing over"), ("捧", "holding in both hands"), ("端起", "lifting up"),
    ("拍打", "patting"), ("敲", "knocking"), ("捶", "pounding"), ("踢", "kicking"), ("踹", "kicking"), ("掷", "throwing"),
    ("扔", "throwing"), ("擦", "wiping"), ("梳", "combing"), ("拨", "stirring"), ("抚摸", "stroking"), ("摸着", "touching"),
    ("掩面", "covering the face"), ("捂住", "covering"), ("拱手", "cupping hands in salute"), ("作揖", "bowing with clasped hands"),
    ("磕头", "kowtowing"), ("叩首", "kowtowing"),
    # 反应
    ("闪躲", "dodging"), ("闪身", "dodging aside"), ("闪开", "dodging away"), ("躲避", "dodging"), ("躲", "dodging"), ("闪", "dodging"),
    ("退缩", "shrinking back"), ("缩身", "shrinking back"), ("扑向", "lunging at"), ("扑倒", "lunging down"), ("扑", "lunging"),
    ("格挡", "parrying"), ("挡住", "blocking"), ("抱头", "covering the head"), ("捂脸", "covering the face"),
    ("定住", "freezing"), ("愣", "freezing"),
)
_POSE_TERMS_EN = (
    ("standing", "standing"), ("stands", "standing"), ("stand", "standing"), ("sitting", "sitting"), ("seated", "sitting"),
    ("sits", "sitting"), ("sit", "sitting"), ("lying", "lying down"), ("lies", "lying down"), ("reclining", "reclining"),
    ("kneeling", "kneeling"), ("kneels", "kneeling"), ("kneel", "kneeling"), ("crouching", "crouching"), ("crouch", "crouching"),
    ("squatting", "squatting"), ("prone", "lying face down"), ("face down", "lying face down"), ("leaning", "leaning"),
    ("running", "running"), ("runs", "running"), ("sprint", "sprinting"), ("walking", "walking"), ("walks", "walking"),
    ("jumping", "jumping"), ("jumps", "jumping"), ("leaping", "leaping"), ("climbing", "climbing"), ("falling", "falling"),
    ("falls", "falling"), ("collapses", "collapsing"), ("dodging", "dodging"), ("dodges", "dodging"), ("swinging", "swinging"),
    ("swings", "swinging"), ("draws a sword", "drawing a sword"), ("thrusts", "thrusting"), ("lunges", "lunging"),
    ("bowing", "bowing"), ("bows", "bowing"), ("kowtow", "kowtowing"), ("reaching", "reaching out"), ("pushing", "pushing"),
    ("pulling", "pulling"), ("embracing", "embracing"), ("turning around", "turning around"), ("riding", "riding"),
)


_NEGATED_RE = re.compile(r"(不|没|未|别|勿|莫|无)(再|曾|有|要|敢|肯|能|会|去|想)?$")


def _pose_term_hits(text: str) -> list[str]:
    """从分镜文字命中姿态/动作英文短语(按出现位置排序、按短语去重);先剔除 _POSE_EXCLUDE 里的非肢体词。"""
    t = str(text or "")
    for bad in _POSE_EXCLUDE:
        t = t.replace(bad, "﹍" * len(bad))    # 等长占位,保住位置
    # 长词优先、占位去重:「侧卧」命中后同一处的「卧」不再单独命中(否则一句里 lying on one side / lying down 双报)
    covered: list[tuple[int, int]] = []
    hits: list[tuple[int, str]] = []
    for zh, en in sorted(_POSE_TERMS, key=lambda kv: -len(kv[0])):
        start = 0
        while True:
            i = t.find(zh, start)
            if i < 0:
                break
            j = i + len(zh)
            if not any(i < b and j > a for a, b in covered):
                covered.append((i, j))
                # 否定词紧邻在前(「不坐起来」「没回身」「不再站」)= 该动作没发生,占住位置但不命中
                if not _NEGATED_RE.search(t[max(0, i - 3):i]) and en not in (e for _, e in hits):
                    hits.append((i, en))
            start = j
    found = [en for _, en in sorted(hits)]
    for m in re.finditer(r"[A-Za-z][A-Za-z ]+", t):
        seg = m.group(0).lower()
        for en_kw, en in _POSE_TERMS_EN:
            if re.search(rf"\b{re.escape(en_kw)}\b", seg) and en not in found:
                found.append(en)
    return found


def normalize_poses(v) -> dict:
    """把 storyboard.json 里各种写法归一成 {id: {pose, action}}:
    dict {id: {pose, action}} / {id: "sit"} / list [{id|character, pose, action}];非法/空 → {}。pose 小写去空格,不在枚举内原样保留(交机检报)。"""
    out: dict = {}
    items = []
    if isinstance(v, dict):
        items = [(k, rec) for k, rec in v.items()]
    elif isinstance(v, list):
        for rec in v:
            if isinstance(rec, dict):
                cid = rec.get("id") or rec.get("character") or rec.get("cast")
                if isinstance(cid, str):
                    items.append((cid, rec))
    for cid, rec in items:
        if not isinstance(cid, str) or not cid.strip():
            continue
        if isinstance(rec, str):
            rec = {"pose": rec}
        if not isinstance(rec, dict):
            continue
        pose = str(rec.get("pose") or rec.get("state") or "").strip().lower()
        action = str(rec.get("action") or rec.get("action_zh") or "").strip()
        if not pose and not action:
            continue
        out[cid.strip()] = {"pose": pose, "action": action}
    return out


_OFFSCREEN_POSE_RE = re.compile(r"^\s*[（(\[【]?\s*(?:画外|off[- ]?screen|o\.\s?s\.)", re.I)


def pose_offscreen(rec) -> bool:
    """poses 条目的 action 以「画外」开头(「画外:只闻其声」)= 该人物本镜不入画(#105)。"""
    return isinstance(rec, dict) and bool(_OFFSCREEN_POSE_RE.match(str(rec.get("action") or "")))


def frame_cast(shot: dict) -> list:
    """本镜入画的出场人物/生物 id:shot.cast 去掉 poses 标了「画外」的。草图提示词的出场句、姿态句、参考图都按它取。"""
    poses = shot.get("poses") or {}
    return [c for c in shot.get("cast") or [] if not pose_offscreen(poses.get(c))]


def pose_hint(shot: dict, names: dict | None = None) -> str:
    """每镜「Body poses and actions:」句:有结构化 `poses` 时逐角色 "<名> <体位英文>, <action 原文>"(action 中文直通,2026-09-14 拍板),
    否则退回从 content/action/sketch 文字按关键词表推导(最多 10 个短语);都没有返回空。"""
    poses = shot.get("poses") or {}
    segs = []
    for cid, rec in poses.items():
        if not isinstance(rec, dict) or pose_offscreen(rec):
            continue
        name = (names or {}).get(cid, cid)
        body = ", ".join(x for x in (POSE_EN.get(rec.get("pose") or "", ""), str(rec.get("action") or "").strip()) if x)
        if body:
            segs.append(f"{name} {body}")
    if segs:
        return "; ".join(segs)
    if poses and all(pose_offscreen(rec) for rec in poses.values()):
        return ""      # 全是画外条目:本镜没有要画的姿态,不再从散文里推导
    text = " ".join(str(shot.get(k) or "") for k in ("content", "action", "sketch"))
    return ", ".join(_pose_term_hits(text)[:10])


def check_poses(board: dict, strict: bool = False) -> tuple[list[str], list[str]]:
    """机检 pose_present(2026-09-14):每镜每个出场角色(CHAR-*)在 `poses` 有条目且 pose 在枚举内。
    整镜没写 `poses` 的存量项目按 WARN(strict=True 按 FAIL);写了 `poses` 但漏角色/枚举外一律 FAIL;
    生物(CRE-*)缺条目只 WARN;poses 里出现不在本镜/本场出场的 id 只 WARN。返回 (errors, warnings)。"""
    errs, warns = [], []
    for sc in board.get("scenes") or []:
        scene_cast = set(sc.get("cast") or []) | set(sc.get("creatures") or [])
        for sh in sc.get("shots") or []:
            key, cast, poses = sh.get("key"), [c for c in (sh.get("cast") or []) if isinstance(c, str)], sh.get("poses") or {}
            if not poses:
                if cast:
                    (errs if strict else warns).append(f"{key}: 缺 poses(出场 {', '.join(cast)} 的体位/动作未登记)")
                continue
            for cid, rec in poses.items():
                pose = (rec or {}).get("pose") or ""
                if pose and pose not in POSE_ENUM:
                    errs.append(f"{key}/{cid}: pose {pose!r} 不在枚举 {'/'.join(POSE_ENUM)} 内")
                elif not pose:
                    errs.append(f"{key}/{cid}: 缺 pose(只写了 action)")
                if cid not in cast and cid not in scene_cast:
                    warns.append(f"{key}/{cid}: poses 里的 id 不在本镜/本场出场名单")
            for cid in cast:
                if cid not in poses:
                    (warns if is_creature_id(cid) else errs).append(f"{key}/{cid}: 出场但 poses 无条目")
    return errs, warns


def _term_hits(text: str, table) -> list[str]:
    """按关键词在文字里的出现位置排序,去重返回英文短语。"""
    found = []
    for zh, en in table:
        i = text.find(zh)
        if i >= 0 and en not in (e for _, e in found):
            found.append((i, en))
    return [en for _, en in sorted(found)]


def camera_hint(shot: dict) -> str:
    """从 sketch/content/action 推导机位英文短语(角度/高度/镜头/朝向/运镜),无命中返回空。"""
    text = " ".join(str(shot.get(k) or "") for k in ("sketch", "content", "action"))
    return ", ".join(_term_hits(text, _CAMERA_TERMS)[:6])


def expression_hint(shot: dict) -> str:
    """从 content/action/sketch 推导神态/视线英文短语,无命中返回空。"""
    text = " ".join(str(shot.get(k) or "") for k in ("content", "action", "sketch"))
    return ", ".join(_term_hits(text, _EXPRESSION_TERMS)[:8])


# ---------------- 画面描述净化 / 景别 / 焦段 / 轴线(2026-09-29 用户拍板,草图向传统电影故事板靠拢) ----------------
# 分镜散文里混着给下游工位的制作元数据(【长镜头前段·镜内分段 0–7s…】、交组B续写、受力反馈=…),原样进提示词
# 既挤占宫格字数预算又干扰模型;草图只画起幅一帧,时间码与「A→B」变化过程只留起点。
# 分镜层可写 shots_draft[].panel_en(≤60 词英文画面描述,见 storyboard SOUL);有则直接用,不再拼中文散文。
_PROSE_DROP_WORDS = ("交组", "续写", "接缝", "指纹", "机检", "批次", "回派", "闭环", "复核", "工位", "台账",
                     "受力反馈", "同空间硬切", "硬切", "WBI-", "grp0", "替代旧", "待与")
_TIMECODE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:[–—\-~～至]\s*\d+(?:\.\d+)?\s*)?(?:s|秒)(?![a-zA-Z])")
_ARROW_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|°|%)?\s*(?:→|->|—>)\s*\d+(?:\.\d+)?\s*(mm|°|%)")
_PAREN_RE = re.compile(r"[（(][^（）()]*[)）]")


def clean_prose(text) -> str:
    """分镜散文 → 只留可画的起幅画面:删【】段落标记、含制作用语或时间码的分句/括注,「200→85mm」只留起点。"""
    t = str(text or "")
    t = re.sub(r"【[^】]*】", "", t)
    t = _ARROW_NUM_RE.sub(lambda m: m.group(1) + (m.group(2) or m.group(3) or ""), t)
    t = _PAREN_RE.sub(lambda m: "" if any(w in m.group(0) for w in _PROSE_DROP_WORDS) or _TIMECODE_RE.search(m.group(0))
                      else m.group(0), t)
    keep = []
    for c in re.split(r"(?<=[;；。，,])", t):
        if not c.strip(" ;；。，,") or any(w in c for w in _PROSE_DROP_WORDS) or _TIMECODE_RE.search(c):
            continue
        keep.append(c)
    out = re.sub(r"\s+", " ", "".join(keep)).strip(" ;；，,")
    return re.sub(r"[;；，,]+$", "", out).strip()


def panel_text(shot: dict) -> str:
    """英文画面描述:草图台账 --panel 覆盖(_panel)> 分镜层 panel_en;都没有返回空(调用方退回净化后的中文散文)。"""
    return re.sub(r"\s+", " ", str(shot.get("_panel") or shot.get("panel_en") or "")).strip()


# 景别 → 可画的构图规则(人物占画高 / 画框切在身体哪里);长词优先。中文取「A → B」的起幅 A。
_SIZE_RULES = (
    ("大远景", "extreme wide shot: landscape or architecture dominates, any figures are tiny (under a tenth of the frame height)"),
    ("极远景", "extreme wide shot: landscape or architecture dominates, any figures are tiny (under a tenth of the frame height)"),
    ("大全景", "very wide shot: full figures small, about a fifth of the frame height, lots of surrounding space"),
    ("中全景", "medium full shot: figures cut just above or below the knees"),
    ("中近景", "medium close-up: figures cut at mid-chest, head and shoulders dominate the frame"),
    ("大特写", "extreme close-up: a single detail (eyes, a hand, an object) fills the frame"),
    ("远景", "wide shot: full figures, about a third of the frame height, surroundings clearly visible"),
    ("全景", "full shot: whole body head to feet, filling about three quarters of the frame height"),
    ("中景", "medium shot: figures cut at the waist"),
    ("近景", "close shot: figures cut at the chest, the face large and clearly readable"),
    ("特写", "close-up: the face fills the frame, cropped at the forehead and just below the chin"),
    ("ECU", "extreme close-up: a single detail (eyes, a hand, an object) fills the frame"),
    ("MCU", "medium close-up: figures cut at mid-chest, head and shoulders dominate the frame"),
    ("CU", "close-up: the face fills the frame, cropped at the forehead and just below the chin"),
    ("MFS", "medium full shot: figures cut just above or below the knees"),
    ("MLS", "medium full shot: figures cut just above or below the knees"),
    ("MS", "medium shot: figures cut at the waist"),
    ("FS", "full shot: whole body head to feet, filling about three quarters of the frame height"),
    ("EWS", "extreme wide shot: landscape or architecture dominates, any figures are tiny (under a tenth of the frame height)"),
    ("ELS", "extreme wide shot: landscape or architecture dominates, any figures are tiny (under a tenth of the frame height)"),
    ("WS", "wide shot: full figures, about a third of the frame height, surroundings clearly visible"),
    ("LS", "wide shot: full figures, about a third of the frame height, surroundings clearly visible"),
)


def size_rule(shot: dict) -> str:
    """景别 → 英文构图规则;认不出原样返回(可能已是英文),空返回空。"""
    raw = str(shot.get("size_hint") or "").strip()
    if not raw:
        return ""
    head = re.split(r"→|->|—>", raw)[0].strip()
    for term, rule in _SIZE_RULES:
        if (term.isascii() and re.search(rf"\b{term}\b", head)) or (not term.isascii() and term in head):
            return rule
    return head


def lens_rule(shot: dict) -> str:
    """焦段(分镜文字里首个 NNmm,变焦取起点)→ 透视描述;没写焦段返回空(广角/长焦字样由 camera_hint 覆盖)。"""
    text = " ".join(clean_prose(shot.get(k)) for k in ("sketch", "content", "action"))     # 净化后:时间码分句里的落幅焦段不算
    m = re.search(r"(\d{2,3})\s*mm", text)
    if not m:
        return ""
    mm = int(m.group(1))
    if mm <= 24:
        look = "ultra-wide lens: strongly exaggerated perspective, steeply converging lines, near things huge and far things tiny"
    elif mm <= 35:
        look = "wide lens: noticeable perspective depth, converging lines, roomy framing"
    elif mm <= 65:
        look = "normal lens: natural perspective like the human eye"
    elif mm <= 135:
        look = "telephoto lens: compressed depth, background enlarged and close behind the subject, little perspective convergence"
    else:
        look = "long telephoto lens: very compressed depth, background layers stacked flat and large behind the subject, narrow field of view"
    return f"{mm}mm {look}"


def horizon_rule(cam: str) -> str:
    """机位高度 → 地平线在画面里的位置(传统分镜交代机位高度的方式)。cam 为 camera_hint 结果。"""
    if any(k in cam for k in ("top-down", "bird's-eye view")):
        return "Looking almost straight down: no horizon in frame, figures seen from above."
    if "high angle" in cam:
        return "Horizon line high in the frame or above it; we look down on the figures."
    if "low angle" in cam:
        return "Horizon line low in the frame; the figures loom above it."
    if "eye level" in cam:
        return "Horizon line at the figures' eye height."
    return ""


# 本场轴线里的运动分句(「红线自画左→画右斜穿」)只在本镜也拍到那个运动主体时才进草图提示词(2026-09-30 用户拍板):
# 前科 fengshen3 ep07 S02-04「什么都没有了」的张望镜被整场的「红线…斜穿」画出一条红色对角线。静态布局分句(谁/什么在画左画右)照留。
_SD_MOTION_RE = re.compile(r"→|->|入画|出画|斜穿|穿过|扑|滚|走|跑|追|冲|飞|射|退|甩|劈|砍|刺|掠|落向|坠|逃|奔|自画|由画|从画|向画|朝画"
                           r"|\b(?:enter|exit|mov|fl[iy]|run|walk|cross|travel|charg|lung|chas|pass|com|go|head|leav|fall|shoot|swing)",
                           re.I)
_SD_SUBJ_CUT_RE = re.compile(r"自|由|从|向|朝|顺|沿|被|第一次|→|->|\b(?:enter|exit|move|fl[iy]|run|walk|cross|travel|charge|lunge|chase"
                             r"|pass|come|go|head|leave|fall|shoot|swing|from|to|toward|is|are)\w*\b", re.I)
_SD_PREFIX_RE = re.compile(r"^\s*(?:R\d+[^:：]{0,12}[:：]|末镜|首镜|本场|全场)\s*")
_SD_STOP = {"画左", "画右", "画面", "画内", "画外", "左侧", "右侧", "screen", "left", "right", "frame", "the", "a", "an"}


def _sd_subject_tokens(clause: str) -> list[str]:
    """运动分句的主体词:去掉 R1:/末镜 前缀,取第一个运动/方向标记之前的部分;中文拆二字组,英文拆词(去方位虚词)。"""
    c = _SD_PREFIX_RE.sub("", clause)
    m = _SD_SUBJ_CUT_RE.search(c)
    head = re.sub(r"画[左右中面内外上下]|[左右]侧|[上下]方", " ", (c[:m.start()] if m else c)).strip(" ,，、:：")
    toks = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", head) if w.lower() not in _SD_STOP]
    for run in re.findall(r"[\u4e00-\u9fff]+", head):
        toks += [run[i:i + 2] for i in range(len(run) - 1) if run[i:i + 2] not in _SD_STOP] or [run]
        if len(run) == 2:       # 「斧劈」这类二字主语:首字也算(斧子/斧刃)
            toks.append(run[0])
    return toks


def _shot_text(shot: dict, names: dict | None = None) -> str:
    """本镜全部可画文字(画面/动作/构图/panel_en/姿态/出场人名),供轴线运动分句判断主体是否在本镜。"""
    bits = [shot.get(k) or "" for k in ("_panel", "panel_en", "content", "action", "sketch", "extras", "_note")]
    for cid, p in (shot.get("poses") or {}).items():
        if not pose_offscreen(p):
            bits.append(str((p or {}).get("action") or "") if isinstance(p, dict) else str(p))
    bits += [str((names or {}).get(c, c)) for c in frame_cast(shot)]
    return " ".join(str(b) for b in bits).lower()


def screen_direction(scene: dict, shots: list[dict] | None = None, names: dict | None = None) -> str:
    """本场轴线/画面方向(storyboard.json scenes[].screen_direction_en / axis_note),各镜共用,保证跨镜画左画右一致。
    shots 给出时(单镜 [shot] / 宫格本场各格)按分句过滤:运动分句的主体不在这些镜的文字里 → 删掉,静态布局分句照留。"""
    sd = clean_prose(scene.get("screen_direction") or "")
    if not sd or shots is None:
        return sd
    text = " ".join(_shot_text(s, names) for s in shots)
    keep, last = [], True       # last:上一运动分句是否保留——省略主语的续句(「、从画右偏上出画」)跟着它走
    for c in re.split(r"(?<=[;；。，,、])|(?<=\.)\s", sd):
        body = c.strip(" ;；。，,、.")
        if not body:
            continue
        if _SD_MOTION_RE.search(body):
            toks = _sd_subject_tokens(body)
            last = any(t in text for t in toks) if toks else last
            if not last:
                continue
        keep.append(c)
    return re.sub(r"[;；，,、\s]+$", "", "".join(keep)).strip()


# 灰调重点色画风的逐镜重点色(2026-09-30 用户拍板):分镜层 shots_draft[].accent = {"element": "…", "color": "red"}
# 或 "none";没写 = 本镜纯灰度——不让模型自己挑(前科:ep07 S02-04 模型把「灯点」画成一排红点)。
_ACCENT_NONE = {"", "none", "no", "null", "无", "没有", "-", "—"}


def accent_of(shot: dict) -> tuple[str, str] | None:
    """(element, color) 或 None(纯灰度)。也收字符串「元素|颜色」。"""
    a = shot.get("_accent") if shot.get("_accent") is not None else shot.get("accent")
    if isinstance(a, dict):
        el = str(a.get("element_en") or a.get("element") or "").strip()
        col = str(a.get("color_en") or a.get("color") or "").strip()
    elif isinstance(a, str) and "|" in a:
        el, col = (x.strip() for x in a.split("|", 1))
    else:
        return None
    if el.lower() in _ACCENT_NONE or not col:
        return None
    return el, col


def accent_sentence(shot: dict, grid: bool = False) -> str:
    ac = accent_of(shot)
    if ac:
        return (f"Accent: {ac[1]} on {ac[0]} only." if grid else
                f"Accent color: {ac[1]}, used only on {ac[0]}; everything else stays greyscale.")
    return "Accent: none, pure greyscale." if grid else "Accent color: none — the whole frame is pure greyscale."


def _space_hint(scene: dict, max_chars: int = 60) -> str:
    """地点只留一句短提示(地点/场名 + 时段,截到 max_chars),不带场景卡描述——背景只是示意。"""
    loc = " ".join(x for x in (scene.get("location") or scene.get("scene_name") or "", scene.get("time_of_day") or "")
                   if x and x not in ("未知", "unknown"))
    return _short(loc, max_chars)


def sketch_ref_capacity(provider: str, cfg: dict | None = None) -> int | None:
    """该图像渠道出草图最多传几张人物参考图:None = 不另设限(各直连渠道,沿用 MAX_CAST_REFS / GRID_MAX_CAST_REFS),
    0 = 纯文生图。只有 REF_CONDITIONAL_SKETCH_PROVIDERS(comfyui / agentics)看图生图配置,见 genmedia.image_ref_capacity;
    cfg 传调用方已取的生效图像渠道配置(省一次读取),缺省现取。"""
    if str(provider or "").strip().lower() not in REF_CONDITIONAL_SKETCH_PROVIDERS:
        return None
    from modules.genmedia import get_config, image_ref_capacity
    try:
        return image_ref_capacity(cfg if cfg is not None else get_config("image")) or 0
    except Exception:  # noqa: BLE001  渠道未配置等:按纯文生图
        return 0


def sketch_text_only(provider: str, cfg: dict | None = None) -> bool:
    """该图像渠道出草图是否走纯文生图(不传人物参考图):comfyui / agentics 没配置可用的图生图时。"""
    return sketch_ref_capacity(provider, cfg) == 0


def sketch_plain_style(provider: str) -> bool:
    """该渠道的模型多为 cfg=1 蒸馏模型(不吃 negative、会把否定句与姿态列举照字面画):风格句只用正向写法。"""
    return str(provider or "").strip().lower() in REF_CONDITIONAL_SKETCH_PROVIDERS


def ref_cast_ids(base: Path, shot: dict, catalog: dict, limit: int | None = None) -> list[str]:
    """单镜参考图对应的出场人物/生物 id(collect_refs 同序同上限)。"""
    cap = MAX_CAST_REFS if limit is None else min(MAX_CAST_REFS, limit)
    ids: list[str] = []
    for cid in frame_cast(shot):
        rec = catalog["characters"].get(cid) or catalog["creatures"].get(cid) or {}
        if rec.get("file") and (base / rec["file"]).is_file() and len(ids) < cap:
            ids.append(cid)
    return ids


def ref_sentence_plain(ref_names: list[str]) -> str:
    who = "; ".join(f"Picture {i} is {n}" for i, n in enumerate(ref_names, 1)) or "one sheet per character"
    return SKETCH_REFS_SENTENCE_PLAIN.format(who=who)


def sketch_single_only(provider: str) -> bool:
    """该图像渠道宫格批量是否退化为逐镜单张:只有 comfyui(本地模型跟不了严格 2×2 排版),见 SINGLE_ONLY_SKETCH_PROVIDERS。"""
    return str(provider or "").strip().lower() in SINGLE_ONLY_SKETCH_PROVIDERS


def whitebox_sentence(legend: list[tuple[str, str]], grid: str = "") -> str:
    """白模构图底句:legend = [(人物名, 颜色英文名)];grid 为宫格说明(单张传空)。"""
    who = (": " + "; ".join(f"the {c} mannequin is {n}" for n, c in legend)) if legend else ""
    return SKETCH_WHITEBOX_SENTENCE.format(who=who, grid=grid)


def _shot_parts(shot: dict, names: dict, clip: dict | None = None, grid: bool = False) -> list[str]:
    """单镜/宫格格共用的逐镜描述:景别规则 → 机位+地平线 → 焦段 → 出场 → 姿态 → 画面(panel_en 或净化散文)→ 神态 → 群众。
    clip = 宫格裁剪档(None 不裁)。"""
    cut = (lambda s_, k: _short(s_, clip[k]) if clip and clip.get(k) else s_)
    out = []
    size = size_rule(shot)
    if size:
        out.append(f"Shot size: {size}.")
    cam = camera_hint(shot)
    if cam:
        hz = horizon_rule(cam)
        out.append(f"Camera: {cam}." + (f" {hz}" if hz else ""))
    lens = lens_rule(shot)
    if lens:
        out.append(f"Lens: {lens}.")
    cast = [names.get(c, c) for c in frame_cast(shot)]
    if cast:
        out.append(("Characters: " if grid else "Characters in frame: ") + ", ".join(cast) + ".")
    ph = pose_hint(shot, names)
    if ph:      # 姿态/动作句不进裁剪(2026-09-14)
        out.append((f"Poses: {ph}." if grid else f"Body poses and actions (draw exactly as stated): {ph}."))
    panel = panel_text(shot)
    if panel:
        out.append(f"What we see (start frame): {cut(panel, 'panel')}")
    else:
        content = clean_prose(shot.get("content"))
        action = clean_prose(shot.get("action"))
        sketch = clean_prose(shot.get("sketch"))
        if content:
            out.append(("" if grid else "What we see: ") + cut(content, "content"))
        if action and action not in content and (not clip or clip.get("action")):
            out.append("Action: " + cut(action, "action"))
        if sketch and (not clip or clip.get("sketch")):
            out.append("Composition: " + cut(sketch, "sketch"))
    ex = expression_hint(shot)
    if ex:
        out.append((f"Expressions: {ex}." if grid else f"Expressions and gestures to make readable: {ex}."))
    if shot.get("extras") and not grid:
        out.append(f"Background extras (loose figures only): {clean_prose(shot['extras'])}")
    npc = npc_sentence(shot, grid=grid, max_chars=max(40, clip.get("sketch") or 0) if clip else (120 if grid else None))
    if npc:
        out.append(npc)
    return out


def build_prompt(scene: dict, shot: dict, names: dict, note: str = "", with_refs: bool = True,
                 plain_style: bool = False, ref_names: list[str] | None = None,
                 layout: bool = False, whitebox_legend: list[tuple[str, str]] | None = None,
                 style: str = DEFAULT_SKETCH_STYLE) -> tuple[str, str]:
    """单镜提示词:风格句 →(构图底句)→ 逐镜描述(_shot_parts:景别规则/机位+地平线/焦段/出场/姿态/画面/神态/群众)
    → 本场轴线 → 地点短提示 → 修改意见。
    with_refs=False(comfyui 纯文生图)时整句风格提示换成 SKETCH_STYLE_PROMPT_TEXT_ONLY(不列举姿态、无否定句、人物按文字画)。
    layout=True:最后一张参考图是构图底——whitebox_legend 不为 None 时是白模机位首帧(彩色假人逐个点名),否则是手绘稿。"""
    # plain_style(comfyui / agentics,2026-09-19)带参考图:正向风格句 + 逐张点名的参考图句,不用含否定句/姿态列举的 SKETCH_STYLE_PROMPT
    pack = STYLE_PACKS[normalize_sketch_style(style)]      # 画风包(2026-09-30):film / konte
    if not with_refs:
        parts = [pack["text_only"]]
    elif plain_style:
        parts = [pack["text_only"][:-len(SKETCH_TEXT_ONLY_TAIL)] + ref_sentence_plain(ref_names or [])]
    else:
        parts = [pack["head"] + pack["refs"]]
    if layout:      # 构图底是最后一张参考图(调用方保证已挂)
        parts.append(whitebox_sentence(whitebox_legend) if whitebox_legend is not None else SKETCH_LAYOUT_SENTENCE)
    parts += _shot_parts(shot, names)
    if pack.get("accent"):
        parts.append(accent_sentence(shot))
    sd = screen_direction(scene, [shot], names)
    if sd:
        parts.append(f"Screen direction for this scene (keep left/right consistent): {sd}.")
    # 场景只靠文字(2026-09-11 用户拍板);只留一句短地点提示,放最后,不拼场景卡描述
    space = _space_hint(scene)
    if space:
        parts.append(f"Location (draw as simple shapes): {space}.")
    if note and note.strip():
        parts.append(f"Revision instruction (takes priority): {note.strip()}")
    negative = pack["negative"] if frame_cast(shot) else pack["negative"].replace(SKETCH_NEGATIVE_PEOPLE_TAIL, "")
    return " ".join(parts), negative


def _shrink(src: Path, cache: Path) -> Path:
    """参考图缩到 REF_MAX_EDGE 长边的 JPEG(按源路径+mtime 哈希缓存);PIL 不可用或失败则原图。"""
    try:
        from PIL import Image
    except Exception:
        return src
    cache.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(f"{src}|{int(src.stat().st_mtime)}|{REF_MAX_EDGE}".encode()).hexdigest()[:16]
    out = cache / f"{h}.jpg"
    if out.is_file():
        return out
    try:
        im = Image.open(src)
        im.thumbnail((REF_MAX_EDGE, REF_MAX_EDGE))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.save(out, "JPEG", quality=85)
        return out
    except Exception:
        return src


def collect_refs(base: Path, ep: str, scene: dict, shot: dict, catalog: dict, limit: int | None = None) -> list[Path]:
    """只带出场人物 sheet(≤MAX_CAST_REFS,limit 再封顶——comfyui / agentics 图生图能收的张数),缩小后返回绝对路径。
    不带场景图(俯视图会误导模型,场景靠文字)、不带风格参考图。"""
    cache = sketch_dir(base, ep) / "_refcache"
    out = []
    for cid in ref_cast_ids(base, shot, catalog, limit):
        rec = catalog["characters"].get(cid) or catalog["creatures"].get(cid) or {}
        out.append(_shrink(base / rec["file"], cache))
    return out


# ---------------- 白模机位构图底(2026-09-29,--whitebox-layout) ----------------
WHITEBOX_LAYOUT_DIR = "_whitebox"      # assets/storyboard/<ep>/_whitebox/<键>.jpg(按 camera.mp4 mtime+时刻缓存)
WHITEBOX_EDGE_INSET_S = 1 / 24         # 与 whitebox_stills 同:镜首向内收一帧,避免采到上一镜


def _whitebox_cameras(base: Path, ep: str) -> dict:
    """白模编译结果 directing/<ep>/whitebox/episode.json → {shot_id: (group, camera)}。"""
    epi = _read_json(base / "directing" / ep / "whitebox" / "episode.json") or {}
    out = {}
    for g in epi.get("groups") or []:
        for cam in (g.get("cameras") or []) if isinstance(g, dict) else []:
            if isinstance(cam, dict) and cam.get("shot_id"):
                out[cam["shot_id"]] = (g, cam)
    return out


def whitebox_layout_frame(base: Path, ep: str, shot: dict, cameras: dict | None = None) -> tuple[Path | None, list]:
    """本镜的白模机位首帧(取自已导出的 assets/whitebox/<ep>/<grp>/camera.mp4,对应 shot_list 定稿镜的首镜)与图例
    [(人物名, 颜色英文名)](只列本镜出场、在该组白模里有假人的)。没有定稿镜/没导出/抽帧失败返回 (None, [])。"""
    import shutil
    import subprocess
    cameras = _whitebox_cameras(base, ep) if cameras is None else cameras
    hit = next((cameras[f["shot_id"]] for f in shot.get("final") or [] if f.get("shot_id") in cameras), None)
    if not hit:
        return None, []
    group, cam = hit
    video = base / "assets" / "whitebox" / ep / str(group.get("group_id") or "") / "camera.mp4"
    ffmpeg = shutil.which("ffmpeg")
    if not video.is_file() or not ffmpeg:
        return None, []
    try:
        start, dur = float(cam.get("start") or 0), float(cam.get("duration_s") or 0)
    except Exception:
        return None, []
    t = round(start + min(WHITEBOX_EDGE_INSET_S, dur / 4 if dur > 0 else 0), 3)
    out_dir = sketch_dir(base, ep) / WHITEBOX_LAYOUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(f"{video}|{int(video.stat().st_mtime)}|{t}".encode()).hexdigest()[:10]
    out = out_dir / f"{shot['key']}_{h}.jpg"
    if not out.is_file():
        for old in out_dir.glob(f"{shot['key']}_*.jpg"):
            old.unlink(missing_ok=True)
        r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(t), "-i", str(video),
                            "-frames:v", "1", "-q:v", "3", str(out)], capture_output=True)
        if r.returncode != 0 or not out.is_file():
            return None, []
    from modules.whitebox_refs import color_name
    cast = set(shot.get("cast") or [])
    legend = [(str(a.get("label") or a.get("id")), color_name(a.get("color")))
              for a in group.get("actors") or [] if isinstance(a, dict) and a.get("id") in cast and a.get("color")]
    return out, legend


def whitebox_grid_layout(base: Path, ep: str, panels: list[tuple[dict, dict]], cols: int, rows: int) -> tuple[Path | None, list]:
    """宫格版:各格白模首帧按宫格排布拼成一张(缺帧的格留白),图例取并集;一格都没有返回 (None, [])。"""
    cameras = _whitebox_cameras(base, ep)
    frames, legend = [], []
    for _, shot in panels:
        f, lg = whitebox_layout_frame(base, ep, shot, cameras)
        frames.append(f)
        legend += [x for x in lg if x not in legend]
    if not any(frames):
        return None, []
    from PIL import Image
    cw, ch = 640, 360
    for f in frames:
        if f:
            w, h = Image.open(f).size
            ch = round(cw * h / w)
            break
    sheet = Image.new("RGB", (cw * cols, ch * rows), "white")
    for k, f in enumerate(frames):
        if f:
            sheet.paste(Image.open(f).convert("RGB").resize((cw, ch)), ((k % cols) * cw, (k // cols) * ch))
    out = sketch_dir(base, ep) / WHITEBOX_LAYOUT_DIR / f"grid_{panels[0][1]['key']}_{panels[-1][1]['key']}.jpg"
    sheet.save(out, "JPEG", quality=88)
    return out, legend


def grid_layout(n: int) -> tuple[int, int]:
    """按镜数选宫格:1 镜单张(调用方走单镜路径)、2–4 镜 2×2(GRID_MAX_PANELS=4,2026-09-12 起不再出 3×3);
    超过 4 镜只作兜底返回 3×3,正常调用方已按 GRID_MAX_PANELS 分批。返回 (cols, rows)。"""
    if n <= 1:
        return 1, 1
    if n <= 4:
        return 2, 2
    return 3, 3


def _short(s, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


GRID_PROMPT_MAX = 3600       # 宫格提示词字符上限(4 格合一,按档收紧逐格文字直到不超;2026-09-29 风格句/景别规则加长后由 2400 提到 3600)
# 收紧顺序:先压画面散文/动作/构图,景别规则、机位、焦段、姿态、神态不裁——草图要的是机位、比例、神态、动作。
_GRID_CLIPS = ({"content": 260, "action": 160, "sketch": 200, "note": 160, "panel": 400},
               {"content": 180, "action": 100, "sketch": 140, "note": 120, "panel": 300},
               {"content": 120, "action": 60, "sketch": 90, "note": 90, "panel": 220},
               {"content": 80, "action": 0, "sketch": 0, "note": 60, "panel": 160})


def build_grid_prompt(panels: list[tuple[dict, dict]], names: dict, cols: int, rows: int, aspect: str = "16:9",
                      max_chars: int = GRID_PROMPT_MAX, with_refs: bool = True,
                      whitebox_legend: list[tuple[str, str]] | None = None,
                      style: str = DEFAULT_SKETCH_STYLE) -> tuple[str, str]:
    """宫格提示词:风格总句 +(白模构图底句)+ 各场地点短提示与轴线一次 + 逐格「Panel k (row r, col c)」逐镜描述(_shot_parts)。
    panels = [(scene, shot)],≤ cols*rows;格数不满时说明剩余格留白(切分时只取前 n 格)。
    总长超 max_chars 时按 _GRID_CLIPS 逐档收紧画面散文。
    with_refs=False(agentics 文生图宫格)时风格句结尾换成 GRID_TEXT_ONLY_SENTENCE(人物按文字画)。
    whitebox_legend 不为 None:最后一张参考图是与宫格同排布的白模机位首帧拼图。"""
    n = len(panels)
    cells = cols * rows
    blank = f" The last {cells - n} cell(s) of the grid stay blank white." if n < cells else ""
    pack = STYLE_PACKS[normalize_sketch_style(style)]
    head = pack["grid_head"].format(cols=cols, rows=rows, n=cells, aspect=aspect or "16:9", blank=blank,
                                    refs=pack["grid_refs"] if with_refs else GRID_TEXT_ONLY_SENTENCE)
    if whitebox_legend is not None:
        head += " " + whitebox_sentence(whitebox_legend, grid=f", laid out as the same {cols}x{rows} grid with cells "
                                        "matching the panels one to one (a plain white cell has no blocking: draw that "
                                        "panel from its text)")
    prompt = ""
    for clip in _GRID_CLIPS:
        parts = [head]
        seen = []
        for sc, _ in panels:
            key = sc.get("scene_no")
            if key in seen:
                continue
            seen.append(key)
            loc = _space_hint(sc)
            sd = screen_direction(sc, [sh for s_, sh in panels if s_.get("scene_no") == key], names)
            if loc or sd:
                parts.append(f"Location {key} (draw as simple shapes): {loc or '-'}."
                             + (f" Screen direction (keep left/right consistent): {_short(sd, 200)}." if sd else ""))
        for k, (sc, shot) in enumerate(panels, 1):
            r, c = (k - 1) // cols + 1, (k - 1) % cols + 1
            seg = [f"Panel {k} (row {r}, column {c}):"]
            if len(seen) > 1:
                seg.append(f"Location {sc.get('scene_no')}.")
            seg += _shot_parts(shot, names, clip=clip, grid=True)
            if pack.get("accent"):
                seg.append(accent_sentence(shot, grid=True))
            note = str(shot.get("_note") or "").strip()
            if note:
                seg.append("Revision instruction: " + _short(note, clip["note"]))
            parts.append(" ".join(seg))
        prompt = " ".join(parts)
        if len(prompt) <= max_chars:
            break
    return prompt, pack["grid_negative"]


def collect_grid_refs(base: Path, ep: str, panels: list[tuple[dict, dict]], catalog: dict,
                      limit: int | None = None) -> list[Path]:
    """宫格模式参考图:各格(≤4)出场人物并集,按出场格数降序取前 GRID_MAX_CAST_REFS 张 sheet(缩小后);
    limit 再封顶(agentics 图生图 profile 能收的张数)。"""
    cap = GRID_MAX_CAST_REFS if limit is None else min(GRID_MAX_CAST_REFS, limit)
    cache = sketch_dir(base, ep) / "_refcache"
    freq: dict[str, int] = {}
    for _, shot in panels:
        for cid in frame_cast(shot):
            freq[cid] = freq.get(cid, 0) + 1
    refs: list[Path] = []
    for cid, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0])):
        rec = catalog["characters"].get(cid) or catalog["creatures"].get(cid) or {}
        if rec.get("file") and (base / rec["file"]).is_file():
            refs.append(_shrink(base / rec["file"], cache))
        if len(refs) >= cap:
            break
    return refs


def split_grid(src: Path, cols: int, rows: int, n: int, outs: list[Path], trim: float = GRID_CELL_TRIM) -> list[Path]:
    """把宫格图等分成 cols×rows 格,按行优先取前 n 格,四边各裁 trim 去格线,存到 outs[k](PNG)。返回写出的路径。"""
    from PIL import Image
    im = Image.open(src).convert("RGB")
    W, H = im.size
    cw, ch = W / cols, H / rows
    tx, ty = cw * trim, ch * trim
    written = []
    for k in range(min(n, cols * rows, len(outs))):
        r, c = k // cols, k % cols
        box = (int(c * cw + tx), int(r * ch + ty), int((c + 1) * cw - tx), int((r + 1) * ch - ty))
        tile = im.crop(box)
        outs[k].parent.mkdir(parents=True, exist_ok=True)
        tile.save(outs[k], "PNG", optimize=True)
        written.append(outs[k])
    return written


def grid_size(provider: str, aspect: str) -> str:
    """宫格图出图尺寸:所有渠道都按 3,686,400 像素当量(2560×1440)出,切 2×2 后每格约 1230×690(裁边后);不再压缩。"""
    try:
        rw, rh = (int(x) for x in str(aspect or "16:9").split(":"))
    except Exception:
        rw, rh = 16, 9
    pixels = 3_686_400
    w = -(-int((pixels * rw / rh) ** 0.5) // 8) * 8
    h = -(-(w * rh) // (rw * 8)) * 8
    return f"{w}x{h}"


def resolve_aspect(base: Path) -> str:
    """项目视频画幅(settings.json output.aspect_preset/aspect_custom);缺省 16:9。"""
    try:
        from modules.output_format import resolve_output
        cfg = _read_json(base / "settings.json") or {}
        aspect, _, _ = resolve_output(cfg)
        return aspect or "16:9"
    except Exception:
        return "16:9"


def sketch_size(provider: str, aspect: str) -> str:
    """出图尺寸:火山/BytePlus 的 Seedream 5.x 硬限「≥3,686,400 像素」(2560x1440 当量,与宿主手绘生图同口径),
    否则按 3686400 当量出、成图再压到 1280 长边;其它渠道走 1280x720 当量(genmedia ASPECT_SIZES,多数接口的最小档)。返回 "WxH"。"""
    try:
        rw, rh = (int(x) for x in str(aspect or "16:9").split(":"))
    except Exception:
        rw, rh = 16, 9
    pixels = 3_686_400 if provider in ("volcengine", "byteplus") else 921_600
    w = -(-int((pixels * rw / rh) ** 0.5) // 8) * 8
    h = -(-(w * rh) // (rw * 8)) * 8
    return f"{w}x{h}"


def animatic_status(base: Path, ep: str) -> dict:
    """动态样片现状(code/animatic.py 产物 assets/storyboard/<ep>/animatic.mp4 + animatic.json):
    存在与否、元数据、是否过期(storyboard.json / shot_list.json / 任一草图比样片新)、当前缺草图数。"""
    d = sketch_dir(base, ep)
    mp4, meta = d / "animatic.mp4", _read_json(d / "animatic.json") or {}
    idx = load_index(base, ep)
    done = [r for r in idx["shots"].values() if isinstance(r, dict) and r.get("status") == "done"
            and r.get("file") and (base / r["file"]).is_file()]
    latest = 0.0
    for f in (base / "directing" / ep / "storyboard.json", base / "directing" / ep / "shot_list.json"):
        if f.is_file():
            latest = max(latest, f.stat().st_mtime)
    for r in done:
        latest = max(latest, (base / r["file"]).stat().st_mtime)
    out = {"exists": mp4.is_file(), "path": mp4.relative_to(base).as_posix(), "sketches_done": len(done)}
    if mp4.is_file():
        st = mp4.stat()
        stale = latest > float(meta.get("inputs_mtime") or st.st_mtime) + 1
        stale_reason = "inputs" if stale else ""
        # 对白语音库(2026-09-13):开关开着且库指纹与样片记录的不一致(台词/音色变了、或样片出时还没开库)也算过期
        try:
            from modules import dialogue_tts as dt
            if dt.enabled(base):
                cur = dt.library_fingerprint(dt.load_manifest(base, ep))
                rec = (meta.get("dialogue_tts") or {}).get("fingerprint", "")
                if not stale and (cur != rec or not (meta.get("dialogue_tts") or {}).get("enabled")):
                    stale, stale_reason = True, "dialogue_tts"
        except Exception:  # noqa: BLE001
            pass
        out.update({"name": mp4.name, "size_mb": round(st.st_size / 1048576, 1), "mtime": int(st.st_mtime),
                    "url": f"/projects/{base.name}/{out['path']}?v={int(st.st_mtime)}",
                    "duration_s": meta.get("duration_s"), "shots": meta.get("shots"),
                    "missing_sketches": meta.get("missing_sketches"), "duration_source": meta.get("duration_source"),
                    "audio_tracks": meta.get("audio_tracks"), "created_at": meta.get("created_at"),
                    "dialogue_tts": meta.get("dialogue_tts") or {},
                    "stale": stale, "stale_reason": stale_reason})
    return out


def sketch_url(base: Path, rec: dict) -> str | None:
    f = base / str(rec.get("file") or "")
    if rec.get("file") and f.is_file():
        return f"/projects/{base.name}/{rec['file']}?v={int(f.stat().st_mtime)}"
    return None
