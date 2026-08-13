# -*- coding: utf-8 -*-
"""captions.py — 花字(caption)渲染共享原语(主流程 p9-caption* 与 av 插件 av2/av4-caption* 共用)。

职责边界(WORKFLOW.md「花字与花字音效」节):
  - 设计(captions.json)由 10-editing/caption Agent 产出;本模块只负责**确定性执行**:
    schema 校验、字体/音效 manifest 扫描、ASS 生成、逐组烧录副本、SFX 轨合成、预混封装。
  - Agent 一律通过 code/render_captions.py CLI 调用本模块,禁止自写花字 ffmpeg 滤镜——
    幂等回执与机检口径都建立在本模块的统一编码参数上。

关键纪律:
  - 原组 clip 永不改动,烧录产物是 assets/clips_caption/{ep}/{grp}.mp4 副本;
    回执 {grp}.render.json 记录(源 clip sha256, 花字片段 hash, 字体 hash),四元组
    一致即 SKIP——「某组不满意只重渲该组」的闭环靠这个成立。
  - av 流程组 clip 无声(机检 clip_silent),副本保持无声;主流程组 clip 音轨 -c:a copy。
  - 花字版成片音轨布局(2026-08-08 定):a:0 = 母带+SFX 预混(播放器开箱即听),
    a:1 = 原母带流拷贝(零重编码存档轨,机检对 a:1 逐帧校验)。干净版 final.mp4 不经本模块。
  - 严禁 -shortest(会静默截断音频末帧,见 avsync.py)。

纯函数库,无 CLI。CLI 入口 code/render_captions.py;机检入口 code/check_captions.py。
"""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from avsync import (canonical_sha256, file_sha256, probe_duration,
                    require_tools, _run)

SCHEMA_VERSION = 2
RENDERER_VERSION = "r6"                       # 渲染逻辑变更即递增 → 旧回执自动过期重渲
X264_ARGS = ["-c:v", "libx264", "-crf", "18", "-preset", "medium",
             "-pix_fmt", "yuv420p"]          # 逐组统一,花字版拼装才能走 concat 流拷贝
SFX_SR = 48000
SFX_BITRATE = "192k"
DEFAULT_SFX_GAIN_DB = -6.0                    # 基准:弱于母带人声

CAPTION_TYPES = ("headline", "keyword",       # v2 新增
                 "location", "time", "skill", "faction", "other")   # v1 保留
ANIM_IN = ("pop_bounce", "pop", "smash", "fade", "slide_up", "slide_down",
           "slide_left", "slide_right", "none")   # smash=巨字砸入(300%→100%,带模糊)
ANIM_OUT = ("fade", "dissolve", "zoom_out", "flash_out", "none")
# dissolve=淡出缩小;zoom_out=放大冲出镜头(参考片大招①);flash_out=闪白熄灭(大招②)

# position 关键词 → (x 比例, y 比例, ASS \an 锚点)。刻意不提供 bottom 贴底位——
# 底部是 subtitle 烧录安全区(subtitle SOUL:下边距 2-4%、≤2 行),花字不得进入。
POSITIONS = {
    "top_left":      (0.08, 0.12, 7),
    "top_center":    (0.50, 0.12, 8),
    "top_right":     (0.92, 0.12, 9),
    "mid_left":      (0.08, 0.45, 4),
    "center":        (0.50, 0.40, 5),
    "mid_right":     (0.92, 0.45, 6),
    "lower_center":  (0.50, 0.72, 2),         # 仍在字幕区之上
}
_POSITION_ALIASES = {"top": "top_center", "left": "mid_left", "right": "mid_right",
                     "bottom": "lower_center", "bottom_center": "lower_center"}

_HEX_COLOR = re.compile(r"^#?([0-9a-fA-F]{6})$")

# ---------------------------------------------------------------- 基础探测

_FFMPEG_BIN: str | None = None


def ffmpeg_bin() -> str:
    """选一个带 libass(subtitles 滤镜)的 ffmpeg。

    homebrew-core 自 2026 起把 ffmpeg 拆成精简版 `ffmpeg` 与完整版 `ffmpeg-full`
    (keg-only,不进 PATH)——花字烧录必须 libass。解析顺序:
      $VIDEOAGENTS_FFMPEG > ffmpeg-full keg > PATH 里的 ffmpeg。
    找不到带 subtitles 的即回落 PATH(由 ffmpeg_has_libass 机检显式拦截)。
    """
    global _FFMPEG_BIN
    if _FFMPEG_BIN:
        return _FFMPEG_BIN
    import os as _os
    import shutil as _shutil
    cands = [_os.environ.get("VIDEOAGENTS_FFMPEG"),
             "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
             "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
             _shutil.which("ffmpeg")]
    for c in cands:
        if not c or not Path(c).is_file() and not _shutil.which(c):
            continue
        try:
            if "subtitles" in _run([c, "-hide_banner", "-filters"], timeout=60):
                _FFMPEG_BIN = c
                return c
        except (OSError, subprocess.TimeoutExpired):
            continue
    _FFMPEG_BIN = "ffmpeg"
    return _FFMPEG_BIN


def probe_video_info(path: str) -> dict:
    """视频流基本信息 + 是否含音轨。缺流/坏文件抛错。"""
    require_tools("ffprobe")
    out = _run(["ffprobe", "-v", "error", "-show_entries",
                "stream=index,codec_type,codec_name,width,height,r_frame_rate",
                "-show_entries", "format=duration", "-of", "json", str(path)], timeout=60)
    data = json.loads(out)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        raise RuntimeError(f"{path} 无视频流")
    num, den = (v.get("r_frame_rate") or "24/1").split("/")
    return {
        "width": int(v["width"]), "height": int(v["height"]),
        "fps": (int(num) / int(den)) if int(den) else 0.0,
        "duration_s": float(data.get("format", {}).get("duration", 0.0)),
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
        "vcodec": v.get("codec_name"),
    }


def ffmpeg_has_libass() -> bool:
    """caption_toolchain_verified 的实测依据:所选 ffmpeg 编译须含 subtitles(libass)滤镜。"""
    require_tools("ffprobe")
    return "subtitles" in _run([ffmpeg_bin(), "-hide_banner", "-filters"], timeout=60)


# ---------------------------------------------------------------- 字体 manifest

_SYSTEM_FONT_DIRS = (            # macOS 优先;linux/win 路径预留,不存在即跳过
    "/System/Library/Fonts", "/System/Library/Fonts/Supplemental",
    "/Library/Fonts", "~/Library/Fonts",
    "/usr/share/fonts", "~/.fonts", "C:/Windows/Fonts",
)
_FONT_EXTS = (".ttf", ".otf", ".ttc")
_CJK_PROBE = "花字体验中文"       # cmap 覆盖这些字符即认定支持 CJK


