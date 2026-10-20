"""项目类型(2026-10-20):新建项目向导第一步选择,决定这个项目走哪条流程 DAG。

- film(影视剧,默认):小说 → 视频主流程,agents/workflow.yaml
- audio-to-video(音频配画):讲述音频 + 文稿 → 按音频生成画面,agents/workflows/audiovideo.yaml
- digital-human(数字人):音频母带 + 人物图片 → 说话人口型视频,agents/workflows/digitalhuman.yaml
- mashup(混剪):文稿 + 本地素材库(+ 可选讲述音频)→ 素材库切片配画,agents/workflows/mashup.yaml
- code-video(演示视频,2026-10-20):主题 / 文稿 + 风格(lemo-opuscar 43 种)→ 语言模型写网页逐帧渲染出片,
  不用视频生成模型,agents/workflows/codevideo.yaml(宿主工具链 modules/codevideo/ + code/codevideo.py)

后三种原是 VideoAgents-Plugins 仓库的同名插件,2026-10-20 并入主程序:成员 SOUL 进 agents/<类别>/,
DAG 与说明文档进 agents/workflows/(随桌面端打包),不再需要安装/启用;同名插件包在「插件」页标为已并入、不再注册。
类型存项目 settings.json#project.type,建项目时写定、设置接口不可改;没有这一段的存量项目按命名空间
产物推断(插件时期跑过的项目),都没有 = film。
"""
from __future__ import annotations

import re
from pathlib import Path

DEFAULT = "film"

# 每条:label(界面名,也进提示词)/ icon / workflow(流程权威 DAG)/ doc(说明文档)/
# categories + agents(本类型专属成员:整个类别 + 放在内置类别里的个别成员)/ outputs_ns(过程件命名空间)/
# first_node(宿主工具链自检节点)/ gates(人工签字点)/ inputs(输入约定,进总制片提示词)
TYPES: dict[str, dict] = {
    "film": {
        "label": "影视剧", "icon": "🎬", "workflow": "agents/workflow.yaml", "doc": "",
        "categories": (), "agents": (), "outputs_ns": "", "first_node": "", "gates": "",
        "inputs": "",
    },
    "audio-to-video": {
        "label": "音频配画", "icon": "🎙️", "workflow": "agents/workflows/audiovideo.yaml",
        "doc": "agents/workflows/audiovideo.md",
        "categories": ("15-audio-video",), "agents": ("11-qa/av-sync-qa",),
        "outputs_ns": "av", "first_node": "av0-ingest", "gates": "AVH1–AVH5",
        "inputs": "讲述音频(母带)在 refs/audio/,对应文稿在 refs/text/(一个音频 = 一个项目 = ep01);"
                  "画面风格与内容定位见 brief.md",
    },
    "digital-human": {
        "label": "数字人", "icon": "🧑‍💼", "workflow": "agents/workflows/digitalhuman.yaml",
        "doc": "agents/workflows/digitalhuman.md",
        "categories": ("17-digital-human",), "agents": ("11-qa/digital-human-sync-qa",),
        "outputs_ns": "digital-human", "first_node": "dh0-ingest", "gates": "无人工签字点",
        "inputs": "音频母带在 refs/audio/,人物图片在 refs/characters/,文稿可选(在 refs/text/;没有就按 DAG 派 dh0-transcribe);"
                  "人物与声音的对应说明见 brief.md「主要构想」",
        # 声音来源 = 克隆声音(project.voice_source=clone)时的输入约定,替换上一条
        "inputs_clone": "没有原始录音:台词稿在 refs/text/transcript.txt(每行「说话人:台词」),每个说话人的克隆声音样本在 "
                        "refs/voices/,人物图片在 refs/characters/;说话人与照片、样本的对应说明见 brief.md「主要构想」",
    },
    "mashup": {
        "label": "混剪", "icon": "✂️", "workflow": "agents/workflows/mashup.yaml",
        "doc": "agents/workflows/mashup.md",
        "categories": ("16-mashup",), "agents": ("11-qa/mashup-qa",),
        "outputs_ns": "mashup", "first_node": "mx0-ingest", "gates": "MH1–MH3",
        "inputs": "文稿在 refs/text/ 与 novel/original.txt(后者是 p0 的文本输入),讲述音频(可选)在 refs/audio/——"
                  "没有音频时按 DAG 头部「音频可选」条款先派 09-audio/narrator 按文稿 TTS 合成母带",
    },
    "code-video": {
        "label": "演示视频", "icon": "🖥️", "workflow": "agents/workflows/codevideo.yaml",
        "doc": "agents/workflows/codevideo.md",
        "categories": ("18-code-video",), "agents": ("11-qa/code-video-qa",),
        "outputs_ns": "codevideo", "first_node": "cv0-ingest", "gates": "CVH1 故事板(project.storyboard_review=false 时跳过)、CVH2 成片",
        "inputs": "主题 / 文稿在 refs/text/transcript.txt,内容定位在 brief.md;风格 = settings.json#project.code_style"
                  "(agents/workflows/codevideo/styles/<slug>/STYLE.md);用户素材(logo、照片、字体、录音、音乐)在 refs/ 各分类",
    },
}

