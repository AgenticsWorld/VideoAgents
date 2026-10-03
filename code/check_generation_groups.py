#!/usr/bin/env python3
"""check_generation_groups.py — 生成组(generation_groups)机检 + 草案分组工具。

机检规则(WORKFLOW.md §7A / shot-planning SOUL):
  1. groups_cover_all_shots      组覆盖全部镜号,不重不漏
  2. group_shots_contiguous_same_scene  组内镜号连续且同 scene_id
  3. group_duration_4_15_int     total_duration_s ∈ [4,15] 整数,且 = Σ组内 duration_s
  4. group_characters_lte_4      characters_union ≤4
  5. continuity_from_chain       首组 null,其余指向前一组 group_id
  5b. group_density_ok           密集组(平均镜长 < 1.5s)总时长 ≤ 15s(2026-10-03 时间尺,用户拍板;modules/time_cost.py;
                                 存量 shot_list(日期早于 2026-10-03)只 WARN)

§7D ① 机检(workflow.yaml p6-shots validation,默认开启,--skip-7d 跳过):
  6. narration_anchors_cover_all       narration.md 条目 100% 有挂点;挂点镜/组引用合法
  7. narration_window_gte_est_x1.15    可用画面窗口 window_s ≥ est_duration_s×1.15,
                                       且不超挂点镜区间物理时长
  8. audio_plan_complete               每组 audio_plan ∈ {dialogue,voice_over(旧名 narration_over),ambient_only}
                                       且与台词事实一致(有画内句=dialogue;无画内句但有旁白挂点或画外/V.O. 句=voice_over;
                                       皆无=ambient_only);ambient_only 必附 silent_rationale;has_dialogue 须=组内有画内句
  8b. speakers_le_3                    §8A(2026-10-03 落成代码):每组画内说话人 ≤3,placement=os|vo 的句不计
  8c. placement_valid 系列             声画分离(2026-10-03,docs/sound_split.md):委托 modules/offscreen_lines.validate
                                       (placement_valid / placement_speaker_is_cast / placement_reason_valid / offscreen_fit /
                                       post_voice_no_overlap);项目 output.sound_split=off 时 shot_list 不得有 os/vo 句
  (dialogue_est_fits_group_x0.7 需 screenplay 对白层估时与角色语速,由 code/check_dialogue_fit.py 执行——
   2026-08-30 起为 p6-dialogue-fit 节点的宿主 CLI,不再由 shot-planning 自查)
  项目「📤 输出设置」旁白开关(output.narration_enabled)关闭时:6/7 跳过(skipped: narration off),
  shot_list 不得残留 narration_anchors;8 照常但禁 narration_over(无对白组一律 ambient_only)。

组间转场机检 transition_ok(2026-08-28,WORKFLOW.md §9C;默认开启,--skip-transition 跳过):
  9. transition_type_valid            组 transition_in.type ∈ 受控枚举(缺省 = hard_cut);可渲染类型
                                       (dissolve/fade_black/fade_white/dip_black/dip_white)duration_s 落在各自范围;
                                       首组只能 hard_cut / fade_black / fade_white(淡入),没有前组可叠
  10. transition_reason_required      非 hard_cut(含标注型 smash_cut/match_cut)必填 reason 与 intent(可溯 directing_plan 转场清单)
  11. transition_budget_le_1pct       Σ可渲染转场 duration_s ≤ 集预算 budget_s × 1%
  11b. transition_pad_valid           节奏垫片(2026-09-17,§9C):hold_s(组前黑场停留)/ freeze_s(前组尾帧定格)各 ∈ [0,3]s,
                                       hold_s 只配 hard_cut / dip_black / fade_black,hold_audio ∈ sustain|fade|mute,
                                       首组不得 hold/freeze;带垫片必填 reason;Σ(hold+freeze) ≤ 集预算 5%
  11c. transition_join_valid          过场设计(2026-09-24,§9C 二期):dissolve 可带 join.style(受控 xfade 风格族:wipe/blur_through/
                                       zoom_through/iris/pixelize/fadegrays);其它类型不配 style
  11d. transition_insert_valid        组边界插入段 inserts[](title_card 字幕卡 / establishing 定场空镜 / timelapse 时光流转 / bridge 生成式桥接):
                                       kind 在枚举内、单段 ∈ [0.5,4]s、单边界 Σ ≤ 6s、join_out 在枚举内、首组不得插入;
                                       Σ插入 ≤ 集预算 × 项目「过场模式」预算%(极简 0 / 经典 8 / 电影感 10 / 自定义自填);
                                       overlay_card(叠字幕,不变长)duration_s ≤ 6s;bridge / i2v 定场须给 clip 文件路径(assets/transitions/)
  11f. motion_pair_valid              三期(2026-09-26):成对运镜 motion_pair {out, in, speed}:out/in 在 MOTION_PAIRS 配对表内且互为配对,
                                       type ∈ hard_cut/dissolve,与 inserts / bridge 互斥,首组不得,须写 reason(写进两侧组 prompt:code/sync_motion_pairs.py)
  11g. transition_audio_lead_valid    四期(2026-09-26):音先入 audio_lead_s ∈ (0,1],只配无 inserts / 无 hold_s 的 hard_cut/dissolve,首组不得,须写 reason
                                       (由 audio-mixing 按 mix_basis sources boundaries[].audio_lead_s 摆位,画面与 timemap 不动)
  11e. transition_close_valid         集尾收束(2026-09-25,§9C):shot_list 顶层可选 episode_close {type: hard_cut|fade_black|fade_white|cut_black|cut_white,
                                       duration_s ∈ [0.3,3](淡出类), hold_s ∈ [0,3](淡出后黑/白场停留;切黑类须 >0), hold_audio ∈ fade|mute};缺省 = 项目设置
                                       settings.json#transitions.episode_close(默认淡出到黑 1.0s + 黑场 0.5s);hard_cut = 显式不处理(停在末帧)
  12. narrative_block_paired          narrative_block 同 id 的组必须连续,role 序列 start[/middle…]/end(单组 single);
                                       块首组必有 transition_in(可为显式 hard_cut + reason),块尾组的下一组同样必有

CLI:
  python3 code/check_generation_groups.py <shot_list.json>            # 机检已有分组
  python3 code/check_generation_groups.py <shot_list.json> --propose  # 按贪心规则生成草案分组并打印
  python3 code/check_generation_groups.py <shot_list.json> --propose --write  # 草案写回 shot_list(新增字段)
  python3 code/check_generation_groups.py <shot_list.json> --narration <narration.md>  # 显式指定旁白稿

narration.md 缺省按数据布局自动推导(directing/epNN/shot_list.json →
story/episodes/epNN/narration.md),推导不到且未 --skip-7d 视为机检失败。
草案分组只是兜底基线:正式流程中分组由 storyboard(节拍)起草、shot-planning 定稿,
本脚本的贪心结果不含节拍判断,仅供机检自测与试点手工分组的参照。
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import time_cost as _tc  # noqa: E402  密集组限长(2026-10-03)
try:
    from modules import offscreen_lines as _osl  # noqa: E402  声画分离(2026-10-03):placement on|os|vo
except Exception:  # pragma: no cover
    _osl = None

MAX_GROUP_S = 15              # 默认=Seedance 2.0 单次生成上限;实际以项目「视频模型设置」
                              # settings.json 的 shot_group.max_group_s 为准(main 里覆盖,4-30)
MIN_GROUP_S = 4
MAX_CHARS = 4
MAX_SPEAKERS = 3              # §8A:组内画内说话人 ≤3(Seedance reference_audio 上限;画外 / V.O. 句不计,2026-10-03)
WINDOW_FACTOR = 1.15          # §7D ①:窗口 ≥ est_duration_s×1.15
# voice_over(2026-10-03 声画分离)= 无画内句但有旁白挂点或画外 / V.O. 句;narration_over 为其旧名(存量照认)
AUDIO_PLANS = ("dialogue", "voice_over", "narration_over", "ambient_only")
# —— 组间转场契约(shot_list.generation_groups[].transition_in,2026-08-28;render_transitions.py 同源)——
# 可渲染 5 种由宿主 CLI code/render_transitions.py 在 Phase 9 实施(pad 补偿,总时长不变);
# 标注型 2 种不渲染(= 硬切),只供 continuity/QA 核构图对位;缺省 = hard_cut
TRANSITION_RENDERABLE = {
    "dissolve": (0.25, 1.0),      # 叠化(xfade=fade)
    "fade_black": (0.3, 1.5),     # 前组尾淡出到黑,本组硬入;首组 = 从黑淡入
    "fade_white": (0.3, 1.5),     # 同上,白
    "dip_black": (0.3, 1.5),      # 前组淡出到黑 + 本组从黑淡入(xfade=fadeblack)
    "dip_white": (0.3, 1.5),      # 同上,白(xfade=fadewhite)
}
TRANSITION_ANNOTATION = ("smash_cut", "match_cut")
TRANSITION_TYPES = ("hard_cut",) + tuple(TRANSITION_RENDERABLE) + TRANSITION_ANNOTATION
TRANSITION_INTENTS = ("flashback_in", "flashback_out", "time_skip", "scene_change", "montage",
                      "dream_in", "dream_out", "chapter", "episode_open", "other")
TRANSITION_BUDGET_RATIO = 0.01     # Σ可渲染转场时长 ≤ 集预算 1%
# —— 节奏垫片(2026-09-17,§9C):transition_in 可选 hold_s(本组前黑场停留)/ freeze_s(前组尾帧定格)/ hold_audio
#    由 render_transitions.py 在组边界**插入**帧(成片变长,声轨/字幕按 timemap 重映射);后期页「插黑 / 定格」写入
PAD_MAX_S = 3.0
PAD_BUDGET_RATIO = 0.05            # Σ(hold_s+freeze_s) ≤ 集预算 5%
HOLD_TYPES = ("hard_cut", "dip_black", "fade_black")     # 黑场停留只配「到黑」的转场
HOLD_AUDIO = ("sustain", "fade", "mute")
# —— 过场设计(2026-09-24,§9C 二期;docs/transition_design.md):transition_in 扩 join.style / inserts[] / overlay_card / audio_lead_s ——
# join.style 只配 dissolve:把 xfade=fade 换成风格族里的模式(render_transitions.xfade_name 同源)
JOIN_STYLES = {
    "wipe": {"direction": ("left", "right", "up", "down"), "softness": ("hard", "slide", "smooth")},
    "blur_through": {},       # xfade=hblur
    "zoom_through": {},       # xfade=zoomin
    "iris": {"direction": ("open", "close")},   # circleopen / circleclose
    "pixelize": {},           # 梦境/数字感
    "fadegrays": {},          # 回忆去色
}
# —— 集尾收束(2026-09-25,§9C):shot_list 顶层 episode_close(可选)——最后一组尾部淡出到黑/白 + 可选黑/白场停留;
#    render_transitions.py 在成片末尾施加(画面),finalize_episode.py 在同一时刻淡出外挂声轨;不进 timemap(尾部插帧不平移任何时刻)、
#    不进混音边界层(黑场段声轨由 finalize 补静音)。缺省 = 项目设置 settings.json#transitions.episode_close;hard_cut = 显式不处理
EPISODE_CLOSE_TYPES = ("hard_cut", "fade_black", "fade_white", "cut_black", "cut_white")
EPISODE_CLOSE_FADES = ("fade_black", "fade_white")     # 末组尾淡出 duration_s 到黑/白,再停留 hold_s
EPISODE_CLOSE_CUTS = ("cut_black", "cut_white")        # 突然黑屏(悬念收束):不淡出,末帧直接切到黑/白场停留 hold_s(须 >0);声音默认同刻切断(mute)
EPISODE_CLOSE_DURATION = (0.3, 3.0)
EPISODE_CLOSE_HOLD_MAX_S = 3.0
EPISODE_CLOSE_AUDIO = ("fade", "mute")
EPISODE_CLOSE_DEFAULT = {"type": "fade_black", "duration_s": 1.0, "hold_s": 0.5, "hold_audio": "fade"}
EPISODE_CLOSE_CUT_DEFAULT = {"hold_s": 1.0, "hold_audio": "mute"}
INSERT_KINDS = ("title_card", "establishing", "timelapse", "bridge")
# —— 三期(2026-09-26):成对运镜 motion_pair {out, in, speed?}——前组尾镜以 out 运镜带出画面、本组首镜以 in 运镜接入(写进两侧组 prompt,
#    code/sync_motion_pairs.py,机检 motion_pair_bound);out → in 的合法配对固定如下(同向延续或推拉互补);与 inserts / bridge 互斥,
#    type 只能 hard_cut / dissolve(运镜对接本身就是过场,不再叠黑白场)
MOTION_PAIRS = {
    "pan_left": "pan_left", "pan_right": "pan_right",           # 同向横摇延续
    "tilt_up": "tilt_up", "tilt_down": "tilt_down",             # 同向俯仰延续
    "whip_pan_left": "whip_pan_left", "whip_pan_right": "whip_pan_right",   # 甩镜同向
    "dolly_left": "dolly_left", "dolly_right": "dolly_right",   # 横移延续
    "push_in": "pull_out", "pull_out": "push_in",               # 推进 ↔ 拉出互补
}
MOTION_SPEEDS = ("slow", "medium", "fast")
# —— 四期(2026-09-26):音先入 audio_lead_s(J-cut)——本组原生声轨比画面早 0–1 s 进入,由 audio-mixing 按 mix_basis sources 的
#    boundaries[].audio_lead_s 摆位(render_transitions 不动画面、不动 timemap);只配无插入段、无黑场停留的 hard_cut / dissolve 边界
AUDIO_LEAD_TYPES = ("hard_cut", "dissolve")
INSERT_MIN_S, INSERT_MAX_S = 0.5, 4.0          # 单段
BOUNDARY_INSERT_MAX_S = 6.0                    # 单边界 Σinserts
INSERT_JOINS = ("hard_cut", "dissolve", "dip_black", "dip_white", "fade_black", "fade_white")   # 插入段 → 下一段的接缝
INSERT_JOIN_DEFAULT_S = 0.4
INSERT_AUDIO = ("mute", "sustain", "bed")
CARD_BG = ("black", "white", "color", "blur_prev")
ESTABLISHING_MODES = ("pano_sweep", "plate_kenburns", "i2v")
OVERLAY_POSITIONS = ("bottom_left", "bottom_right", "center", "top_left", "top_right")
OVERLAY_MAX_S = 6.0
AUDIO_LEAD_MAX_S = 1.0
INSERT_BUDGET_DEFAULT_PCT = 8.0               # 读不到项目「过场模式」时的兜底(经典档)
BLOCK_KINDS = ("flashback", "dream", "montage", "imagination")
BLOCK_ROLES = ("start", "middle", "end", "single")
# narration.md 条目头:[N-xx | anchor: 场景锚 | est_duration_s: 秒 | source: 章#段]
NARR_ITEM_RE = re.compile(
    r"^\[(N-\d+)\s*\|\s*anchor:\s*[^|\]]+\|\s*est_duration_s:\s*([\d.]+)", re.M)


def project_max_group_s(shot_list_path: Path) -> int:
    """项目「视频模型设置」的生成组时长上限:directing/epNN/shot_list.json →
    项目根 settings.json 的 shot_group.max_group_s;读不到回落 15(Seedance 2.0 口径)。"""
    try:
        st = json.loads((shot_list_path.resolve().parents[2] / "settings.json").read_text())
        v = int(round(float((st.get("shot_group") or {})["max_group_s"])))
        return v if 4 <= v <= 30 else 15
    except Exception:
        return 15


def project_narration_enabled(shot_list_path: Path) -> bool:
    """项目「📤 输出设置」旁白开关(output.narration_enabled,默认关;未配置过的存量项目也视为关):关=用户约定全片无任何旁白,
    §7D ① 的 narration_anchors 系列机检跳过,audio_plan 禁 narration_over。"""
    try:
        st = json.loads((shot_list_path.resolve().parents[2] / "settings.json").read_text())
        return (st.get("output") or {}).get("narration_enabled") is True
    except Exception:
        return False


def project_root_of(shot_list_path: Path) -> Path:
    return shot_list_path.resolve().parents[2]


def project_sound_split(shot_list_path: Path) -> str:
    """项目「📤 输出设置」声画分离 output.sound_split ∈ off|script_only|auto(默认 auto,2026-10-03)。"""
    if _osl is not None:
        try:
            return _osl.mode(project_root_of(shot_list_path))
        except Exception:
            pass
    try:
        st = json.loads((project_root_of(shot_list_path) / "settings.json").read_text())
        m = (st.get("output") or {}).get("sound_split")
        return m if m in ("off", "script_only", "auto") else "auto"
    except Exception:
        return "auto"


def line_placement(ln) -> str:
    """对白行 placement(缺省 / 非法 = on;字符串写法的旧对白行 = on)。"""
    if not isinstance(ln, dict):
        return "on"
    if _osl is not None:
        return _osl.placement(ln)
    p = str(ln.get("placement") or "").strip().lower()
    return p if p in ("on", "os", "vo") else "on"


def shot_onscreen_lines(shot: dict) -> list:
    """一镜里有文本的画内台词行(dict 形态);旧字符串写法的行视同画内。"""
    out = []
    for ln in shot.get("dialogue_lines") or []:
        if isinstance(ln, dict):
            if str(ln.get("text") or ln.get("line") or "").strip() and line_placement(ln) == "on":
                out.append(ln)
        elif isinstance(ln, str) and ln.strip():
            out.append({"text": ln.strip(), "speaker": (re.match(r"^(?:S\d+/)?(CHAR-\d+)", ln.strip()) or [None, None])[1]})
    return out


def shot_offscreen_lines(shot: dict) -> list:
    return [ln for ln in shot.get("dialogue_lines") or []
            if isinstance(ln, dict) and str(ln.get("text") or ln.get("line") or "").strip() and line_placement(ln) != "on"]


def shot_has_onscreen_dialogue(shot: dict) -> bool:
    """有画内句 = 对白镜;没有 dialogue_lines 字典行的旧镜表退回 is_dialogue。"""
    lines = [ln for ln in shot.get("dialogue_lines") or [] if isinstance(ln, dict)]
    if lines:
        return bool(shot_onscreen_lines(shot))
    return bool(shot.get("is_dialogue")) and not shot_offscreen_lines(shot)


def _speaker_of(ln, names: dict | None) -> str:
    raw = str(ln.get("speaker") or ln.get("char") or ln.get("character_id") or "").strip()
    m = re.search(r"((?:CHAR|CRE)-\d+)", raw)
    if m:
        return m.group(1)
    for k in ("speaker_char", "character_id"):
        m = re.search(r"((?:CHAR|CRE)-\d+)", str(ln.get(k) or ""))
        if m:
            return m.group(1)
    if names:
        hit = names.get(raw) or names.get(re.sub(r"[〔【(\[（].*$", "", raw).strip())
        if hit:
            return hit
    return raw


def check_speakers(shot_list: dict, names: dict | None = None) -> list[str]:
    """speakers_le_3(§8A,2026-10-03 落成代码):每组**画内**说话人 ≤ MAX_SPEAKERS;画外 / V.O. 句的说话人不计
    (后期合成,不挂 audio_ref)。"""
    errors = []
    by_id = {s["shot_id"]: s for s in shot_list.get("shots") or [] if isinstance(s, dict) and s.get("shot_id")}
    for g in shot_list.get("generation_groups") or []:
        gid = g.get("group_id", "?")
        on, off = set(), set()
        for sid in g.get("shots") or []:
            s = by_id.get(sid)
            if not s:
                continue
            for ln in shot_onscreen_lines(s):
                on.add(_speaker_of(ln, names) or "?")
            for ln in shot_offscreen_lines(s):
                off.add(_speaker_of(ln, names) or "?")
        if len(on) > MAX_SPEAKERS:
            errors.append(f"{gid} speakers_le_3: 画内说话人 {len(on)}>{MAX_SPEAKERS} {sorted(on)}"
                          f"(os/vo 不计{';画外 ' + str(sorted(off)) if off else ''});按说话回合拆组,或把插话转画外(D3)")
    return errors


def check_placement(shot_list_path: Path, shot_list: dict, mode: str) -> list[str]:
    """placement_valid 系列(2026-10-03):委托 modules/offscreen_lines.validate(heard_in 同组 / speaker 是本集人物 /
    placement_reason / offscreen_fit / post_voice_no_overlap)。模式 off 时 shot_list 不得有 os/vo 句。"""
    errors = []
    by_id = {s["shot_id"]: s for s in shot_list.get("shots") or [] if isinstance(s, dict) and s.get("shot_id")}
    if mode == "off":
        for g in shot_list.get("generation_groups") or []:
            for sid in g.get("shots") or []:
                s = by_id.get(sid)
                if s and shot_offscreen_lines(s):
                    errors.append(f"{g.get('group_id', '?')}/{sid} placement_valid: 项目「声画分离」已关闭,但有 placement=os/vo 的台词"
                                  "(改回画内或在输出设置开启)")
        return errors
    if _osl is None or not hasattr(_osl, "validate"):
        print("[placement] skipped: modules/offscreen_lines 不可用")
        return errors
    try:
        root, ep = project_root_of(shot_list_path), shot_list_path.parent.name
        errors += [str(m) for m in (_osl.validate(root, ep, shot_list) or [])]
    except Exception as exc:  # 校验器自身异常不吞:记为机检项
        errors.append(f"placement_valid: offscreen_lines.validate 执行失败:{exc}")
    return errors


def derive_narration_path(shot_list_path: Path) -> Path | None:
    """directing/epNN/shot_list.json → story/episodes/epNN/narration.md"""
    ep = shot_list_path.parent.name
    p = shot_list_path.parent.parent.parent / "story" / "episodes" / ep / "narration.md"
    return p if p.is_file() else None


def project_id_step(shot_list_path: Path) -> int:
    """项目编号制(settings.json#numbering.step):1=逐一制 grp001,10=预留插入位制 grp0010;
    读不到按逐一制(存量项目)。"""
    try:
        st = json.loads((shot_list_path.resolve().parents[2] / "settings.json").read_text())
        return 10 if int((st.get("numbering") or {}).get("step", 1)) == 10 else 1
    except Exception:
        return 1


def propose_groups(shots: list[dict], id_step: int = 1) -> list[dict]:
    """贪心草案:同场景、连续、Σ≤15 整数、组内角色 ≤4。"""
    groups, cur = [], []

    def flush():
        if not cur:
            return
        groups.append({
            "group_id": (f"grp{(len(groups) + 1) * 10:04d}" if id_step == 10
                         else f"grp{len(groups) + 1:03d}"),
            "scene_id": cur[0].get("scene_id"),
            "scene_no": cur[0].get("scene_no"),
            "shots": [s["shot_id"] for s in cur],
            "total_duration_s": int(round(sum(s["duration_s"] for s in cur))),
            "characters_union": sorted({c for s in cur for c in (s.get("characters") or [])}),
            "has_dialogue": any(shot_has_onscreen_dialogue(s) for s in cur),   # 只看画内句(2026-10-03)
            "continuity_from": groups[-1]["group_id"] if groups else None,
        })
        cur.clear()

    for s in shots:
        chars = set(s.get("characters") or [])
        if cur:
            cur_dur = sum(x["duration_s"] for x in cur)
            cur_chars = {c for x in cur for c in (x.get("characters") or [])}
            if (s.get("scene_id") != cur[0].get("scene_id")
                    or s.get("scene_no") != cur[0].get("scene_no")
                    or cur_dur + s["duration_s"] > MAX_GROUP_S
                    or len(cur_chars | chars) > MAX_CHARS):
                flush()
        cur.append(s)
    flush()
    return groups


def check(shot_list: dict) -> list[str]:
    errors = []
    shots = shot_list.get("shots") or []
    groups = shot_list.get("generation_groups") or []
    if not groups:
        return ["shot_list 无 generation_groups 字段(未分组)"]
    by_id = {s["shot_id"]: s for s in shots}
    order = {s["shot_id"]: i for i, s in enumerate(shots)}

    # 1. 覆盖不重不漏
    seen = []
    for g in groups:
        seen.extend(g.get("shots") or [])
    if sorted(seen) != sorted(by_id):
        missing = set(by_id) - set(seen)
        dup_or_extra = [x for x in seen if seen.count(x) > 1] + list(set(seen) - set(by_id))
        errors.append(f"groups_cover_all_shots: 缺 {sorted(missing)} / 重或多 {sorted(set(dup_or_extra))}")

    prev_gid = None
    for g in groups:
        gid = g.get("group_id", "?")
        gshots = g.get("shots") or []
        # 2. 连续且同场景
        idxs = [order.get(x) for x in gshots]
        if None in idxs or idxs != list(range(idxs[0], idxs[0] + len(idxs))):
            errors.append(f"{gid} group_shots_contiguous: 镜号不连续 {gshots}")
        scenes = {by_id[x].get("scene_id") for x in gshots if x in by_id}
        if len(scenes) > 1:
            errors.append(f"{gid} same_scene: 跨场景 {sorted(scenes)}")
        scene_numbers = {by_id[x].get("scene_no") for x in gshots if x in by_id and by_id[x].get("scene_no")}
        if len(scene_numbers) > 1:
            errors.append(f"{gid} same_scene_instance: 跨场次 {sorted(scene_numbers)}")
        # 3. 时长
        td = g.get("total_duration_s")
        real = sum(by_id[x]["duration_s"] for x in gshots if x in by_id)
        if not isinstance(td, int):
            errors.append(f"{gid} duration_int: total_duration_s={td!r} 非整数")
        elif abs(td - real) > 0.05:
            # 与白模编译(modules/whitebox.compile_group)同口径:声明时长须与 Σ镜时长相差 ≤0.05s,
            # 整数声明 + 小数镜长合计(如 19 vs 18.8)两边都拒,不再出现「这里过、白模编译拒」(#87)
            errors.append(f"{gid} duration_sum: total_duration_s={td} ≠ Σ镜时长 {real:g}(差须 ≤0.05s)")
        if not (isinstance(td, (int, float)) and MIN_GROUP_S <= td <= MAX_GROUP_S):
            errors.append(f"{gid} duration_range: {td} ∉ [{MIN_GROUP_S},{MAX_GROUP_S}]")
        # 5b. 密集组限长(2026-10-03):亚秒快切组越长,模型按文字顺序与按白模时间的漂移越积越大
        dense = _tc.group_density_issue([by_id[x]["duration_s"] for x in gshots if x in by_id], td if isinstance(td, (int, float)) else None)
        if dense:
            errors.append(f"{gid} group_density_ok: {dense}")
        # 4. 角色数
        chars = g.get("characters_union")
        real_chars = sorted({c for x in gshots if x in by_id for c in (by_id[x].get("characters") or [])})
        if chars is None:
            errors.append(f"{gid} characters_union 缺失")
        elif sorted(chars) != real_chars:
            errors.append(f"{gid} characters_union 与镜表不符: {chars} ≠ {real_chars}")
        if len(real_chars) > MAX_CHARS:
            errors.append(f"{gid} characters_lte_4: 组内角色 {len(real_chars)} > {MAX_CHARS}")
        # 5. 组序链
        if g.get("continuity_from") != prev_gid:
            errors.append(f"{gid} continuity_from: {g.get('continuity_from')!r} ≠ 前组 {prev_gid!r}")
        prev_gid = gid
    return errors


def check_7d(shot_list: dict, narration_md: str | None,
             narration_on: bool = True) -> list[str]:
    """§7D ① 机检:旁白挂点(narration_anchors)+ 逐组音频形态(audio_plan)。
    旁白开关关闭时(narration_on=False):挂点/窗口机检跳过,shot_list 不得残留
    narration_anchors,audio_plan 禁 narration_over(无对白组一律 ambient_only)。"""
    errors = []
    shots = shot_list.get("shots") or []
    groups = shot_list.get("generation_groups") or []
    by_id = {s["shot_id"]: s for s in shots}
    by_gid = {g.get("group_id"): g for g in groups}

    def facts(g: dict) -> tuple[bool, bool]:
        """(有画内句, 有画外/V.O. 句)。镜表没有 dialogue_lines 字典行的旧项目退回组 has_dialogue。"""
        has_on = has_off = False
        any_dict = False
        for sid in g.get("shots") or []:
            s = by_id.get(sid)
            if not s:
                continue
            if any(isinstance(ln, dict) for ln in s.get("dialogue_lines") or []):
                any_dict = True
            has_on = has_on or bool(shot_onscreen_lines(s))
            has_off = has_off or bool(shot_offscreen_lines(s))
        if not any_dict:
            return bool(g.get("has_dialogue")), False
        if g.get("has_dialogue") is not None and bool(g.get("has_dialogue")) != has_on:
            errors.append(f"{g.get('group_id', '?')} has_dialogue_consistent: has_dialogue={g.get('has_dialogue')},"
                          f"但组内{'有' if has_on else '无'}画内台词句(画外 / V.O. 句不算对白镜,2026-10-03)")
        return has_on, has_off

    if not narration_on:
        print("[7d] skipped: narration off(项目输出设置「旁白」已关闭,全片无旁白)"
              "—— 仅查 audio_plan(禁 narration_over;voice_over 仅限有画外 / V.O. 句的组)")
        if shot_list.get("narration_anchors"):
            errors.append("narration_off: 旁白开关已关闭,但 shot_list 仍有 narration_anchors 条目"
                          "(全片无旁白约定,须清空或按新约定重定稿)")
        for g in groups:
            gid = g.get("group_id", "?")
            plan = g.get("audio_plan")
            has_dlg, has_off = facts(g)
            if plan not in AUDIO_PLANS:
                errors.append(f"{gid} audio_plan_complete: audio_plan={plan!r} 非法或缺失")
                continue
            if plan == "narration_over":
                errors.append(f"{gid} audio_plan_consistent: 旁白开关已关闭,禁用 narration_over"
                              "(无对白组一律 ambient_only 并附 silent_rationale;只有画外 / V.O. 句的组写 voice_over)")
                continue
            expect = "dialogue" if has_dlg else ("voice_over" if has_off else "ambient_only")
            if plan != expect:
                errors.append(f"{gid} audio_plan_consistent: audio_plan={plan},但按"
                              f" 画内句={'有' if has_dlg else '无'}/画外句={'有' if has_off else '无'}(旁白已关闭)应为 {expect}")
            if plan == "ambient_only" and not (g.get("silent_rationale") or "").strip():
                errors.append(f"{gid} silent_rationale: ambient_only 组未说明纯画面"
                              "能讲清叙事的理由(§7D ① 无声组核查)")
        return errors

    if narration_md is None:
        errors.append("narration_anchors_cover_all: narration.md 未找到"
                      "(--narration 指定或 --skip-7d 显式跳过)")
        narr = {}
    else:
        narr = {m.group(1): float(m.group(2))
                for m in NARR_ITEM_RE.finditer(narration_md)}
        if not narr:
            errors.append("narration_anchors_cover_all: narration.md 解析不到任何条目"
                          "(条目头格式应为 [N-xx | anchor: … | est_duration_s: …])")

    anchors = shot_list.get("narration_anchors")
    if anchors is None:
        errors.append("narration_anchors_cover_all: shot_list 无 narration_anchors 字段"
                      "(§7D ① 挂点未定稿)")
        anchors = []
    anchored_groups = set()
    seen_ids = set()
    for a in anchors:
        nid = a.get("narration_id", "?")
        seen_ids.add(nid)
        if narr and nid not in narr:
            errors.append(f"{nid} anchor_ref: narration.md 无此条目")
        a_shots = a.get("anchor_shots") or []
        gid = a.get("anchor_group")
        g = by_gid.get(gid)
        if not a_shots:
            errors.append(f"{nid} anchor_ref: anchor_shots 为空")
        for sid in a_shots:
            if sid not in by_id:
                errors.append(f"{nid} anchor_ref: 镜 {sid} 不存在")
        if g is None:
            errors.append(f"{nid} anchor_ref: 组 {gid!r} 不存在")
        else:
            anchored_groups.add(gid)
            outside = [s for s in a_shots if s not in (g.get("shots") or [])]
            if outside:
                errors.append(f"{nid} anchor_ref: 镜 {outside} 不属于组 {gid}")
        est = a.get("est_duration_s")
        if narr.get(nid) is not None and est is not None and abs(est - narr[nid]) > 0.11:
            errors.append(f"{nid} est_mismatch: 挂点 est={est} ≠ narration.md {narr[nid]}")
        win = a.get("window_s")
        est_ref = narr.get(nid, est)
        if not isinstance(win, (int, float)):
            errors.append(f"{nid} window: window_s 缺失")
        else:
            span = sum(by_id[s].get("duration_s") or 0 for s in a_shots if s in by_id)
            if span and win > span + 0.01:
                errors.append(f"{nid} window: window_s={win} 超挂点镜区间物理时长 {span:g}"
                              "(窗口应=区间时长−区间内对白占时)")
            if est_ref is not None and win < est_ref * WINDOW_FACTOR - 1e-9:
                errors.append(f"{nid} narration_window_gte_est_x1.15: window_s={win} <"
                              f" est {est_ref}×{WINDOW_FACTOR}={est_ref * WINDOW_FACTOR:.2f}"
                              "(优先调镜时长消化,装不下上报回派 narration 精简)")
    missing = sorted(set(narr) - seen_ids, key=lambda x: int(x.split("-")[1]))
    if missing:
        errors.append(f"narration_anchors_cover_all: 条目缺挂点 {missing}")

    for g in groups:
        gid = g.get("group_id", "?")
        plan = g.get("audio_plan")
        has_dlg, has_off = facts(g)
        has_narr = gid in anchored_groups
        if plan not in AUDIO_PLANS:
            errors.append(f"{gid} audio_plan_complete: audio_plan={plan!r} 非法或缺失")
            continue
        # voice_over(2026-10-03)与旧名 narration_over 等价:无画内句但有旁白挂点或画外 / V.O. 句
        expect = ("dialogue" if has_dlg
                  else "voice_over" if (has_narr or has_off) else "ambient_only")
        plan_norm = "voice_over" if plan == "narration_over" else plan
        if plan_norm != expect:
            errors.append(f"{gid} audio_plan_consistent: audio_plan={plan},但按"
                          f" 画内句={'有' if has_dlg else '无'}/挂点={'有' if has_narr else '无'}/画外句={'有' if has_off else '无'}"
                          f" 应为 {expect}{'(旧名 narration_over 亦可)' if expect == 'voice_over' else ''}")
        if plan == "ambient_only" and not (g.get("silent_rationale") or "").strip():
            errors.append(f"{gid} silent_rationale: ambient_only 组未说明纯画面"
                          "能讲清叙事的理由(§7D ① 无声组核查)")
    return errors


def transition_of(group: dict) -> dict:
    """组入口转场,规范化:缺省 / None / 空对象 = hard_cut;垫片字段 hold_s / freeze_s 数值化(缺省 0)。"""
    t = group.get("transition_in")
    if not isinstance(t, dict) or not t:
        return {"type": "hard_cut"}
    t = dict(t)
    t["type"] = str(t.get("type") or "hard_cut")
    for k in ("hold_s", "freeze_s"):
        try:
            v = float(t.get(k) or 0.0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            t[k] = v
        else:
            t.pop(k, None)
    if t.get("hold_s"):
        t["hold_audio"] = str(t.get("hold_audio") or "sustain")
    # 过场设计(2026-09-24):inserts[] 规范化为对象列表、duration_s 数值化;join / overlay_card 非对象即丢弃;audio_lead_s 数值化
    ins = t.get("inserts")
    if isinstance(ins, list) and ins:
        norm = []
        for x in ins:
            if not isinstance(x, dict) or not x.get("kind"):
                continue
            x = dict(x)
            try:
                x["duration_s"] = float(x.get("duration_s") or 0.0)
            except (TypeError, ValueError):
                x["duration_s"] = 0.0
            x["join_out"] = str(x.get("join_out") or "hard_cut")
            if x["join_out"] != "hard_cut":
                try:
                    x["join_out_s"] = float(x.get("join_out_s") or INSERT_JOIN_DEFAULT_S)
                except (TypeError, ValueError):
                    x["join_out_s"] = INSERT_JOIN_DEFAULT_S
            x["audio"] = str(x.get("audio") or "mute")
            norm.append(x)
        t["inserts"] = norm
    else:
        t.pop("inserts", None)
    if not isinstance(t.get("join"), dict) or not t["join"].get("style"):
        t.pop("join", None)
    oc = t.get("overlay_card")
    if isinstance(oc, dict) and [l for l in (oc.get("lines") or []) if str(l).strip()]:
        oc = dict(oc)
        oc["lines"] = [str(l) for l in oc.get("lines") if str(l).strip()]
        try:
            oc["duration_s"] = float(oc.get("duration_s") or 2.5)
        except (TypeError, ValueError):
            oc["duration_s"] = 2.5
        oc["position"] = str(oc.get("position") or "bottom_left")
        t["overlay_card"] = oc
    else:
        t.pop("overlay_card", None)
    try:
        al = float(t.get("audio_lead_s") or 0.0)
    except (TypeError, ValueError):
        al = 0.0
    if al > 0:
        t["audio_lead_s"] = al
    else:
        t.pop("audio_lead_s", None)
    # 三期(2026-09-26):成对运镜 motion_pair 规范化——非对象 / 缺 out 即丢弃;speed 缺省 medium
    mp = t.get("motion_pair")
    if isinstance(mp, dict) and mp.get("out"):
        mp = dict(mp)
        mp["out"] = str(mp.get("out"))
        mp["in"] = str(mp.get("in") or MOTION_PAIRS.get(mp["out"]) or "")
        mp["speed"] = str(mp.get("speed") or "medium")
        t["motion_pair"] = mp
    else:
        t.pop("motion_pair", None)
    return t


def motion_pair_of(t: dict) -> dict | None:
    """已规范化 transition_in 的成对运镜 {out, in, speed} 或 None。"""
    mp = t.get("motion_pair") if isinstance(t, dict) else None
    return mp if isinstance(mp, dict) and mp.get("out") else None


def inserts_of(t: dict) -> list[dict]:
    """组边界插入段(已规范化的 transition_in → inserts[]),无则空列表。"""
    return list(t.get("inserts") or []) if isinstance(t, dict) else []


def insert_total_s(t: dict) -> float:
    return float(sum(float(x.get("duration_s") or 0.0) for x in inserts_of(t)))


def project_insert_budget_pct(shot_list_path: Path) -> float:
    """项目「过场模式」的插入段时长预算(% 集预算):settings.json#transitions(经 modules.transition_design.effective 按模式展开,
    集级覆盖 assets/group_settings/<ep>/episode.json#transitions_mode 优先);读不到按经典档 8%。"""
    try:
        base = shot_list_path.resolve().parents[2]
        ep = shot_list_path.resolve().parent.name
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from modules.transition_design import effective
        return float(effective(base, ep)["insert_budget_pct"])
    except Exception:
        return INSERT_BUDGET_DEFAULT_PCT


def pad_of(t: dict) -> tuple[float, float, str]:
    """(freeze_s, hold_s, hold_audio) —— 组边界要插入的定格 / 黑场时长(秒)与黑场声音策略。"""
    return float(t.get("freeze_s") or 0.0), float(t.get("hold_s") or 0.0), str(t.get("hold_audio") or "sustain")


def normalize_episode_close(raw, *, source: str = "shot_list.episode_close") -> dict | None:
    """集尾收束规范化:None / 非对象 → None(未指定);{type: hard_cut} → {"type": "hard_cut"}(显式不处理);
    fade_black / fade_white → {type, duration_s, hold_s, hold_audio, reason?, source}。数值不合法的原样保留给 check_episode_close 报错。"""
    if not isinstance(raw, dict) or not raw:
        return None
    t = {"type": str(raw.get("type") or "hard_cut")}
    if t["type"] == "hard_cut":
        if str(raw.get("reason") or "").strip():
            t["reason"] = str(raw["reason"]).strip()
        t["source"] = str(raw.get("source") or source)
        return t
    is_cut = t["type"] in EPISODE_CLOSE_CUTS
    for k, dflt in (("duration_s", 0.0 if is_cut else EPISODE_CLOSE_DEFAULT["duration_s"]),
                    ("hold_s", EPISODE_CLOSE_CUT_DEFAULT["hold_s"] if is_cut else 0.0)):
        v = raw.get(k, dflt)
        if k == "duration_s" and is_cut:
            v = 0.0                       # 切黑不淡出
        try:
            t[k] = float(v)
        except (TypeError, ValueError):
            t[k] = v
    t["hold_audio"] = str(raw.get("hold_audio") or (EPISODE_CLOSE_CUT_DEFAULT if is_cut else EPISODE_CLOSE_DEFAULT)["hold_audio"])
    if str(raw.get("reason") or "").strip():
        t["reason"] = str(raw["reason"]).strip()
    t["source"] = str(raw.get("source") or source)
    return t


def check_episode_close(t: dict | None, label: str = "episode_close") -> list[str]:
    """集尾收束契约(transition_close_valid):type 枚举、duration_s / hold_s 范围、hold_audio 枚举。"""
    if not t:
        return []
    errs = []
    ty = t.get("type")
    if ty not in EPISODE_CLOSE_TYPES:
        return [f"{label} transition_close_valid: type={ty!r} 不在枚举 {list(EPISODE_CLOSE_TYPES)}"]
    if ty == "hard_cut":
        return errs
    lo, hi = EPISODE_CLOSE_DURATION
    d = t.get("duration_s")
    if ty in EPISODE_CLOSE_FADES:
        if isinstance(d, bool) or not isinstance(d, (int, float)):
            errs.append(f"{label} transition_close_valid: duration_s={d!r} 须为秒数(范围 {lo}–{hi}s)")
        elif not (lo - 1e-9 <= float(d) <= hi + 1e-9):
            errs.append(f"{label} transition_close_valid: duration_s={d} ∉ [{lo},{hi}]")
    h = t.get("hold_s", 0.0)
    if isinstance(h, bool) or not isinstance(h, (int, float)):
        errs.append(f"{label} transition_close_valid: hold_s={h!r} 须为秒数(范围 0–{EPISODE_CLOSE_HOLD_MAX_S:g}s)")
    elif not (0 <= float(h) <= EPISODE_CLOSE_HOLD_MAX_S + 1e-9):
        errs.append(f"{label} transition_close_valid: hold_s={h} ∉ [0,{EPISODE_CLOSE_HOLD_MAX_S:g}]")
    elif ty in EPISODE_CLOSE_CUTS and float(h) <= 0:
        errs.append(f"{label} transition_close_valid: {ty}(切黑)须 hold_s > 0(否则等于停在末帧)")
    if t.get("hold_audio") not in EPISODE_CLOSE_AUDIO:
        errs.append(f"{label} transition_close_valid: hold_audio={t.get('hold_audio')!r} 不在枚举 {list(EPISODE_CLOSE_AUDIO)}")
    return errs


def episode_close_of(shot_list: dict) -> dict | None:
    """shot_list 顶层 episode_close(规范化);未写 = None(由项目设置决定,见 modules.transition_design.effective_episode_close)。"""
    return normalize_episode_close((shot_list or {}).get("episode_close"))


def _check_join(gid: str, t: dict) -> list[str]:
    j = t.get("join")
    if not j:
        return []
    errs = []
    style = str(j.get("style") or "")
    if style not in JOIN_STYLES:
        errs.append(f"{gid} transition_join_valid: join.style={style!r} 不在枚举 {list(JOIN_STYLES)}")
        return errs
    if t.get("type") != "dissolve":
        errs.append(f"{gid} transition_join_valid: join.style 只配 dissolve(接缝风格是叠化的 xfade 模式替换),当前 type={t.get('type')}")
    for k, allowed in JOIN_STYLES[style].items():
        v = j.get(k)
        if v is not None and v not in allowed:
            errs.append(f"{gid} transition_join_valid: join.{k}={v!r} 不在 {style} 的枚举 {list(allowed)}")
    return errs


def _check_motion_pair(gid: str, t: dict, is_first: bool) -> list[str]:
    """motion_pair_valid(三期 2026-09-26):out/in 在配对表内且互为合法配对、speed 枚举、type ∈ hard_cut/dissolve、与 inserts 互斥、首组不得、须写 reason。"""
    mp = motion_pair_of(t)
    if not mp:
        return []
    errs = []
    out, inn = str(mp.get("out") or ""), str(mp.get("in") or "")
    if out not in MOTION_PAIRS:
        errs.append(f"{gid} motion_pair_valid: motion_pair.out={out!r} 不在枚举 {list(MOTION_PAIRS)}")
    elif inn != MOTION_PAIRS[out]:
        errs.append(f"{gid} motion_pair_valid: motion_pair.in={inn!r} 与 out={out} 不配对(应为 {MOTION_PAIRS[out]})")
    if str(mp.get("speed") or "medium") not in MOTION_SPEEDS:
        errs.append(f"{gid} motion_pair_valid: motion_pair.speed={mp.get('speed')!r} 不在枚举 {list(MOTION_SPEEDS)}")
    if t.get("type") not in ("hard_cut", "dissolve"):
        errs.append(f"{gid} motion_pair_valid: motion_pair 只配 hard_cut / dissolve(运镜对接本身就是过场),得到 {t.get('type')}")
    if inserts_of(t):
        errs.append(f"{gid} motion_pair_valid: motion_pair 与 inserts 互斥(插入段会打断运镜衔接)")
    if is_first:
        errs.append(f"{gid} motion_pair_valid: 首组无前组,不得 motion_pair")
    if not str(t.get("reason") or "").strip():
        errs.append(f"{gid} motion_pair_valid: 带 motion_pair 须写 reason")
    return errs


def _check_inserts(gid: str, t: dict, is_first: bool) -> tuple[list[str], float]:
    ins = inserts_of(t)
    if not ins:
        return [], 0.0
    errs, total = [], 0.0
    if is_first:
        errs.append(f"{gid} transition_insert_valid: 首组不得带 inserts(集首字卡归片头包装,本期不支持)")
    if not str(t.get("reason") or "").strip():
        errs.append(f"{gid} transition_insert_valid: 带 inserts 须写 reason(过场意图)")
    for k, x in enumerate(ins):
        kind, d = x.get("kind"), float(x.get("duration_s") or 0)
        tag = f"{gid} inserts[{k}]"
        if kind not in INSERT_KINDS:
            errs.append(f"{tag} transition_insert_valid: kind={kind!r} 不在枚举 {list(INSERT_KINDS)}")
            continue
        if not (INSERT_MIN_S - 1e-9 <= d <= INSERT_MAX_S + 1e-9):
            errs.append(f"{tag} transition_insert_valid: {kind} duration_s={d:g} ∉ [{INSERT_MIN_S:g},{INSERT_MAX_S:g}]")
        total += d
        if x.get("join_out") not in INSERT_JOINS:
            errs.append(f"{tag} transition_insert_valid: join_out={x.get('join_out')!r} 不在枚举 {list(INSERT_JOINS)}")
        if x.get("audio") not in INSERT_AUDIO:
            errs.append(f"{tag} transition_insert_valid: audio={x.get('audio')!r} 不在枚举 {list(INSERT_AUDIO)}")
        if kind == "title_card":
            card = x.get("card") if isinstance(x.get("card"), dict) else {}
            lines = [str(l) for l in (card.get("lines") or []) if str(l).strip()]
            if not lines:
                errs.append(f"{tag} transition_insert_valid: title_card 缺 card.lines(至少一行文字)")
            if len(lines) > 3:
                errs.append(f"{tag} transition_insert_valid: title_card 最多 3 行,得到 {len(lines)}")
            if card.get("bg", "black") not in CARD_BG:
                errs.append(f"{tag} transition_insert_valid: card.bg={card.get('bg')!r} 不在枚举 {list(CARD_BG)}")
        elif kind in ("establishing", "timelapse"):
            src = x.get("source") if isinstance(x.get("source"), dict) else {}
            if not src.get("scene_id"):
                errs.append(f"{tag} transition_insert_valid: {kind} 缺 source.scene_id")
            if kind == "establishing" and src.get("mode", "pano_sweep") not in ESTABLISHING_MODES:
                errs.append(f"{tag} transition_insert_valid: source.mode={src.get('mode')!r} 不在枚举 {list(ESTABLISHING_MODES)}")
            if kind == "establishing" and src.get("mode") == "i2v":
                # 生成式定场空镜(2026-09-26):clip 文件约定在 assets/transitions/epNN/<B-id>.establishing.mp4,由 Phase 7 p7-transition-clips 出
                f = str(src.get("file") or "")
                if not f.startswith("assets/transitions/") or not f.endswith(".mp4"):
                    errs.append(f"{tag} transition_insert_valid: i2v 定场须给 source.file(assets/transitions/epNN/<B-id>.establishing.mp4),得到 {f!r}")
            if kind == "timelapse" and not (src.get("scheme_from") and src.get("scheme_to")):
                errs.append(f"{tag} transition_insert_valid: timelapse 须给 source.scheme_from / scheme_to(同锚点两个光照方案)")
        elif kind == "bridge":
            if t.get("motion_pair"):
                errs.append(f"{tag} transition_insert_valid: bridge 与 motion_pair 互斥")
            # 三期(2026-09-26):桥接 clip 由 Phase 7 p7-transition-clips 出到 assets/transitions/epNN/<B-id>.bridge.mp4
            f = str(x.get("file") or "")
            if not f.startswith("assets/transitions/") or not f.endswith(".mp4"):
                errs.append(f"{tag} transition_insert_valid: bridge 须给 file(assets/transitions/epNN/<B-id>.bridge.mp4),得到 {f!r}")
    if total > BOUNDARY_INSERT_MAX_S + 1e-9:
        errs.append(f"{gid} transition_insert_valid: 单边界 Σinserts {total:g}s > {BOUNDARY_INSERT_MAX_S:g}s")
    return errs, total


def check_transitions(shot_list: dict, insert_budget_pct: float | None = None) -> list[str]:
    """transition_ok(2026-08-28):组入口转场 transition_in + 叙事块 narrative_block 机检。
    insert_budget_pct:插入段预算(% 集预算,过场设计 2026-09-24);None = 按经典档 INSERT_BUDGET_DEFAULT_PCT。"""
    errors = []
    groups = shot_list.get("generation_groups") or []
    budget = shot_list.get("budget_s") or shot_list.get("total_duration_s") \
        or sum(g.get("total_duration_s") or 0 for g in groups)
    render_total = 0.0
    pad_total = 0.0
    insert_total = 0.0
    if insert_budget_pct is None:
        insert_budget_pct = INSERT_BUDGET_DEFAULT_PCT
    has_tr = {}
    for i, g in enumerate(groups):
        gid = g.get("group_id", "?")
        raw = g.get("transition_in")
        if raw is not None and not isinstance(raw, dict):
            errors.append(f"{gid} transition_type_valid: transition_in 须为对象,得到 {type(raw).__name__}")
            continue
        t = transition_of(g)
        ty = t["type"]
        has_tr[gid] = bool(raw)
        if ty not in TRANSITION_TYPES:
            errors.append(f"{gid} transition_type_valid: type={ty!r} 不在枚举 {list(TRANSITION_TYPES)}")
            continue
        dur = t.get("duration_s")
        if ty in TRANSITION_RENDERABLE:
            lo, hi = TRANSITION_RENDERABLE[ty]
            if not isinstance(dur, (int, float)) or isinstance(dur, bool):
                errors.append(f"{gid} transition_type_valid: {ty} 缺 duration_s(范围 {lo}–{hi}s)")
            elif not (lo - 1e-9 <= dur <= hi + 1e-9):
                errors.append(f"{gid} transition_type_valid: {ty} duration_s={dur} ∉ [{lo},{hi}]")
            else:
                render_total += float(dur)
            if i == 0 and ty in ("dissolve", "dip_black", "dip_white"):
                errors.append(f"{gid} transition_type_valid: 首组无前组可叠,{ty} 非法"
                              "(首组只能 hard_cut / fade_black / fade_white 淡入)")
        elif dur not in (None, 0, 0.0):
            errors.append(f"{gid} transition_type_valid: {ty} 不渲染,duration_s 应省略或为 0(得到 {dur})")
        if ty != "hard_cut":
            if not str(t.get("reason") or "").strip():
                errors.append(f"{gid} transition_reason_required: {ty} 缺 reason(须可溯 directing_plan 转场清单)")
            if t.get("intent") not in TRANSITION_INTENTS:
                errors.append(f"{gid} transition_reason_required: {ty} intent={t.get('intent')!r}"
                              f" 不在枚举 {list(TRANSITION_INTENTS)}")
        # 节奏垫片(2026-09-17)
        raw_t = raw if isinstance(raw, dict) else {}
        for k in ("hold_s", "freeze_s"):
            rv = raw_t.get(k)
            if rv is None:
                continue
            if isinstance(rv, bool) or not isinstance(rv, (int, float)):
                errors.append(f"{gid} transition_pad_valid: {k}={rv!r} 须为秒数")
            elif rv < 0 or rv > PAD_MAX_S + 1e-9:
                errors.append(f"{gid} transition_pad_valid: {k}={rv} ∉ [0,{PAD_MAX_S:g}]")
        freeze_s, hold_s, hold_audio = pad_of(t)
        if hold_s or freeze_s:
            pad_total += hold_s + freeze_s
            if i == 0:
                errors.append(f"{gid} transition_pad_valid: 首组不得 hold_s/freeze_s(集首用 fade_black 淡入)")
            if not str(t.get("reason") or "").strip():
                errors.append(f"{gid} transition_pad_valid: 带 hold_s/freeze_s 须写 reason(节奏意图)")
        if hold_s:
            if ty not in HOLD_TYPES:
                errors.append(f"{gid} transition_pad_valid: hold_s 只配 {list(HOLD_TYPES)}(黑场停留须「到黑」),得到 {ty}")
            if hold_audio not in HOLD_AUDIO:
                errors.append(f"{gid} transition_pad_valid: hold_audio={hold_audio!r} 不在枚举 {list(HOLD_AUDIO)}")
        # 过场设计(2026-09-24):接缝风格 / 插入段 / 叠字幕 / 音先入
        errors += _check_join(gid, t)
        ins_errs, ins_s = _check_inserts(gid, t, i == 0)
        errors += ins_errs
        insert_total += ins_s
        oc = t.get("overlay_card")
        if oc:
            if not (0 < float(oc.get("duration_s") or 0) <= OVERLAY_MAX_S + 1e-9):
                errors.append(f"{gid} transition_insert_valid: overlay_card.duration_s={oc.get('duration_s')} ∉ (0,{OVERLAY_MAX_S:g}]")
            if oc.get("position") not in OVERLAY_POSITIONS:
                errors.append(f"{gid} transition_insert_valid: overlay_card.position={oc.get('position')!r} 不在枚举 {list(OVERLAY_POSITIONS)}")
        if t.get("audio_lead_s"):
            # 四期(2026-09-26):音先入只配无插入段 / 无黑场停留的 hard_cut / dissolve 边界;首组无前组不得先入
            al = float(t["audio_lead_s"])
            if al > AUDIO_LEAD_MAX_S + 1e-9:
                errors.append(f"{gid} transition_audio_lead_valid: audio_lead_s={t['audio_lead_s']} > {AUDIO_LEAD_MAX_S:g}")
            if ty not in AUDIO_LEAD_TYPES:
                errors.append(f"{gid} transition_audio_lead_valid: audio_lead_s 只配 {list(AUDIO_LEAD_TYPES)}(音先入压在前组尾画面上),得到 {ty}")
            if inserts_of(t) or hold_s:
                errors.append(f"{gid} transition_audio_lead_valid: audio_lead_s 不得与 inserts / hold_s 并用(先入声无处可压)")
            if i == 0:
                errors.append(f"{gid} transition_audio_lead_valid: 首组无前组,不得 audio_lead_s")
            if not str(t.get("reason") or "").strip():
                errors.append(f"{gid} transition_audio_lead_valid: 带 audio_lead_s 须写 reason")
        errors += _check_motion_pair(gid, t, i == 0)
    if budget and insert_total > float(budget) * insert_budget_pct / 100.0 + 1e-9:
        errors.append(f"transition_insert_budget: Σ插入段 {insert_total:g}s > 集预算 {budget}s × {insert_budget_pct:g}%"
                      f" = {float(budget) * insert_budget_pct / 100.0:.2f}s(项目「过场模式」预算;极简档为 0)")
    if budget and render_total > float(budget) * TRANSITION_BUDGET_RATIO + 1e-9:
        errors.append(f"transition_budget_le_1pct: Σ可渲染转场 {render_total:g}s >"
                      f" 集预算 {budget}s × {TRANSITION_BUDGET_RATIO:g} = {float(budget) * TRANSITION_BUDGET_RATIO:.2f}s")
    if budget and pad_total > float(budget) * PAD_BUDGET_RATIO + 1e-9:
        errors.append(f"transition_pad_valid: Σ黑场停留+定格 {pad_total:g}s > 集预算 {budget}s × {PAD_BUDGET_RATIO:g}"
                      f" = {float(budget) * PAD_BUDGET_RATIO:.2f}s")

    # 集尾收束(2026-09-25):顶层 episode_close 可选,写了就要合法
    raw_close = shot_list.get("episode_close")
    if raw_close is not None and not isinstance(raw_close, dict):
        errors.append(f"episode_close transition_close_valid: episode_close 须为对象,得到 {type(raw_close).__name__}")
    else:
        errors += check_episode_close(episode_close_of(shot_list))

    # narrative_block:同 id 连续、role 序列合法、块首与块尾下一组都有 transition_in
    blocks: dict[str, list[tuple[int, str, str]]] = {}
    for i, g in enumerate(groups):
        nb = g.get("narrative_block")
        if nb is None:
            continue
        gid = g.get("group_id", "?")
        if not isinstance(nb, dict) or not nb.get("id"):
            errors.append(f"{gid} narrative_block_paired: narrative_block 须为含 id 的对象")
            continue
        if nb.get("kind") not in BLOCK_KINDS:
            errors.append(f"{gid} narrative_block_paired: kind={nb.get('kind')!r} 不在枚举 {list(BLOCK_KINDS)}")
        if nb.get("role") not in BLOCK_ROLES:
            errors.append(f"{gid} narrative_block_paired: role={nb.get('role')!r} 不在枚举 {list(BLOCK_ROLES)}")
        blocks.setdefault(str(nb["id"]), []).append((i, gid, str(nb.get("role"))))
    for bid, rows in blocks.items():
        idxs = [r[0] for r in rows]
        if idxs != list(range(idxs[0], idxs[0] + len(idxs))):
            errors.append(f"narrative_block_paired: 块 {bid} 的组不连续 {[r[1] for r in rows]}")
        roles = [r[2] for r in rows]
        expect = ["single"] if len(rows) == 1 else ["start"] + ["middle"] * (len(rows) - 2) + ["end"]
        if roles != expect:
            errors.append(f"narrative_block_paired: 块 {bid} role 序列 {roles} 应为 {expect}")
        first_gid = rows[0][1]
        if not has_tr.get(first_gid):
            errors.append(f"{first_gid} narrative_block_paired: 块 {bid} 入口组缺 transition_in"
                          "(可为显式 hard_cut + reason,表示有意硬切)")
        last_i = rows[-1][0]
        if last_i + 1 < len(groups):
            nxt = groups[last_i + 1].get("group_id", "?")
            if not has_tr.get(nxt):
                errors.append(f"{nxt} narrative_block_paired: 块 {bid} 出口(块尾 {rows[-1][1]} 的下一组)缺 transition_in")
    return errors


def main():
    ap = argparse.ArgumentParser(description="generation_groups 机检 / 草案分组")
    ap.add_argument("shot_list", help="shot_list.json 路径")
    ap.add_argument("--propose", action="store_true", help="生成贪心草案分组")
    ap.add_argument("--write", action="store_true", help="配合 --propose:把草案写回 shot_list.json")
    ap.add_argument("--narration", help="narration.md 路径(缺省按数据布局自动推导)")
    ap.add_argument("--skip-7d", action="store_true",
                    help="跳过 §7D ① 旁白挂点/audio_plan 机检(仅查生成组)")
    ap.add_argument("--skip-transition", action="store_true",
                    help="跳过组间转场机检 transition_ok(transition_in / narrative_block)")
    args = ap.parse_args()

    path = Path(args.shot_list)
    data = json.loads(path.read_text())
    global MAX_GROUP_S
    MAX_GROUP_S = project_max_group_s(path)

    if args.propose:
        groups = propose_groups(data.get("shots") or [], project_id_step(path))
        data["generation_groups"] = groups
        durs = [g["total_duration_s"] for g in groups]
        sizes = [len(g["shots"]) for g in groups]
        print(f"草案分组: {len(data.get('shots') or [])} 镜 → {len(groups)} 组;"
              f" 组镜数分布 {sorted(set(sizes))},组时长 {min(durs)}–{max(durs)}s")
        if args.write:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            print(f"已写回 {path}")

    errors = check(data)
    # 密集组限长对存量 shot_list(日期早于时间尺截止)只 WARN,不挡 G6
    if _tc.is_legacy(_tc.doc_date(data)):
        dense = [e for e in errors if " group_density_ok: " in e]
        for e in dense:
            print(f"  ⚠ {e}(存量 shot_list,仅提醒)")
        errors = [e for e in errors if e not in dense]
    if path.parent.parent.name == "directing":
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from modules import script_keypoints as kp
        try:
            missing = kp.coverage(path.parent.parent.parent, path.parent.name, path.name)
            if missing:
                errors.append("script_keypoints_covered: 关键点缺少镜头体现记录 " + ", ".join(missing))
        except (ValueError, OSError, KeyError) as error:
            errors.append(f"script_keypoints_covered: 无法读取关键点记录: {error}")
    narration_on = project_narration_enabled(path)
    if not args.skip_7d:
        narr_text = None
        if narration_on:
            narr_path = Path(args.narration) if args.narration else derive_narration_path(path)
            narr_text = narr_path.read_text() if narr_path and narr_path.is_file() else None
        errors += check_7d(data, narr_text, narration_on)
    # §8A speakers_le_3(只数画内说话人)+ 声画分离 placement 系列(2026-10-03)
    names = None
    try:
        from modules.dialogue_tts import name_index
        names = name_index(project_root_of(path))
    except Exception:
        names = None
    errors += check_speakers(data, names)
    ss_mode = project_sound_split(path)
    if ss_mode == "off":
        print("[placement] skipped: sound_split off(项目「声画分离」已关闭,全部台词画内)")
    errors += check_placement(path, data, ss_mode)
    if not args.skip_transition:
        errors += check_transitions(data, project_insert_budget_pct(Path(args.shot_list)))
    if errors:
        print(f"机检未通过({len(errors)} 项):")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    groups = data["generation_groups"]
    n_anchor = len(data.get("narration_anchors") or [])
    n_tr = sum(1 for g in groups if transition_of(g)["type"] != "hard_cut")
    print(f"机检通过: {len(groups)} 组全部合规"
          + ("" if args.skip_7d else
             (f";§7D ① 挂点 {n_anchor} 条/audio_plan 齐备" if narration_on
              else ";§7D ① 旁白已关闭(skipped: narration off)/audio_plan 齐备"))
          + ("" if args.skip_transition else f";transition_ok 非硬切转场 {n_tr} 处"))


if __name__ == "__main__":
    main()