def _font_records(path: Path, source: str) -> list[dict]:
    """一个字体文件 → 0..n 条 manifest 记录(ttc 一文件多面)。

    fontTools 可用时读 name 表取 family/subfamily 并用 cmap 判 CJK 覆盖;
    不可用则降级为文件名启发(cjk 置 None=未知,渲染时不拦但 font_resolved 会警告)。
    """
    try:
        import logging
        logging.getLogger("fontTools").setLevel(logging.ERROR)   # 压掉坏表警告刷屏
        from fontTools.ttLib import TTFont, TTCollection   # noqa: PLC0415
    except ImportError:
        stem = path.stem
        return [{"family": stem, "subfamily": "", "index": 0, "cjk": None}]

    def one(tt, idx):
        name = tt["name"]
        fam = (name.getDebugName(16) or name.getDebugName(1) or path.stem)
        sub = (name.getDebugName(17) or name.getDebugName(2) or "")
        try:
            cmap = tt.getBestCmap()
            cjk = all(ord(ch) in cmap for ch in _CJK_PROBE)
        except Exception:
            cjk = None
        return {"family": fam, "subfamily": sub, "index": idx, "cjk": cjk}

    try:
        if path.suffix.lower() == ".ttc":
            coll = TTCollection(str(path), lazy=True)
            recs = [one(f, i) for i, f in enumerate(coll.fonts)]
            coll.close()
            return recs
        tt = TTFont(str(path), lazy=True)
        rec = [one(tt, 0)]
        tt.close()
        return rec
    except Exception:
        return []                 # 坏字体文件直接跳过,不进 manifest


def scan_fonts(user_dir: Path, out_path: Path | None = None) -> dict:
    """扫系统字体目录 + 用户外置目录(data/fonts,gitignored),生成 manifest。

    id 规则:sys:/user: 前缀 + family(去空格);同名冲突追加 #index。
    user 目录的字体在渲染时通过 subtitles=...:fontsdir= 生效,无需安装。
    """
    fonts, seen = [], set()
    dirs = [(Path(d).expanduser(), "system") for d in _SYSTEM_FONT_DIRS] \
        + [(Path(user_dir), "user")]
    for root, source in dirs:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if p.suffix.lower() not in _FONT_EXTS or not p.is_file():
                continue
            for rec in _font_records(p, source):
                if rec["family"].startswith("."):
                    continue          # 系统隐藏字体(.Hiragino*/.LastResort)不入库
                fid = f"{'user' if source == 'user' else 'sys'}:{rec['family'].replace(' ', '')}"
                if rec["subfamily"] and rec["subfamily"].lower() not in ("regular", "normal"):
                    fid += f"-{rec['subfamily'].replace(' ', '')}"
                if fid in seen:
                    fid += f"#{rec['index']}"
                if fid in seen:
                    continue
                seen.add(fid)
                fonts.append({"id": fid, "family": rec["family"],
                              "subfamily": rec["subfamily"], "path": str(p),
                              "index": rec["index"], "cjk": rec["cjk"], "source": source})
    manifest = {"schema": "fonts.manifest.v1", "fonts": fonts}
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    return manifest


def resolve_font(font_id: str, manifest: dict) -> dict:
    """font_id → manifest 记录。找不到即抛错——libass 找不到字体会**静默**回落
    默认字体(机检 font_resolved 的存在意义),必须在渲染前显式失败。"""
    for f in manifest.get("fonts", []):
        if f["id"] == font_id:
            return f
    raise RuntimeError(
        f"font_id {font_id!r} 不在 fonts manifest 中;先跑 render_captions.py fonts-scan,"
        "或把字体文件放进 data/fonts/ 后重扫(外置字体不进 git 仓库)")


# ---------------------------------------------------------------- 音效 manifest

_SFX_EXTS = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac")


def scan_sfx(sfx_dir: Path, out_path: Path | None = None) -> dict:
    """扫 data/sfx/(gitignored)生成音效 manifest。

    id = 文件名 stem;tags = 一级子目录名 + 文件名按 -_ 分词(whoosh_impact_01
    → ["whoosh","impact"]);license 读同目录 LICENSE*.txt 首行,缺省 unknown
    (11-qa/copyright 终审会查,建议只放 CC0 素材)。
    """
    items = []
    root = Path(sfx_dir)
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.suffix.lower() not in _SFX_EXTS or not p.is_file():
                continue
            rel = p.relative_to(root)
            tags = [t for t in re.split(r"[-_]+", p.stem) if t and not t.isdigit()]
            if len(rel.parts) > 1:
                tags = [rel.parts[0]] + tags
            lic = "unknown"
            for cand in (p.parent / "LICENSE.txt", p.parent / "LICENSE", root / "LICENSE.txt"):
                if cand.is_file():
                    lic = cand.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[0][:80]
                    break
            items.append({"id": p.stem, "file": str(rel), "tags": sorted(set(t.lower() for t in tags)),
                          "duration_s": round(probe_duration(str(p)), 3),
                          "license": lic, "gain_db_default": DEFAULT_SFX_GAIN_DB})
    manifest = {"schema": "sfx.manifest.v1", "sfx": items}
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    return manifest


def resolve_sfx(sfx_id: str, manifest: dict, sfx_dir: Path) -> Path:
    for s in manifest.get("sfx", []):
        if s["id"] == sfx_id:
            p = Path(sfx_dir) / s["file"]
            if not p.is_file():
                raise RuntimeError(f"sfx {sfx_id!r} 的文件 {p} 不存在,重跑 sfx-scan")
            return p
    raise RuntimeError(f"sfx_id {sfx_id!r} 不在 sfx manifest 中(data/sfx/ 放素材后 sfx-scan)")


# ---------------------------------------------------------------- schema 校验

def load_captions(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data.get("captions"), list):
        raise RuntimeError(f"{path} 缺 captions 数组")
    return data