# 原插件 manifest 里声明 stateless 的成员(11-qa/ 前缀本就无状态,不必列)
STATELESS_AGENTS = frozenset({
    "16-mashup/footage-scout", "16-mashup/footage-curator", "16-mashup/clip-cutter",
    "17-digital-human/avatar-generator",
})

# 已并入主程序的插件包名 → 项目类型(插件名与类型 id 相同)
INTEGRATED_PLUGINS = frozenset(t for t in TYPES if t != DEFAULT)

# 数字人的声音来源(2026-10-20):recording = 用户上传的原始录音作母带(默认);clone = 没有录音,
# 按台词稿逐句用 refs/voices/ 里的样本克隆合成母带(dh0-voice,code/dialogue_clone.py)
VOICE_SOURCES = ("recording", "clone")

# 演示视频的项目设置(2026-10-20,建项目时写定):code_style = 风格 slug(lemo-opuscar 风格库);storyboard_review = 先审故事板
# (默认开,CVH1);narration / subtitles = 旁白 / 字幕;length_s = 目标片长;fps;music_source = code(代码作曲,默认)
# | model(按「生成模型」页音乐设置生成)| library(从项目音乐库 / 用户音乐选曲;三期 2026-10-20)。后两种是外来音乐:
# 宿主 codevideo.py music-fit 适配到片长并测节拍网格,cv2-beatsync 把时间轴对到拍上。画幅 / 语言 / 成片分辨率沿用 output 段
CODE_VIDEO_DEFAULTS = {"code_style": "", "storyboard_review": True, "narration": True, "subtitles": True,
                       "length_s": 45, "fps": 24, "music_source": "code"}
CODE_VIDEO_FPS = (24, 25, 30)
CODE_VIDEO_MUSIC = ("code", "model", "library")
CODE_VIDEO_LENGTH = (5, 600)

_LIB_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,79}")


def normalize(value) -> str:
    v = str(value or "").strip()
    return v if v in TYPES else DEFAULT


def infer(project_dir: Path) -> str:
    """存量项目(无 settings.json#project 段)按插件时期留下的命名空间产物推断类型;只看文件是否存在。"""
    if (project_dir / "av" / "audio_map.json").is_file():
        return "audio-to-video"
    if any((project_dir / "mashup" / f).is_file()
           for f in ("audio_map.json", "footage_source.json", "beat_track.json")):
        return "mashup"
    if (project_dir / "digital-human" / "input_manifest.json").is_file():
        return "digital-human"
    return DEFAULT


def resolve(saved, project_dir: Path) -> dict:
    """settings.json#project 段规范化:{"type": <id>, ...类型附带的建项目输入}。"""
    block = saved if isinstance(saved, dict) else {}
    out = {"type": normalize(block["type"]) if block.get("type") else infer(project_dir)}
    if out["type"] == "mashup":
        out["footage_libraries"] = clean_libraries(block.get("footage_libraries"))
    if out["type"] == "digital-human":
        out["voice_source"] = block.get("voice_source") if block.get("voice_source") in VOICE_SOURCES else "recording"
    if out["type"] == "code-video":
        out.update(code_video_settings(block, strict=False))
    return out


