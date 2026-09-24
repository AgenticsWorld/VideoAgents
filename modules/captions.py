# -*- coding: utf-8 -*-
"""captions.py — 花字(caption)渲染共享原语(主流程 p9-caption* 与 av 插件 av2/av4-caption* 共用)。

职责边界(WORKFLOW.md「花字与花字音效」节):
  - 设计(captions.json schema v3)由 10-editing/caption Agent 产出;本模块只负责
    **确定性执行**:schema 校验、字体/音效 manifest 扫描、SFX 轨合成、预混封装。
    花字渲染在 captions_html.py(HTML 引擎;libass 路径 2026-08-17 随 ep01 验收退役)。
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

SCHEMA_VERSION_HTML = 3                       # HTML 引擎(captions_html.py);
                                              # v2/libass 已退役(2026-08-17 ep01 验收)
X264_ARGS = ["-c:v", "libx264", "-crf", "18", "-preset", "medium",
             "-pix_fmt", "yuv420p"]          # 逐组统一,花字版拼装才能走 concat 流拷贝
SFX_SR = 48000
SFX_BITRATE = "192k"
DEFAULT_SFX_GAIN_DB = -6.0                    # 基准:弱于母带人声

# 花字用途类型:唯一源 modules/caption_catalog.json(2026-09-24 起;后期处理页「花字」弹窗、
# 机检 caption_types_allowed、派单注入共用)。历史 7 值(headline/keyword/location/time/skill/faction/other)仍在目录内。
try:
    from caption_catalog import type_ids as _catalog_type_ids, tier_of as _tier_of, \
        tier_em_range as _tier_em_range, load_catalog as _load_catalog
except ImportError:                                   # 宿主内以 modules.* 方式导入时
    from modules.caption_catalog import type_ids as _catalog_type_ids, tier_of as _tier_of, \
        tier_em_range as _tier_em_range, load_catalog as _load_catalog
CAPTION_TYPES = _catalog_type_ids()

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
    """选 ffmpeg:优先完整版 ffmpeg-full(keg-only,不进 PATH)。

    homebrew-core 自 2026 起把 ffmpeg 拆成精简版与完整版;HTML 引擎需要
    prores_ks/overlay/alphaextract,统一钉在同一个二进制上保证编码一致性。
    解析顺序:$VIDEOAGENTS_FFMPEG > ffmpeg-full keg > PATH 里的 ffmpeg。
    (沿用 subtitles 滤镜作"完整版"探针,与 libass 无关。)
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


_FONT_ID_PREFIX = {"system": "sys", "user": "user", "project": "proj"}
PROJECT_FONTS_SUBDIR = "refs/fonts"     # 项目字体:Web「参考文件」页「字体」板块上传目录


def _scan_font_dir(root: Path, source: str, seen: set) -> list[dict]:
    """扫一个目录下的全部字体文件 → manifest 记录列表。

    id 规则:sys:/user:/proj: 前缀 + family(去空格),非常规字重追加 -subfamily;
    同名冲突追加 #index,再冲突即跳过。seen 跨目录共享,先扫的目录先占 id。
    """
    out = []
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in _FONT_EXTS or not p.is_file():
            continue
        for rec in _font_records(p, source):
            if rec["family"].startswith("."):
                continue          # 系统隐藏字体(.Hiragino*/.LastResort)不入库
            fid = f"{_FONT_ID_PREFIX[source]}:{rec['family'].replace(' ', '')}"
            if rec["subfamily"] and rec["subfamily"].lower() not in ("regular", "normal"):
                fid += f"-{rec['subfamily'].replace(' ', '')}"
            if fid in seen:
                fid += f"#{rec['index']}"
            if fid in seen:
                continue
            seen.add(fid)
            out.append({"id": fid, "family": rec["family"],
                        "subfamily": rec["subfamily"], "path": str(p),
                        "index": rec["index"], "cjk": rec["cjk"], "source": source})
    return out


def scan_fonts(user_dir: Path, out_path: Path | None = None) -> dict:
    """扫系统字体目录 + 用户外置目录(data/fonts,gitignored),生成 manifest。

    id 规则:sys:/user: 前缀 + family(去空格);同名冲突追加 #index。
    user 目录的字体在渲染时通过 subtitles=...:fontsdir= 生效,无需安装。
    项目字体(refs/fonts/)不写进这份全局 manifest,由 load_fonts_manifest 按项目并入。
    """
    fonts, seen = [], set()
    for root, source in [(Path(d).expanduser(), "system") for d in _SYSTEM_FONT_DIRS] \
            + [(Path(user_dir), "user")]:
        fonts += _scan_font_dir(root, source, seen)
    manifest = {"schema": "fonts.manifest.v1", "fonts": fonts}
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    return manifest