def validate_captions(data: dict, shot_list: dict,
                      fonts_manifest: dict | None = None,
                      sfx_manifest: dict | None = None,
                      beat_text: str | None = None,
                      proj_root: Path | None = None) -> list[str]:
    """design 段机检核心。返回问题列表(空=通过)。

    v1 文件(无 schema_version/group_id)不做静默猜测:直接报错要求升级 v2——
    v1 从未被渲染过,不存在存量兼容负担;预览页读 v1 只展示不渲染。
    beat_text 提供时执行 caption_text_from_source(av 项目无 dictionary 的防造词
    口径):花字文本去标点后的每个 2+ 字连续片段须能在母带原文中找到。
    """
    issues = []
    if data.get("schema_version") != SCHEMA_VERSION:
        return [f"schema_version 必须为 {SCHEMA_VERSION}(v1 花字清单请升级:补 group_id/"
                "local_start/local_end/style_presets)"]
    presets = data.get("style_presets") or {}
    if not presets:
        issues.append("缺 style_presets(全集收敛到 ≤4 个预设)")
    if len(presets) > 4:
        issues.append(f"style_presets 有 {len(presets)} 个,超过全集 ≤4 的收敛上限")
    groups = {g["group_id"]: g for g in shot_list.get("generation_groups", [])}
    seen_ids = set()
    for i, c in enumerate(data.get("captions", [])):
        tag = c.get("id") or f"captions[{i}]"
        if c.get("id") in seen_ids:
            issues.append(f"{tag}: id 重复")
        seen_ids.add(c.get("id"))
        if not (c.get("text") or "").strip():
            issues.append(f"{tag}: text 为空")
        if c.get("type") not in CAPTION_TYPES:
            issues.append(f"{tag}: type {c.get('type')!r} 不在 {CAPTION_TYPES}")
        grp = groups.get(c.get("group_id"))
        if grp is None:
            issues.append(f"{tag}: group_id {c.get('group_id')!r} 不在 shot_list.generation_groups")
            continue
        span = float(grp.get("av_span_s") or grp.get("total_duration_s") or 0)
        ls, le = c.get("local_start"), c.get("local_end")
        if not (isinstance(ls, (int, float)) and isinstance(le, (int, float)) and 0 <= ls < le):
            issues.append(f"{tag}: local_start/local_end 非法({ls!r}/{le!r})")
        elif le > span + 0.05:
            issues.append(f"{tag}: local_end {le} 超出组时长 {span}")
        # 集级/组内双写对账:start ≈ 组时间轴起点 + local_start(±0.1s)
        base = grp.get("audio_in_s")
        if base is not None and isinstance(ls, (int, float)) \
                and isinstance(c.get("start"), (int, float)) \
                and abs(float(c["start"]) - (float(base) + float(ls))) > 0.1:
            issues.append(f"{tag}: start {c['start']} 与 组起点{base}+local_start{ls} 不一致")
        sref = c.get("style_ref") or ""
        if sref.startswith("preset:"):
            if sref[7:] not in presets:
                issues.append(f"{tag}: style_ref {sref!r} 未在 style_presets 定义")
        elif "#" not in sref:
            issues.append(f"{tag}: style_ref 须为 preset:xxx 或 style.json#xxx")
        anim = c.get("animation") or {}
        if (anim.get("in") or {}).get("type", "none") not in ANIM_IN:
            issues.append(f"{tag}: animation.in.type 不在 {ANIM_IN}")
        if (anim.get("out") or {}).get("type", "none") not in ANIM_OUT:
            issues.append(f"{tag}: animation.out.type 不在 {ANIM_OUT}")
        pos = c.get("position") or "center"
        if _POSITION_ALIASES.get(pos, pos) not in POSITIONS:
            issues.append(f"{tag}: position {pos!r} 不在 {sorted(POSITIONS)}")
        if fonts_manifest is not None and sref.startswith("preset:") and sref[7:] in presets:
            try:
                merged = {**presets[sref[7:]], **(c.get("style_override") or {})}
                resolve_font(merged.get("font_id", ""), fonts_manifest)
            except RuntimeError as e:
                issues.append(f"{tag}: {e}")
        sfx = c.get("sfx")
        if sfx and sfx_manifest is not None:
            for one in (sfx if isinstance(sfx, list) else [sfx]):
                if not any(s["id"] == one.get("sfx_id") for s in sfx_manifest.get("sfx", [])):
                    issues.append(f"{tag}: sfx_id {one.get('sfx_id')!r} 不在 sfx manifest")
        segs = c.get("segments")
        if segs is not None:
            joined = "".join(s.get("text", "") for s in segs)
            if joined != (c.get("text") or ""):
                issues.append(f"{tag}: segments 拼接({joined!r})≠ text"
                              "(多色分词只改颜色,文本以 text 为准参与溯源机检)")
        if beat_text is not None:
            for frag in re.findall(r"[\u4e00-\u9fff]{2,}", c.get("text") or ""):
                if frag not in beat_text:
                    issues.append(f"{tag}: 文本片段「{frag}」不在母带原文中(禁造词,"
                                  "caption_text_from_source)")
    # 素材图卡(cards,2026-08-10):只允许项目内图片,零外部素材
    for i, cd in enumerate(data.get("cards", []) or []):
        tag = cd.get("id") or f"cards[{i}]"
        grp = groups.get(cd.get("group_id"))
        if grp is None:
            issues.append(f"{tag}: group_id {cd.get('group_id')!r} 不在 generation_groups")
            continue
        span = float(grp.get("av_span_s") or grp.get("total_duration_s") or 0)
        ls, le = cd.get("local_start"), cd.get("local_end")
        if not (isinstance(ls, (int, float)) and isinstance(le, (int, float)) and 0 <= ls < le):
            issues.append(f"{tag}: local_start/local_end 非法({ls!r}/{le!r})")
        elif le > span + 0.05:
            issues.append(f"{tag}: local_end {le} 超出组时长 {span}")
        img = str(cd.get("image") or "")
        if not img or img.startswith(("/", "..")):
            issues.append(f"{tag}: image 必须是项目内相对路径,得到 {img!r}")
        elif proj_root is not None and not (proj_root / img).is_file():
            issues.append(f"{tag}: 图卡图片不存在:{img}")
        sfx = cd.get("sfx")
        if sfx and sfx_manifest is not None:
            for one in (sfx if isinstance(sfx, list) else [sfx]):
                if not any(s["id"] == one.get("sfx_id") for s in sfx_manifest.get("sfx", [])):
                    issues.append(f"{tag}: sfx_id {one.get('sfx_id')!r} 不在 sfx manifest")
    return issues


# ---------------------------------------------------------------- ASS 生成

def _ass_color(hex_color: str, alpha: int = 0) -> str:
    """#RRGGBB → ASS &HAABBGGRR(注意 BGR 序)。"""
    m = _HEX_COLOR.match(hex_color or "")
    rgb = m.group(1) if m else "FFFFFF"
    r, g, b = rgb[0:2], rgb[2:4], rgb[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc_text(text: str) -> str:
    return text.replace("\\", "").replace("{", "").replace("}", "").replace("\n", "\\N")


def _merged_style(cap: dict, presets: dict) -> dict:
    sref = cap.get("style_ref") or ""
    base = presets.get(sref[7:], {}) if sref.startswith("preset:") else {}
    return {**base, **(cap.get("style_override") or {})}


# ---- 样式引擎 Pro(2026-08-10):逐字排版/双层描边/渐变/底衬/斜竖排/光晕/溶解 ----
# 设计对标 花字音效demo/后期.mp4:综艺花字语言——每条花字几乎都有双层描边,
# 大标题用垂直渐变书法体,关键词多色分词、逐字底衬、斜排竖排混用,入场逐字弹跳。
# 全部在 ASS/libass 内实现(渐变=多条 \clip 色带,底衬=\p1 矢量矩形),保持确定性。

_FULLWIDTH = re.compile(r"[ᄀ-鿿　-ヿ가-힯＀-￯]")


def _char_adv(ch: str, size: int, spacing: float) -> float:
    """字符步进宽:CJK 全宽 ≈ 字号,拉丁/数字 ≈ 0.55 字号。"""
    return (size if _FULLWIDTH.match(ch) else size * 0.55) + spacing


def _hex_rgb(color: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", color or "")
    if not m:
        return (255, 255, 255)
    v = m.group(1)
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))