def code_video_settings(block, strict: bool = True) -> dict:
    """演示视频设置规范化;strict 时非法值抛 ValueError(建项目接口转 400),否则回落默认。"""
    block = block if isinstance(block, dict) else {}
    out = dict(CODE_VIDEO_DEFAULTS)

    def bad(msg):
        if strict:
            raise ValueError(msg)

    style = str(block.get("code_style") or "").strip()
    if style:
        from modules.codevideo import style_ids
        if style in style_ids():                  # 完整风格 43 个 + OPUSCAR 98 轻量风格 o98-01…98
            out["code_style"] = style
        else:
            bad(f"code_style 不在风格库:{style}")
    for k in ("storyboard_review", "narration", "subtitles"):
        if k in block:
            if isinstance(block[k], bool):
                out[k] = block[k]
            else:
                bad(f"{k} 须为 true / false")
    if "length_s" in block:
        try:
            v = float(block["length_s"])
            if not CODE_VIDEO_LENGTH[0] <= v <= CODE_VIDEO_LENGTH[1]:
                raise ValueError
            out["length_s"] = int(v) if v == int(v) else round(v, 1)
        except (TypeError, ValueError):
            bad(f"length_s 须在 {CODE_VIDEO_LENGTH[0]}–{CODE_VIDEO_LENGTH[1]} 秒之间")
    if "fps" in block:
        if block["fps"] in CODE_VIDEO_FPS:
            out["fps"] = block["fps"]
        else:
            bad(f"fps 只能是 {CODE_VIDEO_FPS}")
    if "music_source" in block:
        if block["music_source"] in CODE_VIDEO_MUSIC:
            out["music_source"] = block["music_source"]
        else:
            bad(f"music_source 只能是 {CODE_VIDEO_MUSIC}")
    return out


def clean_libraries(value) -> list[str]:
    libs = []
    for v in value if isinstance(value, (list, tuple)) else []:
        v = str(v or "").strip()
        if _LIB_RE.fullmatch(v) and v not in libs:
            libs.append(v)
    return libs


def of_agent(agent_id: str) -> str | None:
    """专属成员所属的项目类型;内置共用成员返回 None。"""
    cat = (agent_id or "").split("/")[0]
    for tid, t in TYPES.items():
        if cat in t["categories"] or agent_id in t["agents"]:
            return tid
    return None


def creation_overrides(type_id: str) -> dict:
    """非影视剧类型建项目时强制的设置:这些开关只服务小说→视频主流程,在本类型流程里要么无对应节点、
    要么与「母带即成片声轨、零重编码」冲突(片头片尾/集尾淡出会重编码并推移讲述起点)。"""
    if normalize(type_id) == DEFAULT:
        return {}
    return {
        "output": {"narration_enabled": False, "dialogue_tts": False, "dialogue_voice": "native",
                   "sound_split": "off", "npc_staging": "off", "spatial_blocking": False},
        "duration": {"episode_minutes": "auto", "long_take": False},
        "packaging": {"intro_enabled": False, "outro_enabled": False, "teaser_enabled": False},
        "transitions": {"mode": "minimal", "episode_close": {"type": "hard_cut"}},
    }


def label(type_id: str) -> str:
    return TYPES[normalize(type_id)]["label"]


def _members(t: dict, agent_ids) -> list[str]:
    return sorted(a for a in agent_ids
                  if a.split("/")[0] in t["categories"] or a in t["agents"])