def scan_project_fonts(proj_root: Path) -> list[dict]:
    """扫项目 refs/fonts/(用户在「参考文件」页上传的字体)→ 记录列表,id 前缀 proj:。
    每次调用现扫(文件少、无需缓存),增删字体立即生效,不必重跑 fonts-scan。"""
    return _scan_font_dir(Path(proj_root) / PROJECT_FONTS_SUBDIR, "project", set())


def load_fonts_manifest(manifest_path: Path, proj_root: Path | None = None,
                        require: bool = False) -> dict:
    """读全局 fonts manifest 并把项目字体并入(项目字体排前,便于设计 Agent 优先看到)。

    require=True 且全局 manifest 缺失时抛 RuntimeError(渲染前置条件);
    否则缺失视为空清单(机检口径由调用方决定)。
    """
    manifest_path = Path(manifest_path)
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    elif require:
        raise RuntimeError(f"缺 {manifest_path};先跑 render_captions.py fonts-scan")
    else:
        manifest = {"schema": "fonts.manifest.v1", "fonts": []}
    if proj_root is not None:
        proj_fonts = scan_project_fonts(proj_root)
        if proj_fonts:
            ids = {f["id"] for f in proj_fonts}
            manifest = {**manifest,
                        "fonts": proj_fonts + [f for f in manifest.get("fonts", [])
                                               if f["id"] not in ids]}
    return manifest


def resolve_font(font_id: str, manifest: dict) -> dict:
    """font_id → manifest 记录。找不到即抛错——libass 找不到字体会**静默**回落
    默认字体(机检 font_resolved 的存在意义),必须在渲染前显式失败。"""
    for f in manifest.get("fonts", []):
        if f["id"] == font_id:
            return f
    raise RuntimeError(
        f"font_id {font_id!r} 不在 fonts manifest 中;先跑 render_captions.py fonts-scan,"
        "或把字体文件放进 data/fonts/ 后重扫(外置字体不进 git 仓库);"
        "项目字体请在 Web「参考文件」页「字体」板块上传(refs/fonts/,id 为 proj:<family>)")


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

    只接受 schema v3(HTML 引擎);v1/v2 从未渲染或已随 libass 退役
    (2026-08-17 ep01 验收),旧文件用 code/migrate_captions_v3.py 升级。
    beat_text 提供时执行 caption_text_from_source(av 项目无 dictionary 的防造词
    口径):花字文本去标点后的每个 2+ 字连续片段须能在母带原文中找到。
    """
    issues = []
    sv = data.get("schema_version")
    if sv != SCHEMA_VERSION_HTML:
        return [f"schema_version 必须为 {SCHEMA_VERSION_HTML}(HTML 引擎;v2 已随 "
                "libass 退役,用 code/migrate_captions_v3.py 迁移)"]
    # v3:样式=项目内 HTML 模版,动画按内容逐条创作
    # (2026-08-17 用户裁定:不要每条都套同几个模版;上限只防失控,不是目标)
    used_tpl = {c.get("template_ref") for c in data.get("captions", [])}
    if len(used_tpl) > 20:
        issues.append(f"全集引用 {len(used_tpl)} 个模版(>20),确认非失控生成")
    if data.get("cards"):
        issues.append("v3 暂不支持 cards 图卡(需求出现时再移植)")
        if data.get("cards"):
            issues.append("v3 暂不支持 cards 图卡(需求出现时再移植)")
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
        # 样式:模版引用 + em_pct + params(动画在模版内)
        ref = c.get("template_ref") or ""
        if not ref.startswith("template:"):
            issues.append(f"{tag}: template_ref 须为 template:<name>,得到 {ref!r}")
        elif proj_root is not None:
            import captions_html as chtml
            try:
                p = chtml.template_path(proj_root, ref)
                pi = chtml.protocol_issues(p.read_text(encoding="utf-8"))
                issues.extend(f"{tag}: 模版 {ref} {x}" for x in pi)
            except RuntimeError as e:
                issues.append(f"{tag}: {e}")
        # 字号档(tier):显式 tier > 类型 default_tier;em_pct 按档校验(目录 tiers.em_pct)
        if c.get("tier") is not None and c.get("tier") not in _load_catalog()["tiers"]:
            issues.append(f"{tag}: tier {c.get('tier')!r} 不在 {tuple(_load_catalog()['tiers'])}")
        tier = _tier_of(c)
        lo, hi = _tier_em_range(tier)
        em = c.get("em_pct")
        if not (isinstance(em, (int, float)) and lo <= em <= hi):
            issues.append(f"{tag}: em_pct {em!r} 不在 {tier} 档 [{lo:g}, {hi:g}]"
                          "(headline 建议 15-21,keyword 12-16,按成片实测标定)")
        if c.get("params") is not None and not isinstance(c["params"], dict):
            issues.append(f"{tag}: params 须为对象")
        pos = c.get("position") or "center"
        if _POSITION_ALIASES.get(pos, pos) not in POSITIONS:
            issues.append(f"{tag}: position {pos!r} 不在 {sorted(POSITIONS)}")
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
    return issues


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