def _lerp_hex(c1: str, c2: str, t: float) -> str:
    a, b = _hex_rgb(c1), _hex_rgb(c2)
    return "#" + "".join(f"{round(a[i] + (b[i] - a[i]) * t):02X}" for i in range(3))


def _grad_bands(grad: dict) -> list[str]:
    """垂直渐变 → N 条色带(上→下)。bands=4~6 足以以假乱真。"""
    n = max(2, int(grad.get("bands", 5)))
    top, bottom = grad.get("top", "#FFFFFF"), grad.get("bottom", "#888888")
    return [_lerp_hex(top, bottom, i / (n - 1)) for i in range(n)]


def _rot_pt(px: float, py: float, cx: float, cy: float, deg: float) -> tuple[int, int]:
    import math
    r = math.radians(deg)
    dx, dy = px - cx, py - cy
    return (round(cx + dx * math.cos(r) - dy * math.sin(r)),
            round(cy + dx * math.sin(r) + dy * math.cos(r)))


def _caption_runs(cap: dict) -> list[tuple[str, str | None]]:
    """展开为 (字符, 分词色) 列表。segments 提供多色分词;无则全 None(用样式填充)。"""
    segs = cap.get("segments")
    disp = cap.get("display_text")
    if segs:
        return [(ch, s.get("color")) for s in segs for ch in s.get("text", "")]
    return [(ch, None) for ch in (disp or cap.get("text", ""))]


def _entrance_tags(t_in: str, in_ms: int, dur_ms: int, out_kind: str,
                   out_ms: int, breathe: int) -> str:
    """单字符的入/驻/出 override 标签(位置标签由调用方拼)。

    入出场力度对标 花字参考.mp4(2026-08-11):smash=巨字带模糊砸入;
    zoom_out=尾段放大 3 倍冲出镜头;flash_out=尾段闪白后熄灭。
    """
    tags = ""
    if t_in == "pop_bounce":
        k = max(1, int(in_ms * 0.6))
        tags += (f"\\fscx24\\fscy24\\t(0,{k},\\fscx116\\fscy116)"
                 f"\\t({k},{in_ms},\\fscx100\\fscy100)")
    elif t_in == "pop":
        tags += f"\\fscx10\\fscy10\\t(0,{in_ms},\\fscx100\\fscy100)"
    elif t_in == "smash":            # 巨字砸入:300%+模糊 → 急减速落位(accel<1 前快后慢)
        tags += (f"\\fscx300\\fscy300\\blur4"
                 f"\\t(0,{in_ms},0.6,\\fscx100\\fscy100\\blur0)")
    if breathe and dur_ms > in_ms + 400:
        tags += f"\\t({in_ms},{dur_ms},\\fscx{breathe}\\fscy{breathe})"
    # 冲击帧必须发生在不透明时(2026-08-11 实测教训):smash 淡入压到 ≤90ms,
    # 否则巨字期全程半透明,等看清已落位——冲击力被淡入吃掉
    fade_in = (min(90, in_ms) if t_in == "smash" else in_ms) if t_in != "none" else 0
    fade_out = out_ms if out_kind in ("fade", "dissolve", "zoom_out") else \
        max(80, int(out_ms * 0.4)) if out_kind == "flash_out" else 0
    if fade_in or fade_out:
        tags += f"\\fad({fade_in},{fade_out})"
    out_start = max(0, dur_ms - out_ms)
    if out_kind == "dissolve":       # 溶解出场:尾段缩小(配合 \fad 淡出)
        tags += f"\\t({out_start},{dur_ms},\\fscx78\\fscy78)"
    elif out_kind == "zoom_out":
        # 放大冲出:缩放窗口(1.6×out)早于淡出窗口(1×out)开跑——
        # 前 0.6×out 完全不透明地放大到 ~180%,随后边爆边淡,最猛处仍可见
        zoom_start = max(0, dur_ms - int(out_ms * 1.6))
        tags += f"\\t({zoom_start},{dur_ms},1.4,\\fscx330\\fscy330)"
    elif out_kind == "flash_out":    # 闪白:填充/描边急变白 + 轻微涨大,再短促熄灭
        flash_end = min(dur_ms, out_start + int(out_ms * 0.5))
        tags += (f"\\t({out_start},{flash_end},\\1c&HFFFFFF&\\3c&HF0F8FF&)"
                 f"\\t({out_start},{dur_ms},\\fscx108\\fscy108)")
    return tags