def orchestrator_section(block: dict, proj_rel: str, agent_ids) -> str:
    """总制片提示词:非影视剧项目的流程权威与调度纪律(原「已启用插件」段的项目级版本)。"""
    tid = normalize((block or {}).get("type"))
    if tid == DEFAULT:
        return ""
    t = TYPES[tid]
    clone = tid == "digital-human" and (block or {}).get("voice_source") == "clone"
    lines = [
        f"\n\n## 项目类型:{t['label']}({tid};新建项目时选定,settings.json#project.type,不可更改)",
        f"- 本项目不走「小说→视频」主流程。流程权威 DAG = `{t['workflow']}`(与 agents/workflow.yaml 同构的机器可读 DAG,"
        f"头部注释写明本流程的特殊授权、输入约定与宿主工具链,派单前通读);说明文档 `{t['doc']}`",
        f"- 输入约定:{t['inputs_clone'] if clone else t['inputs']}",
    ]
    if clone:
        lines.append(
            "- 声音来源 = 克隆声音(settings.json#project.voice_source=clone):用户没有原始录音,`dh0-voice` 节点生效——"
            "派给 09-audio/voice-generation,instruction 写明「只跑宿主 CLI `python3 code/dialogue_clone.py plan → synth → check`;"
            "说话人与样本的对应按 brief.md 人物对应说明写进 digital-human/voice_cast.json(或 --voice 说话人=refs/voices/<文件>);"
            "台词一字不改,不自写 TTS / 拼接脚本;plan 报渠道不能克隆、缺样本或缺台词稿就停下上报」。"
            "dh0-ingest 依赖它:母带就是 dh0-voice 产出的 assets/audio/master/{ep}.mp3(不从 refs/audio 复制,只核对与 "
            "digital-human/voice_clone.json 指纹一致),文稿取 digital-human/transcript_cloned.txt(逐段带时间戳与人物),"
            "dh0-transcribe 跳过。台词稿或样本改了 = 重跑 dh0-voice,其下游全部标脏")
    elif tid == "digital-human":
        lines.append("- 声音来源 = 原始录音:`dh0-voice`(克隆配音)不适用,立项时置 skipped")
    libs = (block or {}).get("footage_libraries") or []
    if tid == "mashup":
        lines.append(
            "- 新建项目时选定的素材库:" + ("、".join(libs) if libs else "(未选)")
            + "——写 mashup/footage_source.json 时以此为准;用户在指令里另行指定时以用户指令为准"
            + ("" if libs else ";未选且用户也没指明时必须先向用户要库名,不得替用户选"))
    if tid == "code-video":
        cv = code_video_settings(block, strict=False)
        lines.append(
            f"- 演示视频设置(settings.json#project,建项目时写定):风格 `{cv['code_style'] or '(未选)'}`"
            + ("(轻量风格:OPUSCAR 98 的一段风格提示,没有完整 STYLE.md 与样片;cv0 锁定时宿主生成 codevideo/style/STYLE.md,"
               "导演须在 TREATMENT.md「风格补全」一节补齐提示没写到的部分)" if str(cv["code_style"]).startswith("o98-")
               else f"(agents/workflows/codevideo/styles/{cv['code_style'] or '<slug>'}/STYLE.md)")
            + f"、目标片长约 {cv['length_s']} 秒、"
            f"{cv['fps']} fps、旁白{'开' if cv['narration'] else '关'}、字幕{'开' if cv['subtitles'] else '关'}、"
            f"先审故事板{'开' if cv['storyboard_review'] else '关'}、配乐 "
            + {"code": "代码作曲(code-composer 写作曲脚本)", "model": "按「生成模型」页音乐设置生成(外来音乐,须对拍)",
               "library": "从项目音乐库 / 用户音乐选曲(外来音乐,须对拍)"}[cv["music_source"]]
            + "。按这些设置裁定 DAG 条件节点:"
            + ("cv1-board 与 gc1(CVH1)照常派" if cv["storyboard_review"] else "cv1-board 与 gc1(CVH1)置 skipped")
            + ";" + ("cv2-voice 照常派" if cv["narration"] else "cv2-voice 置 skipped(全片无旁白)")
            + ";" + ("cv2-beatsync 置 skipped(代码作曲按时间轴写谱,天然对齐)" if cv["music_source"] == "code"
                     else "cv2-beatsync 照常派(music-fit 测出节拍网格后把时间轴对到拍上,cv3 画面在它之后才开工)")
            + ("" if cv["code_style"] else ";风格未选时 cv0-ingest 停下向用户要风格,不得替用户选"))
        lines.append(
            "- 本类型不用任何视频生成模型:画面由 scene-coder 写的网页(window.render(t))经宿主 CLI "
            "`python3 code/codevideo.py` 逐帧渲染;一个项目 = 一部片 = ep01;段(timeline.json sections)= 组(grp)= 场次,"
            "cv3-sec 按组扇出")
    lines += [
        f"- 立项/排产:生成或改写 {proj_rel}/runs/dag.json 时,主流程节点照 agents/workflow.yaml 实例化(p0-init/g0 照常),"
        f"再把 `{t['workflow']}` 的全部节点并入同一份 dag.json,并在**同一次改动**中按该文件顶层 `main_dag_on_start.skip` "
        f"把清单内主流程节点置 skipped(skip_reason 写「项目类型 {tid} 不适用,可恢复」;条件白名单同 WORKFLOW.md §10.3 第 6 条);"
        "每个并入节点显式写 phase 字段(取所属 phase 的 id);改完照常 `dagcheck.py --strict`",
        "- 本类型专属成员:" + "、".join(_members(t, agent_ids))
        + "(派单方式与内置成员完全相同);其余工位按 DAG 节点写的 agent 复用内置团队,"
        "工单 instruction 写明该节点在本流程里的口径(DAG 节点注释里的机检替换、零重编码等),不能指望被复用的成员自己知道",
        f"- 第一个节点 {t['first_node']} 负责宿主工具链自检;自检失败就停在该节点上报用户,不得绕过",
        "- orchestrator 只拆单、派单、等待和验收,**禁止直接执行本类型成员的生产命令**,也禁止通过改写 "
        "VIDEOAGENTS_AGENT 冒充成员绕过职责边界;耗时命令必须留在被派发的成员任务内前台完成",
        "- 工具/命令仍为 in_progress 时对应节点只能保持 running;不得结束任务后声称“后台继续”。外部异步渠道"
        "必须按 DAG 的最小生成单元派单并保留可恢复回执,等待真实产物与 validation 通过后才能标 done",
        f"- 人工签字点:{t['gates']}(有签字点的一律 `--sign`,与 H1–H5 同规格);随 skip 清单挂起的主流程闸门"
        "(H1–H5、H3S/H3A/H3V/H3B/H3P 等)不发签字单",
        "- 运行提示词里按项目设置注入的主流程条款(旁白、对白配音、白模、过场设计、多集推进、片头片尾等)只约束主流程节点;"
        "与本类型 DAG 头部授权冲突时以 DAG 为准",
    ]
    return "\n".join(lines)