def build_group_ass(captions: list[dict], presets: dict, fonts_manifest: dict,
                    width: int, height: int) -> str:
    """一个组的花字 → 完整 ASS 文本(时间用组内 local_start/local_end)。

    逐字排版引擎:每个字符独立 Dialogue 栈(光晕→底衬→挤出→外描边→渐变/填充),
    支持 char_stagger_s 逐字错落入场、angle_deg 斜排、vertical 竖排、
    segments 多色分词、gradient 垂直渐变、stroke2 双层描边、glow 光晕、
    char_box 逐字底衬、出场 dissolve 溶解缩小。
    """
    styles, events = {}, []
    for cap in sorted(captions, key=lambda c: float(c.get("local_start", 0))):
        st = _merged_style(cap, presets)
        font = resolve_font(st.get("font_id", ""), fonts_manifest)
        size = max(12, int(height * float(st.get("size_pct", 7)) / 100))
        stroke = st.get("stroke") or {}
        w1 = round(height * float(stroke.get("width_pct", 0.5)) / 100, 1)
        inner_col = stroke.get("color", "#000000")
        stroke2 = st.get("stroke2") or {}
        w2 = round(height * float(stroke2.get("width_pct", 0)) / 100, 1)
        outer_col = stroke2.get("color", "#000000")
        spacing = height * float(st.get("spacing_pct", 0)) / 100
        # Style 行只承载 字体/字号(描边、颜色全部走 override,避免样式组合爆炸);
        # 底衬块用 BorderStyle=3 的孪生 Style(不透明底盒随字对齐——\p1 矢量画盒
        # 会被 libass 按基线错位,2026-08-10 前科),盒色走 \3c、盒垫走 \bord
        skey = (font["family"], size)
        if skey in styles:
            sname = styles[skey][0]
        else:
            sname = f"S{len(styles)}"
            styles[skey] = (sname, (
                f"Style: {sname},{font['family']},{size},&H00FFFFFF,&H000000FF,"
                f"&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,2,0,5,20,20,20,1\n"
                f"Style: {sname}B,{font['family']},{size},&H00FFFFFF,&H000000FF,"
                f"&H00000000,&H64000000,-1,0,0,0,100,100,0,0,3,2,0,5,20,20,20,1"))
        runs = _caption_runs(cap)
        if not runs:
            continue
        pos_key = _POSITION_ALIASES.get(cap.get("position", "center"),
                                        cap.get("position", "center"))
        fx, fy, an = POSITIONS.get(pos_key, POSITIONS["center"])
        bx, by = width * fx, height * fy
        vertical = bool(st.get("vertical"))
        angle = float(st.get("angle_deg", 0))
        # 布局:字符中心序列(锚点语义:1/4/7 左对齐,2/5/8 居中,3/6/9 右对齐;
        # 竖排时上/中/下对齐同理作用于 y)
        advs = [(_char_adv(ch, size, spacing)) for ch, _ in runs]
        total = sum(advs)
        align_h = {1: 0, 4: 0, 7: 0, 2: -total / 2, 5: -total / 2, 8: -total / 2,
                   3: -total, 6: -total, 9: -total}[an]
        # 垂直方向:上锚(7/8/9)字符中心下移半字高,下锚(1/2/3)上移
        v_off = size * 0.6 if an in (7, 8, 9) else (-size * 0.6 if an in (1, 2, 3) else 0)
        centers = []
        acc = 0.0
        for adv in advs:
            if vertical:
                centers.append((bx, by + align_h + acc + adv / 2 + v_off))
            else:
                centers.append((bx + align_h + acc + adv / 2, by + v_off))
            acc += adv
        if angle:
            centers = [_rot_pt(cx, cy, bx, by + v_off, angle) for cx, cy in centers]
        # 时序
        t0, t1 = float(cap["local_start"]), float(cap["local_end"])
        anim = cap.get("animation") or {}
        ain, aout = anim.get("in") or {}, anim.get("out") or {}
        t_in = ain.get("type", "fade")
        in_ms = int(float(ain.get("duration_s", 0.3)) * 1000)
        out_kind = aout.get("type", "fade")
        out_ms = int(float(aout.get("duration_s", 0.25)) * 1000)
        stagger = float(st.get("char_stagger_s", 0))
        breathe = 104 if t_in == "pop_bounce" else (103 if str(t_in).startswith("slide") else 0)
        # 视觉要素
        grad = st.get("gradient")
        bands = _grad_bands(grad) if grad else None
        glow = st.get("glow")
        cbox = st.get("char_box")
        shadow = st.get("shadow") or {}
        ex_layers = max(1, int(st.get("layers", 1)))
        depth = max(1, int(height * float(shadow.get("depth_pct", 0.4)) / 100))
        fill_default = st.get("color", "#FFFFFF")
        slide_off = max(24, int(height * 0.06))
        for i, ((ch, seg_col), (cx, cy)) in enumerate(zip(runs, centers)):
            if ch.strip() == "":
                continue
            ct0 = t0 + i * stagger
            dur_ms = max(200, int((t1 - ct0) * 1000))
            ent = _entrance_tags(t_in, in_ms, dur_ms, out_kind, out_ms, breathe)
            rot = f"\\frz{-angle:.1f}" if angle else ""

            def _place(px: float, py: float) -> str:
                if str(t_in).startswith("slide"):
                    dx = -slide_off if t_in == "slide_right" else \
                        slide_off if t_in == "slide_left" else 0
                    dy = -slide_off if t_in == "slide_down" else \
                        slide_off if t_in == "slide_up" else 0
                    return (f"\\an5\\move({px + dx:.0f},{py + dy:.0f},"
                            f"{px:.0f},{py:.0f},0,{in_ms}){rot}")
                return f"\\an5\\pos({px:.0f},{py:.0f}){rot}"

            row = f"{_ass_time(ct0)},{_ass_time(t1)},{sname},,0,0,0,,"
            # L0 光晕(柔和外发光)
            if glow:
                gw = round(height * float(glow.get("width_pct", 1.2)) / 100, 1)
                gc = _ass_color(glow.get("color", "#66CCFF"))
                events.append(
                    f"Dialogue: 0,{row}{{{_place(cx, cy)}{ent}\\bord{gw + w1 + w2}"
                    f"\\blur6\\1c{gc}\\3c{gc}\\1a&H80&\\3a&H60&\\shad0}}{_esc_text(ch)}")
            # L1 逐字底衬块:BorderStyle=3 孪生 Style,\3c=盒色、\bord=盒垫、
            # \1a 全透明藏字形(盒随字形 bbox 自动对齐);radius 该方案不支持(方角)
            if cbox:
                pad = round(size * float(cbox.get("pad_pct", 10)) / 200, 1)
                bc = _ass_color(cbox.get("color", "#2E7D32"))
                ba = max(0, min(255, int(cbox.get("alpha", 0))))
                events.append(
                    f"Dialogue: 1,{_ass_time(ct0)},{_ass_time(t1)},{sname}B,,0,0,0,,"
                    f"{{{_place(cx, cy)}{ent}\\bord{pad}\\3c{bc}\\3a&H{ba:02X}&"
                    f"\\1a&HFF&\\shad0}}{_esc_text(ch)}")
            # L2.. 立体挤出(底层深色偏移副本)
            for li in range(ex_layers - 1, 0, -1):
                d = int(depth * li / max(1, ex_layers - 1))
                col = _ass_color(shadow.get("color", "#303030"))
                events.append(
                    f"Dialogue: {2 + ex_layers - 1 - li},{row}"
                    f"{{{_place(cx + d, cy + d)}{ent}\\1c{col}\\3c{col}"
                    f"\\bord1\\be1\\shad0}}{_esc_text(ch)}")
            base_layer = 2 + ex_layers
            # L 外描边圈(双层描边的外圈)
            if w2 > 0:
                oc = _ass_color(outer_col)
                events.append(
                    f"Dialogue: {base_layer},{row}{{{_place(cx, cy)}{ent}"
                    f"\\bord{w1 + w2}\\1c{oc}\\3c{oc}\\shad0}}{_esc_text(ch)}")
            # L 填充 + 内描边(渐变=多条 \clip 色带;分词色 > 渐变 > 样式色)
            ic = _ass_color(inner_col)
            if bands and not seg_col:
                half_h = size * 0.62 + w1
                half_w = _char_adv(ch, size, 0) / 2 + w1 + 2
                n = len(bands)
                for bi, bcol in enumerate(bands):
                    y1 = cy - half_h + (2 * half_h) * bi / n
                    y2 = cy - half_h + (2 * half_h) * (bi + 1) / n + 0.5
                    events.append(
                        f"Dialogue: {base_layer + 1 + bi},{row}{{{_place(cx, cy)}{ent}"
                        f"\\bord{w1}\\1c{_ass_color(bcol)}\\3c{ic}\\shad0"
                        f"\\clip({cx - half_w:.0f},{y1:.0f},{cx + half_w:.0f},{y2:.0f})}}"
                        f"{_esc_text(ch)}")
            else:
                fc = _ass_color(seg_col or fill_default)
                events.append(
                    f"Dialogue: {base_layer + 1},{row}{{{_place(cx, cy)}{ent}"
                    f"\\bord{w1}\\1c{fc}\\3c{ic}\\shad0}}{_esc_text(ch)}")
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}",
        f"PlayResY: {height}", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        *[s[1] for s in styles.values()], "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    return "\n".join(head + events) + "\n"

# ---------------------------------------------------------------- 逐组烧录

def _filter_escape(path: str) -> str:
    """ffmpeg filter 参数里的路径转义(: 与 ' 与 \\)。"""
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _fonts_hash(captions: list[dict], presets: dict, fonts_manifest: dict) -> str:
    used = []
    for cap in captions:
        st = _merged_style(cap, presets)
        try:
            f = resolve_font(st.get("font_id", ""), fonts_manifest)
            p = Path(f["path"])
            stt = p.stat()
            used.append((f["id"], str(p), stt.st_size, int(stt.st_mtime)))
        except (RuntimeError, OSError):
            used.append((st.get("font_id", "?"), "missing", 0, 0))
    return canonical_sha256(sorted(used))


def _flat_fontsdir(captions: list[dict], presets: dict,
                   fonts_manifest: dict) -> Path:
    """本组用到的字体文件 → 扁平临时目录(软链),作 subtitles= 的 fontsdir。

    libass 的 fontsdir **不递归子目录**(2026-08-10 实测前科:data/fonts/google-ofl/
    下的字体没被加载,标题/关键词全部静默回落默认字体,机检照样全绿)。
    系统已装字体 libass 走 CoreText/fontconfig 按 family 命中,不依赖本目录;
    user 字体(data/fonts/ 任意层级)必须由这里拍平后 libass 才能看见。
    """
    d = Path(tempfile.mkdtemp(prefix="capfonts_"))
    seen = set()
    for c in captions:
        st = _merged_style(c, presets)
        fid = st.get("font_id", "")
        if not fid or fid in seen:
            continue
        seen.add(fid)
        src = Path(resolve_font(fid, fonts_manifest)["path"])
        dst = d / src.name
        if not dst.exists():
            dst.write_bytes(src.read_bytes())   # libass 连软链都不认(实测),只能实拷
    return d


def font_effective(font_id: str, fonts_manifest: dict) -> bool:
    """实效性检查:该字体经 fontsdir 渲染出的画面 ≠ 无 fontsdir 的回落画面。

    libass 找不到 family 时**静默回落**系统字体,不报错、规格机检照样全绿
    (2026-08-10 前科:fontsdir 不递归子目录,标题烧成了回落字体没人发现)。
    唯一可靠的验证就是渲一帧对比。系统已装字体两者可能相同,故只对 user: 字体有意义。
    """
    require_tools("ffmpeg")
    ent = resolve_font(font_id, fonts_manifest)
    flat = Path(tempfile.mkdtemp(prefix="capfx_"))
    (flat / Path(ent["path"]).name).write_bytes(Path(ent["path"]).read_bytes())
    empty = Path(tempfile.mkdtemp(prefix="capfx0_"))
    ass = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 180\n"
           f"[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
           f"SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
           f"StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
           f"Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
           f"Style: T,{ent['family']},72,&H00FFFFFF,&H000000FF,&H00000000,"
           f"&H64000000,-1,0,0,0,100,100,0,0,1,2,0,5,10,10,10,1\n"
           f"[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
           f"MarginV, Effect, Text\n"
           f"Dialogue: 0,0:00:00.00,0:00:01.00,T,,0,0,0,,永字八法测试\n")
    with tempfile.NamedTemporaryFile("w", suffix=".ass", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(ass)
        ass_path = tf.name
    frames = []
    try:
        for fd in (flat, empty):
            out = Path(tempfile.mktemp(suffix=".png"))
            p = subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi",
                                "-i", "color=c=gray:s=320x180:d=1",
                                "-vf", f"subtitles=filename='{_filter_escape(ass_path)}'"
                                       f":fontsdir='{_filter_escape(str(fd))}'",
                                "-frames:v", "1", str(out)],
                               capture_output=True, text=True, timeout=120)
            if p.returncode != 0 or not out.is_file():
                raise RuntimeError(f"font_effective 采样渲染失败:{(p.stderr or '')[-200:]}")
            frames.append(out.read_bytes())
            out.unlink()
    finally:
        os.unlink(ass_path)
    return frames[0] != frames[1]