def member_section(block: dict, agent_id: str, proj_rel: str) -> str:
    """成员提示词:本类型专属成员的团队身份;被本类型流程复用的内置成员加一条口径提醒。影视剧项目返回空串。"""
    tid = normalize((block or {}).get("type"))
    if tid == DEFAULT:
        return ""
    t = TYPES[tid]
    if of_agent(agent_id) == tid:
        ns = (f"\n- 过程件命名空间:{proj_rel}/{t['outputs_ns']}/ —— 工单显式指定其他路径(如 DAG 授权的直改正史 "
              "bible/、story/、directing/、assets/、edit/)时以工单为准;runs/、qa/defects/ 等通用运行记录不受此限")
        return (f"\n\n## 你属于「{t['label']}」项目类型团队({tid})\n"
                f"- 流程权威文件:{t['workflow']}(需要时自行阅读);说明文档:{t['doc']}{ns}\n"
                "- 团队通用纪律对本类型成员同等生效:工单格式(WORKFLOW.md §6)、运行记录三件套(§6.1)、"
                "质量三道闸与缺陷单(§7)、文件名 ASCII 红线(§1 原则 9)、Bible 冲突只上报不擅改")
    return (f"\n\n## 本项目类型:{t['label']}({tid})\n"
            f"- 本项目走 `{t['workflow']}`,不是小说→视频主流程;你在这里被复用为该流程的一个工位。"
            "工单 instruction / acceptance 与你 SOUL 中主流程默认口径冲突时(如母带零重编码、整秒切分、机检替换),"
            "以工单与该 DAG 节点注释为准;拿不准就上报 orchestrator,不要套用主流程口径")