def render_group(clip_path: Path, out_path: Path, captions: list[dict],
                 presets: dict, fonts_manifest: dict, fonts_dir: Path,
                 force: bool = False, cards: list[dict] | None = None,
                 proj_root: Path | None = None) -> dict:
    """单组烧录:clip + 该组花字(+素材图卡)→ 副本。幂等(回执指纹一致即 SKIP)。

    fonts_dir 形参保留作兼容,实际 fontsdir 由 _flat_fontsdir 按本组用到的
    字体动态拍平生成(libass fontsdir 不递归,直接传 data/fonts 会漏子目录)。
    cards(素材图卡,2026-08-10):只用项目内图片(concepts/keyframes),
    纸质白框 + 淡入淡出 + 轻微上浮,叠在画面上、花字之下;零外部素材零版权风险。
    """
    require_tools("ffmpeg", "ffprobe")
    clip_path, out_path = Path(clip_path), Path(out_path)
    cards = cards or []
    receipt_path = out_path.with_suffix(".render.json")
    src_sha = file_sha256(str(clip_path))
    cap_hash = canonical_sha256({"captions": captions, "presets": presets})
    f_hash = _fonts_hash(captions, presets, fonts_manifest)
    cards_hash = canonical_sha256({
        "cards": cards,
        "images": {c["image"]: file_sha256(str(proj_root / c["image"]))
                   for c in cards if proj_root and (proj_root / c["image"]).is_file()}})
    if not force and receipt_path.is_file() and out_path.is_file():
        try:
            old = json.loads(receipt_path.read_text(encoding="utf-8"))
            if (old.get("src_sha256"), old.get("captions_hash"),
                    old.get("fonts_hash"), old.get("cards_hash"),
                    old.get("renderer")) == \
                    (src_sha, cap_hash, f_hash, cards_hash, RENDERER_VERSION):
                return {"status": "skipped", "out": str(out_path)}
        except (json.JSONDecodeError, OSError):
            pass
    info = probe_video_info(str(clip_path))
    W, H = info["width"], info["height"]
    ass_text = build_group_ass(captions, presets, fonts_manifest, W, H)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fonts_dir = _flat_fontsdir(captions, presets, fonts_manifest)
    with tempfile.NamedTemporaryFile("w", suffix=".ass", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(ass_text)
        ass_path = tf.name
    try:
        sub_f = (f"subtitles=filename='{_filter_escape(ass_path)}'"
                 f":fontsdir='{_filter_escape(str(fonts_dir))}'")
        audio = ["-c:a", "copy"] if info["has_audio"] else ["-an"]   # av 组 clip 保持无声
        if not cards:
            cmd = [ffmpeg_bin(), "-v", "error", "-y", "-i", str(clip_path),
                   "-vf", sub_f, *X264_ARGS, *audio, str(out_path)]
        else:
            inputs = ["-i", str(clip_path)]
            chains, prev = [], "0:v"
            for i, cd in enumerate(cards):
                img = proj_root / cd["image"]
                if not img.is_file():
                    raise RuntimeError(f"图卡图片不存在:{img}")
                inputs += ["-loop", "1", "-t", f"{info['duration_s']:.3f}",
                           "-i", str(img)]
                ch = max(64, int(H * float(cd.get("size_pct", 34)) / 100))
                b = max(3, int(H * float(cd.get("frame_pct", 1.1)) / 100))
                ls, le = float(cd["local_start"]), float(cd["local_end"])
                pos = _POSITION_ALIASES.get(cd.get("position", "mid_left"),
                                            cd.get("position", "mid_left"))
                fx, fy, _ = POSITIONS.get(pos, POSITIONS["mid_left"])
                cx, cy = W * fx, H * fy
                # 贴边锚位往画面里收一点,免得白框贴死边缘
                cx = min(max(cx, W * 0.17), W * 0.83)
                cy = min(max(cy, H * 0.24), H * 0.76)
                chains.append(
                    f"[{i + 1}:v]scale=-2:{ch},pad=iw+{2 * b}:ih+{2 * b}:{b}:{b}:"
                    f"color=0xF8F3E7,setsar=1,format=rgba,"
                    f"fade=t=in:st={ls:.3f}:d=0.28:alpha=1,"
                    f"fade=t=out:st={max(ls, le - 0.25):.3f}:d=0.25:alpha=1[cd{i}]")
                # 卡片静止,只有淡入淡出(正弦"漂浮"实测是上下抖动,2026-08-11 用户反馈砍掉)
                chains.append(
                    f"[{prev}][cd{i}]overlay="
                    f"x='{cx:.0f}-overlay_w/2':y='{cy:.0f}-overlay_h/2':"
                    f"enable='between(t,{ls:.3f},{le:.3f})'[v{i}]")
                prev = f"v{i}"
            chains.append(f"[{prev}]{sub_f}[outv]")
            cmd = [ffmpeg_bin(), "-v", "error", "-y", *inputs,
                   "-filter_complex", ";".join(chains), "-map", "[outv]",
                   *([] if not info["has_audio"] else ["-map", "0:a"]),
                   *X264_ARGS, *audio, str(out_path)]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if p.returncode != 0 or not out_path.is_file():
            raise RuntimeError(f"烧录失败 {clip_path.name}: {(p.stderr or '')[-400:]}")
    finally:
        os.unlink(ass_path)
    out_info = probe_video_info(str(out_path))
    for k, tol in (("width", 0), ("height", 0)):
        if abs(out_info[k] - info[k]) > tol:
            raise RuntimeError(f"烧录后 {k} 变化:{info[k]} → {out_info[k]}")
    if abs(out_info["duration_s"] - info["duration_s"]) > 1.0 / max(1, info["fps"]) + 0.001:
        raise RuntimeError(f"烧录后时长漂移:{info['duration_s']} → {out_info['duration_s']}")
    receipt = {"schema": "caption.render.v1", "renderer": RENDERER_VERSION,
               "src": str(clip_path),
               "src_sha256": src_sha, "captions_hash": cap_hash,
               "fonts_hash": f_hash, "cards_hash": cards_hash,
               "cards_count": len(cards), "captions_count": len(captions),
               "out_sha256": file_sha256(str(out_path)),
               "encoder": " ".join(X264_ARGS)}
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    return {"status": "rendered", "out": str(out_path)}


# ---------------------------------------------------------------- 远端素材库同步

_ASSETS_CONFIG = Path(__file__).resolve().parent / "caption_assets.json"


def assets_repo() -> tuple[str, str]:
    """花字素材库远端仓库 (repo, branch)。repo 为空 = 未配置(仅用本地素材)。

    优先级:$VIDEOAGENTS_CAPTION_ASSETS_REPO(owner/name[@branch])>
    modules/caption_assets.json。仿 TimbreModel 音色库的远端目录模式:
    素材二进制不进代码仓库,由用户在独立 GitHub 仓库动态维护。
    """
    env = os.environ.get("VIDEOAGENTS_CAPTION_ASSETS_REPO", "").strip()
    if env:
        repo, _, branch = env.partition("@")
        return repo.strip(), (branch.strip() or "main")
    try:
        cfg = json.loads(_ASSETS_CONFIG.read_text(encoding="utf-8"))
        return (cfg.get("repo") or "").strip(), (cfg.get("branch") or "main").strip()
    except (OSError, json.JSONDecodeError):
        return "", "main"


def _http_get(url: str, timeout: int = 120) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "videoagents-captions"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _sync_via_git(repo: str, branch: str, fonts_dir: Path, sfx_dir: Path) -> dict:
    """assets 同步的 git 回退路径:浅克隆后把 fonts/ sfx/ 整体镜像进 remote/ 缓存。

    remote/ 内容 = 仓库内容的完整镜像(先清后拷,天然实现"远端删本地删");
    走系统 git(凭证由 credential helper 提供),私有仓库也能同步。
    """
    tmp = Path(tempfile.mkdtemp(prefix="capassets_"))
    p = subprocess.run(["git", "clone", "--depth", "1", "--branch", branch,
                        f"https://github.com/{repo}.git", str(tmp / "r")],
                       capture_output=True, text=True, timeout=1800)
    if p.returncode != 0:
        raise RuntimeError(f"素材仓库克隆失败 {repo}@{branch}:{(p.stderr or '')[-300:]}")
    import shutil as _shutil
    stats = {"downloaded": 0, "removed": 0, "kept": 0}
    for top, local_root in (("fonts", Path(fonts_dir) / "remote"),
                            ("sfx", Path(sfx_dir) / "remote")):
        src = tmp / "r" / top
        if not src.is_dir():
            continue
        if local_root.exists():
            _shutil.rmtree(local_root)
        _shutil.copytree(src, local_root)
        stats["downloaded"] += sum(1 for f in local_root.rglob("*") if f.is_file())
    _shutil.rmtree(tmp, ignore_errors=True)
    return {"repo": repo, "branch": branch, "via": "git-clone", **stats}


def sync_assets(fonts_dir: Path, sfx_dir: Path) -> dict:
    """从远端仓库同步 fonts/ 与 sfx/ 到本地缓存目录(remote/ 子目录)。

    - 远端是唯一事实源(用户动态维护):remote/ 子目录内**新增下载、删除移除**;
      用户手放在 data/fonts|sfx 其他子目录的素材不受影响,两路并存;
    - 下载 .part 原子落盘;已存在且字节数一致的跳过(trees API 带 blob size);
    - 仓库未配置时返回 {"skipped": True},调用方继续用本地素材,不报错。
    """
    repo, branch = assets_repo()
    if not repo:
        return {"skipped": True, "reason": "caption_assets.json 未配置 repo"}
    tree_url = f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
    try:
        tree = json.loads(_http_get(tree_url).decode("utf-8"))
    except Exception:
        # API 匿名限流 60 次/小时(团队共用出口 IP 极易撞)或私有仓库:
        # 回退 git 浅克隆(走本机 git 凭证),同步语义与 API 路径一致
        return _sync_via_git(repo, branch, fonts_dir, sfx_dir)
    if tree.get("truncated"):
        raise RuntimeError(f"仓库 {repo} 文件数超出 trees API 上限,需改用分页遍历")
    wanted = {"fonts": {}, "sfx": {}}
    for node in tree.get("tree", []):
        if node.get("type") != "blob":
            continue
        path = node.get("path", "")
        top, _, rest = path.partition("/")
        if top in wanted and rest and not rest.endswith((".md", ".txt", ".json")):
            wanted[top][rest] = int(node.get("size") or 0)
        elif top in wanted and rest:                       # 许可证/说明也带下来
            wanted[top][rest] = int(node.get("size") or 0)
    stats = {"downloaded": 0, "removed": 0, "kept": 0}
    for top, local_root in (("fonts", Path(fonts_dir) / "remote"),
                            ("sfx", Path(sfx_dir) / "remote")):
        local_root.mkdir(parents=True, exist_ok=True)
        for rest, size in wanted[top].items():
            dst = local_root / rest
            if dst.is_file() and dst.stat().st_size == size:
                stats["kept"] += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            raw = (f"https://raw.githubusercontent.com/{repo}/{branch}/"
                   f"{top}/{rest}")
            part = dst.with_suffix(dst.suffix + ".part")
            part.write_bytes(_http_get(raw))
            part.replace(dst)                               # 原子落盘
            stats["downloaded"] += 1
        # 远端已删除的本地同步移除(只清 remote/ 缓存,不碰用户手放目录)
        for p in sorted(local_root.rglob("*")):
            if p.is_file() and str(p.relative_to(local_root)) not in wanted[top] \
                    and not p.name.endswith(".part"):
                p.unlink()
                stats["removed"] += 1
    return {"repo": repo, "branch": branch, **stats}


# ---------------------------------------------------------------- SFX 轨 + 预混封装

def build_sfx_track(data: dict, sfx_manifest: dict, sfx_dir: Path,
                    duration_s: float, out_path: Path) -> dict:
    """captions.json → 集级 SFX 轨(aac 48k,时长恒 == 成片)。

    多条 sfx 重叠:amix normalize=0 自然叠加(不整体压低)+ alimiter -1dBTP 防爆音。
    """
    require_tools("ffmpeg")
    uses = []                                    # 图卡出场音效与花字同轨(集级 start)
    for c in list(data.get("captions", [])) + list(data.get("cards", [])):
        sfx = c.get("sfx")
        if not sfx:
            continue
        # sfx 支持单条 dict 或 list(2026-08-11:大标题 whoosh 前导+落点重音双层)
        for one in (sfx if isinstance(sfx, list) else [sfx]):
            uses.append((c, one))
    if not uses:
        raise RuntimeError("captions.json 无任何 sfx 引用,不产 SFX 轨"
                           "(caption_sfx_track 是条件产物,调用方先判空)")
    inputs, filters, mix_in = [], [], ["[base]"]
    for i, (cap, sfx) in enumerate(uses):
        p = resolve_sfx(sfx["sfx_id"], sfx_manifest, sfx_dir)
        t_ms = max(0, int(round((float(cap.get("start", 0))
                                 + float(sfx.get("offset_s", 0))) * 1000)))
        gain = float(sfx.get("gain_db", next(
            (s["gain_db_default"] for s in sfx_manifest["sfx"] if s["id"] == sfx["sfx_id"]),
            DEFAULT_SFX_GAIN_DB)))
        # 音高微变(±10% 内):同一条素材逐次听感不重复,治"音效单调"的关键
        pitch = max(0.7, min(1.4, float(sfx.get("pitch", 1.0))))
        pitch_f = (f"asetrate={int(SFX_SR * pitch)},aresample={SFX_SR},"
                   if abs(pitch - 1.0) > 0.005 else "")
        inputs += ["-i", str(p)]
        # 注意:sfx 文件是 ffmpeg 的第 0..n-1 号输入(本命令没有别的 -i),
        # anullsrc 底噪是 filter_complex 内的源滤镜,不占输入编号
        filters.append(f"[{i}:a]aresample={SFX_SR},aformat=channel_layouts=stereo,"
                       f"{pitch_f}volume={gain}dB,adelay={t_ms}|{t_ms}[s{i}]")
        mix_in.append(f"[s{i}]")
    filters.insert(0, f"anullsrc=r={SFX_SR}:cl=stereo,atrim=0:{duration_s:.3f}[base]")
    fc = (";".join(filters) + ";" + "".join(mix_in)
          + f"amix=inputs={len(mix_in)}:duration=first:normalize=0,"
          f"alimiter=limit=0.891,atrim=0:{duration_s:.3f}[out]")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg_bin(), "-v", "error", "-y", *inputs, "-filter_complex", fc,
           "-map", "[out]", "-c:a", "aac", "-b:a", SFX_BITRATE, str(out_path)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if p.returncode != 0:
        raise RuntimeError(f"SFX 轨合成失败:{(p.stderr or '')[-400:]}")
    return {"out": str(out_path), "sfx_count": len(uses),
            "duration_s": round(probe_duration(str(out_path)), 3)}


def mux_caption_final(video_path: Path, master_path: Path, sfx_track: Path,
                      out_path: Path) -> dict:
    """花字版成片封装:v=烧录后拼装视频(流拷贝),a:0=母带+SFX 预混(开箱即听),
    a:1=母带流拷贝(零重编码存档,机检 final_caption_master_frames_intact 对它查)。
    时长权威=母带(amix duration=first 以母带为第一路);严禁 -shortest。"""
    require_tools("ffmpeg")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fc = (f"[1:a]aresample={SFX_SR}[m];[2:a]aresample={SFX_SR}[s];"
          f"[m][s]amix=inputs=2:duration=first:normalize=0,"
          f"alimiter=limit=0.891[premix]")
    cmd = [ffmpeg_bin(), "-v", "error", "-y", "-i", str(video_path),
           "-i", str(master_path), "-i", str(sfx_track),
           "-filter_complex", fc,
           "-map", "0:v:0", "-map", "[premix]", "-map", "1:a:0",
           "-c:v", "copy", "-c:a:0", "aac", "-b:a:0", SFX_BITRATE,
           "-c:a:1", "copy",
           # disposition 必须显式定:-c:a:1 copy 会把母带文件自带的 default 标志
           # 原样拷进存档轨,QuickTime 等按 default 选轨的播放器会放到无音效的 a:1
           # 上(2026-08-08 前科:用户 QuickTime 听不到音效);预混轨才是默认播放轨
           "-disposition:a:0", "default", "-disposition:a:1", "0",
           "-metadata:s:a:0", "title=premix", "-metadata:s:a:1", "title=master",
           str(out_path)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if p.returncode != 0:
        raise RuntimeError(f"花字版封装失败:{(p.stderr or '')[-400:]}")
    return {"out": str(out_path), "duration_s": round(probe_duration(str(out_path)), 3)}
