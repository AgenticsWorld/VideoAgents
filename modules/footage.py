#!/usr/bin/env python3
"""footage.py — 现成素材检索/下载/切片原语(mashup 混剪插件的宿主工具链;零第三方依赖)。

依赖外部命令:yt-dlp(检索与下载)、ffmpeg/ffprobe(探测/抽帧/切片)、node(yt-dlp 的
JS runtime,缺失时部分 YouTube 视频 403)。装机自检用 `doctor` 子命令,任何 Agent
使用本模块前先跑它——插件 plugin.json 的 requires 字段从不被运行时读取,
doctor 是唯一拦截点。

成本阶梯(设计约束,Agent 按此顺序花流量):
  search(纯元数据,零视频流量) → preview ≤360p(几 MB) → fetch 1080p 区间(几十 MB)。
  正片流量只许花在已经视觉确认过的候选上;严禁整片下载超过 --max-duration 的源。
  素材库(library)通道零流量三档:catalog/search 元数据 → 看库内 sheet/thumbnail
  (现成 jpg,零下载) → cut 直切库内源片(本地转码)。mashup 插件 v3 起只走此通道。

CLI(全部子命令输出 JSON 到 stdout,便于 Agent 解析;失败 exit 非 0):
  python3 modules/footage.py doctor
  python3 modules/footage.py search --query "..." [--provider youtube|pexels|pixabay|library]
      [--limit 8] [--min-duration 5] [--max-duration 1200]
      [--library <name> ...]   # provider=library 必填,可重复给多个库
  python3 modules/footage.py catalog --library <name> [--library <name2> ...] [--brief]
  python3 modules/footage.py still --input <jpg|视频> [--at 秒] --out <mp4>
      --frames N [--fps 24] [--width 1920] [--height 1080]
      [--zoom in|out|none] [--pan left|right|none]
  python3 modules/footage.py preview --url <URL> --out <mp4> [--max-height 360]
      [--section 60-120]
  python3 modules/footage.py montage --input <video> --out <jpg> [--tiles 4x3]
  python3 modules/footage.py frame --input <video> --at <秒> --out <jpg>
  python3 modules/footage.py probe --input <video>
  python3 modules/footage.py fetch --url <URL> --out <mp4> [--section 128-142]
      [--pad 3] [--max-height 1080]
  python3 modules/footage.py cut --input <video> --out <mp4> --in-point 131.0
      --duration 4.2 [--fps 24] [--width 1920] [--height 1080] [--crop-x center]
  python3 modules/footage.py concat --list <concat.txt> --out <mp4>
  python3 modules/footage.py mux --video <mp4> --audio <母带> --out <mp4>
  python3 modules/footage.py ledger --sources mashup/sources.json --provider youtube
      --id <vid> [--file <path>] [--sha256 auto|<hex>] [--used-by-add grpNNN]
      [--mark unavailable]

Python:
  from modules.footage import search_youtube, search_pexels, search_pixabay, \
      probe_video, download_preview, download_source, extract_montage, cut_clip

要点:
  - search 结果统一 schema:{provider, id, url, title, duration_s, uploader,
    view_count, license, width, height};license 拿不到时为 "unknown"
    (版权责任由用户在 MH1 签字自担,本模块不做过滤,只如实登记)。
  - library 通道读 data/footage/<name>/clips.json(modules/footage_library.py 落盘):
    id 统一为 "<库名>:<clip_id>"(跨库不撞号),url 为库内 clip mp4 相对路径,
    另附 library/clip_id/start_s/end_s(源片绝对秒)/subtitle/info/thumbnail/sheet/
    source_file;license 恒 "user-provided(素材库自备素材)"。
  - still 产零公差静帧推拉片段(Ken Burns 兜底):帧数恒 == --frames,契约同 cut
    (setsar=1、-an、count_frames 尾检),可直接进 concat 拼片。
  - youtube 检索标题含 Videohive/Envato/Nimia/CinemaStock/Motion Array/
    Storyblocks/Artgrid/Shutterstock/Pond5 的候选大概率是带水印预览片,
    结果标 watermark_risk=true 供 scout 降权,但不删除(仍由 curator 看帧定夺)。
  - pexels/pixabay 需 API Key:环境变量 PEXELS_API_KEY / PIXABAY_API_KEY,
    或 data/.videoagents/footageconfig.json 的 pexels_api_key / pixabay_api_key。
  - fetch --section 自动前后各垫 --pad 秒(yt-dlp 区间下载切在关键帧,不垫会丢头尾);
    直链 URL(pexels/pixabay 的 .mp4)整文件下载,忽略 --section。
  - cut 输出零公差:帧数恒 == round(duration*fps),统一 scale+crop 到目标宽高、
    setsar=1、去音轨(-an);素材反正要转码归一,精确到帧是白捡的。
  - mux 封装铁律:-c:v copy -c:a copy,**严禁 -shortest**(会静默截断音频末帧
    且总长检查仍 PASS);要求视频时长 >= 音频,不满足直接报错。
  - ledger 是台账 sources.json 的**唯一合法写入口**(mx2 clip-cutter 全量并发,
    多进程读-改-写同一 JSON 必然互相覆盖丢更新):flock 独占锁 + 临时文件原子替换;
    只更新已存在条目(file/sha256/used_by 追加/状态标记),条目登记归 curator/merge。
机检入口见 code/check_footage.py。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_FPS = 24          # 与 modules/avsync.py 的 FPS 约定一致
DEFAULT_W, DEFAULT_H = 1920, 1080
PREVIEW_HEIGHT = 360
FETCH_HEIGHT = 1080
SECTION_PAD_S = 3.0       # 区间下载关键帧对齐余量
STOCK_WATERMARK_HINTS = ("videohive", "envato", "nimia", "cinemastock",
                         "motion array", "storyblocks", "artgrid",
                         "shutterstock", "pond5", "istock", "getty")
_CONFIG_PATH = Path(os.environ.get("VIDEOAGENTS_DATA_DIR",
                    Path(__file__).resolve().parents[1] / "data")) / ".videoagents" / "footageconfig.json"


class FootageError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def require_tools(*names: str) -> None:
    """缺工具即抛错,不做静默降级(同 avsync.require_tools 口径)。"""
    missing = [n for n in names if not tool_available(n)]
    if missing:
        raise FootageError(
            f"缺少外部命令:{', '.join(missing)}。yt-dlp: pipx install yt-dlp;"
            f" ffmpeg: brew install ffmpeg; node: brew install node")


_YTDLP_JS_FLAG: bool | None = None


def _ytdlp_base() -> list[str]:
    """yt-dlp 基础参数;2025.11+ 版本需 JS runtime 否则部分视频 403,自动探测一次。"""
    global _YTDLP_JS_FLAG
    if _YTDLP_JS_FLAG is None:
        r = _run(["yt-dlp", "--help"], timeout=60)
        _YTDLP_JS_FLAG = "--js-runtimes" in (r.stdout or "") and tool_available("node")
    # socket-timeout/retries:代理出口不稳时连接会无限 stall(2026-08-13 实测挂满
    # 外层 3600s 超时才被杀),30s 无数据即断开重试,快败快重试
    cmd = ["yt-dlp", "--no-playlist", "--no-warnings",
           "--socket-timeout", "30", "--retries", "3"]
    if _YTDLP_JS_FLAG:
        cmd += ["--js-runtimes", "node"]
    return cmd


def _config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _api_key(provider: str) -> str:
    env = {"pexels": "PEXELS_API_KEY", "pixabay": "PIXABAY_API_KEY"}[provider]
    key = os.environ.get(env) or str(_config().get(f"{provider}_api_key") or "")
    if not key:
        raise FootageError(
            f"{provider} 需要 API Key:设环境变量 {env} 或写入 {_CONFIG_PATH}"
            f" 的 {provider}_api_key 字段(免费申请:pexels.com/api / pixabay.com/api/docs)")
    return key


# ---------------- 检索(纯元数据,零视频流量) ----------------

def search_youtube(query: str, limit: int = 8,
                   min_duration: float = 5, max_duration: float = 1200,
                   **_: object) -> list[dict]:
    """yt-dlp ytsearch 元数据检索。时长过滤挡掉超短废片与合集/直播录像。"""
    require_tools("yt-dlp")
    r = _run(_ytdlp_base() + [f"ytsearch{max(limit * 2, limit + 4)}:{query}",
                              "--flat-playlist", "--print",
                              "%(id)s\t%(duration)s\t%(view_count)s\t%(channel)s\t%(license)s\t%(title)s"],
             timeout=180)
    if r.returncode != 0:
        raise FootageError(f"yt-dlp 检索失败:{(r.stderr or '').strip()[-300:]}")
    out = []
    for line in (r.stdout or "").strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        vid, dur, views, channel, lic, title = parts[0], parts[1], parts[2], parts[3], parts[4], "\t".join(parts[5:])
        try:
            dur_s = float(dur)
        except ValueError:
            continue
        if not (min_duration <= dur_s <= max_duration):
            continue
        low = title.lower()
        out.append({
            "provider": "youtube", "id": vid,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "title": title, "duration_s": dur_s, "uploader": channel,
            "view_count": int(views) if views.isdigit() else None,
            "license": lic if lic not in ("NA", "none", "") else "unknown",
            "width": None, "height": None,
            "watermark_risk": any(h in low for h in STOCK_WATERMARK_HINTS),
        })
        if len(out) >= limit:
            break
    return out


def _get_json(url: str, headers: dict | None = None, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def search_pexels(query: str, limit: int = 8, **_: object) -> list[dict]:
    """Pexels 视频检索(CC0 类许可,可免费商用);直链 mp4 可直接 fetch。"""
    data = _get_json(
        "https://api.pexels.com/videos/search?" + urllib.parse.urlencode(
            {"query": query, "per_page": limit}),
        headers={"Authorization": _api_key("pexels")})
    out = []
    for v in data.get("videos", []):
        files = sorted((f for f in v.get("video_files", []) if f.get("height")),
                       key=lambda f: f["height"], reverse=True)
        best = files[0] if files else {}
        out.append({
            "provider": "pexels", "id": str(v.get("id")), "url": v.get("url"),
            "title": (v.get("url") or "").rstrip("/").rsplit("/", 1)[-1].replace("-", " "),
            "duration_s": float(v.get("duration") or 0),
            "uploader": (v.get("user") or {}).get("name"),
            "view_count": None, "license": "Pexels License(free, no attribution required)",
            "width": best.get("width"), "height": best.get("height"),
            "file_url": best.get("link"), "watermark_risk": False,
        })
    return out


def search_pixabay(query: str, limit: int = 8, **_: object) -> list[dict]:
    """Pixabay 视频检索(Content License,可免费商用);直链 mp4 可直接 fetch。"""
    data = _get_json(
        "https://pixabay.com/api/videos/?" + urllib.parse.urlencode(
            {"key": _api_key("pixabay"), "q": query, "per_page": max(limit, 3)}))
    out = []
    for v in data.get("hits", [])[:limit]:
        files = v.get("videos") or {}
        best = files.get("large") or files.get("medium") or files.get("small") or {}
        out.append({
            "provider": "pixabay", "id": str(v.get("id")), "url": v.get("pageURL"),
            "title": ", ".join((v.get("tags") or "").split(",")[:4]),
            "duration_s": float(v.get("duration") or 0),
            "uploader": v.get("user"), "view_count": v.get("views"),
            "license": "Pixabay Content License(free, no attribution required)",
            "width": best.get("width"), "height": best.get("height"),
            "file_url": best.get("url"), "watermark_risk": False,
        })
    return out


def _footage_library():
    """惰性导入同仓 modules/footage_library.py(零第三方依赖,双导入路径兼容)。"""
    try:
        from modules import footage_library  # 作为包导入时
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import footage_library
    return footage_library


def _library_entry(lib: str, source: dict, clip: dict) -> dict:
    """clips.json 单条 → search 统一 schema(附库通道扩展字段)。"""
    fl = _footage_library()
    lib_dir = fl.FOOTAGE_DIR / lib
    info = str(clip.get("info") or "")
    info_head = info.splitlines()[0].strip() if info else ""
    title = clip.get("title") or clip.get("id", "")
    if info_head:
        title = f"{title} | {info_head[:60]}"

    def _p(rel: str | None) -> str | None:
        return str(lib_dir / rel) if rel else None

    return {
        "provider": "library", "id": f"{lib}:{clip.get('id')}",
        "url": _p(clip.get("file")), "title": title,
        "duration_s": float(clip.get("duration_s") or 0),
        "uploader": str(source.get("title") or lib),
        "view_count": None,
        "license": "user-provided(素材库自备素材)",
        "width": source.get("width"), "height": source.get("height"),
        "watermark_risk": False,
        "library": lib, "clip_id": clip.get("id"),
        "start_s": clip.get("start_s"), "end_s": clip.get("end_s"),
        "subtitle": clip.get("subtitle") or "", "info": info,
        "thumbnail": _p(clip.get("thumbnail")), "sheet": _p(clip.get("sheet")),
        "source_file": _p((source.get("file") or "") or None),
    }


def search_library(query: str, limit: int = 8,
                   min_duration: float = 0, max_duration: float = 1200,
                   libraries: list[str] | None = None, **_: object) -> list[dict]:
    """本地素材库检索:query 分词(空白/顿号/逗号)对 title+info+subtitle 计分,多库合并排序。

    零网络零流量;语义匹配(render 组「人的状态」类意图)召回有限,scout 应以
    catalog 一次通读为主、本命令为关键词辅助与 queries_run 登记载体。
    """
    if not libraries:
        raise FootageError("provider=library 需要 --library <name>(可重复给多个库)")
    fl = _footage_library()
    terms = [t for t in re.split(r"[\s、,,;;/]+", str(query or "").strip()) if t]
    if not terms:
        raise FootageError("检索词为空")
    scored: list[tuple[int, int, dict]] = []
    for lib in libraries:
        doc = fl.load_clips(fl.safe_name(lib))
        if not doc.get("clips"):
            raise FootageError(f"素材库为空或不存在:{lib}(data/footage/{lib}/clips.json)")
        source = doc.get("source") or {}
        for clip in doc["clips"]:
            dur = float(clip.get("duration_s") or 0)
            if not (min_duration <= dur <= max_duration):
                continue
            hay = " ".join((str(clip.get("title") or ""), str(clip.get("info") or ""),
                            str(clip.get("subtitle") or "")))
            score = sum(hay.count(t) for t in terms)
            if score > 0:
                scored.append((score, clip.get("index") or 0, _library_entry(lib, source, clip)))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [e for _, _, e in scored[:limit]]


def library_catalog(libraries: list[str], brief: bool = False) -> dict:
    """逐库全量清单(scout 的主工具:一次通读全部库做语义匹配,零流量)。

    brief=True 时省略 subtitle/info 全文,只留 info 首行与 subtitle 截断,
    适合库很大时先扫概貌。
    """
    if not libraries:
        raise FootageError("catalog 需要 --library <name>(可重复给多个库)")
    fl = _footage_library()
    out: dict = {"libraries": []}
    for lib in libraries:
        name = fl.safe_name(lib)
        doc = fl.load_clips(name)
        source = doc.get("source") or {}
        clips = []
        for clip in doc.get("clips") or []:
            e = _library_entry(lib, source, clip)
            if brief:
                e["subtitle"] = (e["subtitle"] or "").replace("\n", " ")[:60]
                e["info"] = (e["info"] or "").splitlines()[0][:80] if e["info"] else ""
            clips.append(e)
        out["libraries"].append({
            "name": name, "dir": str(fl.FOOTAGE_DIR / name),
            "source": {"title": source.get("title"),
                       "duration_s": source.get("duration_s"),
                       "width": source.get("width"), "height": source.get("height"),
                       "fps": source.get("fps"), "file": source.get("file")},
            "clip_count": len(clips), "clips": clips,
        })
    return out


SEARCH_PROVIDERS = {"youtube": search_youtube, "pexels": search_pexels,
                    "pixabay": search_pixabay, "library": search_library}


# ---------------- 探测与抽帧 ----------------

def probe_video(path: str) -> dict:
    """ffprobe 实测:duration_s/width/height/fps/nb_frames/has_audio/sar/pix_fmt/color_range。失败即抛错。"""
    require_tools("ffprobe")
    r = _run(["ffprobe", "-v", "quiet", "-show_entries",
              "format=duration:stream=codec_type,width,height,r_frame_rate,nb_frames,"
              "sample_aspect_ratio,pix_fmt,color_range",
              "-of", "json", str(path)], timeout=120)
    if r.returncode != 0 or not r.stdout:
        raise FootageError(f"ffprobe 失败:{path}")
    j = json.loads(r.stdout)
    info = {"duration_s": float(j["format"]["duration"]), "has_audio": False,
            "width": None, "height": None, "fps": None, "nb_frames": None, "sar": None,
            "pix_fmt": None, "color_range": None}
    for s in j.get("streams", []):
        if s.get("codec_type") == "audio":
            info["has_audio"] = True
        elif s.get("codec_type") == "video" and info["width"] is None:
            info["width"], info["height"] = s.get("width"), s.get("height")
            num, _, den = (s.get("r_frame_rate") or "0/1").partition("/")
            info["fps"] = round(float(num) / float(den or 1), 3) if float(den or 1) else None
            info["nb_frames"] = int(s["nb_frames"]) if str(s.get("nb_frames", "")).isdigit() else None
            info["sar"] = s.get("sample_aspect_ratio")
            info["pix_fmt"] = s.get("pix_fmt")
            info["color_range"] = s.get("color_range")
    return info


def count_frames(path: str) -> int:
    """逐帧精确计数(-count_frames,比 nb_frames 元数据可信;机检口径)。"""
    require_tools("ffprobe")
    r = _run(["ffprobe", "-v", "quiet", "-select_streams", "v:0", "-count_frames",
              "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
             timeout=300)
    out = (r.stdout or "").strip().split(",")[-1]
    if not out.isdigit():
        raise FootageError(f"帧计数失败:{path}")
    return int(out)


def extract_montage(src: str, out_jpg: str, tiles: str = "4x3", tile_w: int = 320) -> str:
    """全片均匀抽帧拼一张网格图(curator 看内容分布/查水印硬切的主要凭据)。"""
    require_tools("ffmpeg", "ffprobe")
    cols, rows = (int(x) for x in tiles.lower().split("x"))
    n = cols * rows
    dur = probe_video(src)["duration_s"]
    Path(out_jpg).parent.mkdir(parents=True, exist_ok=True)
    r = _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf",
              f"fps={n / dur},scale={tile_w}:-2,tile={cols}x{rows}",
              "-frames:v", "1", str(out_jpg)], timeout=300)
    if r.returncode != 0 or not Path(out_jpg).exists():
        raise FootageError(f"抽帧拼图失败:{(r.stderr or '').strip()[-200:]}")
    return out_jpg


def extract_frame(src: str, at_s: float, out_jpg: str, width: int = 960) -> str:
    """取单帧(curator 把选中画面存 keyframes 作组锚点图给预览页展示)。"""
    require_tools("ffmpeg")
    Path(out_jpg).parent.mkdir(parents=True, exist_ok=True)
    r = _run(["ffmpeg", "-y", "-v", "error", "-ss", str(at_s), "-i", str(src),
              "-vf", f"scale={width}:-2", "-frames:v", "1", str(out_jpg)], timeout=120)
    if r.returncode != 0 or not Path(out_jpg).exists():
        raise FootageError(f"取帧失败:{(r.stderr or '').strip()[-200:]}")
    return out_jpg


# ---------------- 下载 ----------------

def _parse_section(section: str) -> tuple[float, float]:
    m = re.fullmatch(r"([\d.]+)-([\d.]+)", section.strip().lstrip("*"))
    if not m or float(m.group(2)) <= float(m.group(1)):
        raise FootageError(f"--section 须为 <起>-<止> 秒(止>起),得到:{section}")
    return float(m.group(1)), float(m.group(2))


def _download_direct(url: str, out: Path, timeout: int = 600) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(out, "wb") as f:  # noqa: S310
        shutil.copyfileobj(resp, f)


def _ytdlp_download(url: str, out: Path, max_height: int,
                    section: str | None, pad: float) -> None:
    cmd = _ytdlp_base() + ["-S", f"res:{max_height}", "--force-overwrites",
                           "-o", str(out), url]
    if section:
        a, b = _parse_section(section)
        cmd += ["--download-sections", f"*{max(0.0, a - pad)}-{b + pad}"]
    r = _run(cmd, timeout=1200)
    if not out.exists():
        # yt-dlp 可能按实际容器改扩展名(.webm/.mkv),按 stem 找回并转封装到目标名
        got = sorted(out.parent.glob(out.stem + ".*"))
        got = [g for g in got if g.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov")]
        if got:
            r2 = _run(["ffmpeg", "-y", "-v", "error", "-i", str(got[0]),
                       "-c", "copy", str(out)], timeout=600)
            if r2.returncode == 0 and out.exists():
                got[0].unlink(missing_ok=True)
                return
            raise FootageError(f"下载产物转封装失败:{got[0].name}")
        raise FootageError(f"下载失败:{(r.stderr or '').strip()[-300:]}")


def download_preview(url: str, out: str, max_height: int = PREVIEW_HEIGHT,
                     section: str | None = None) -> dict:
    """低清预览下载(选片核验用,几 MB 级)。"""
    require_tools("yt-dlp", "ffmpeg")
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    _ytdlp_download(url, p, max_height, section, pad=SECTION_PAD_S)
    return {"path": str(p), **probe_video(str(p))}


def download_source(url: str, out: str, max_height: int = FETCH_HEIGHT,
                    section: str | None = None, pad: float = SECTION_PAD_S) -> dict:
    """正片下载。YouTube 传 --section 只下所需区间(前后垫 pad 秒);
    pexels/pixabay 的直链 mp4 整文件下载(本就是短素材)。"""
    require_tools("ffmpeg")
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    if url.lower().split("?")[0].endswith((".mp4", ".webm", ".mov")):
        _download_direct(url, p)
        if not p.exists() or p.stat().st_size == 0:
            raise FootageError(f"直链下载失败:{url}")
    else:
        require_tools("yt-dlp")
        _ytdlp_download(url, p, max_height, section, pad)
    info = probe_video(str(p))
    return {"path": str(p), "section": section, "pad_s": pad if section else 0, **info}


# ---------------- 切片与封装 ----------------

def frames_for_beat(t_in: float, t_out: float, fps: int = DEFAULT_FPS) -> int:
    """镜的帧数按**累计取整**:round(t_out*fps) - round(t_in*fps)。

    逐镜 round(dur*fps) 的舍入误差会累积(13 镜实测即多 1 帧),累计取整使
    全片帧数恒 == round(total*fps),零漂移在数学上成立。机检与切片同用此口径。
    """
    return round(t_out * fps) - round(t_in * fps)


def cut_clip(src: str, out: str, in_point: float, duration: float,
             fps: int = DEFAULT_FPS, width: int = DEFAULT_W, height: int = DEFAULT_H,
             crop_x: str = "center", frames: int | None = None) -> dict:
    """精剪归一化,一次转码完成四件事:精确入点、零公差帧数、规格归一、去音轨。

    帧数缺省 round(duration*fps);**多镜拼片必须显式传 frames**(用
    frames_for_beat 的累计取整口径,否则逐镜舍入累积破坏零漂移)。
    scale 短边铺满后 crop 到目标宽高(crop_x: center|left|right,
    预览时标了主体偏移用后两者);setsar=1;-an。
    """
    require_tools("ffmpeg", "ffprobe")
    frames = frames if frames is not None else round(duration * fps)
    if frames <= 0:
        raise FootageError(f"时长非法:{duration}")
    src_info = probe_video(src)
    if in_point + duration > src_info["duration_s"] + 0.05:
        raise FootageError(
            f"入点+时长({in_point}+{duration})超出素材长度 {src_info['duration_s']:.2f}s;"
            f"回退 curator 重定 rough_in/rough_out")
    xpos = {"center": "(iw-ow)/2", "left": "0", "right": "iw-ow"}[crop_x]
    # scale 显式 out_range=tv:归一 color range(-pix_fmt 只转像素格式不改 range 元数据;
    # full-range 源/jpg 静帧不归一会让 concat 后的成片流参数混杂——ffmpeg 8 的 xfade
    # 在参数切换处直接断流,播放器上还会黑位跳变。2026-09-03 leijun2 实测教训)
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase"
          f":in_range=auto:out_range=tv,"
          f"crop={width}:{height}:{xpos}:(ih-oh)/2,fps={fps},setsar=1")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    r = _run(["ffmpeg", "-y", "-v", "error", "-ss", str(in_point), "-i", str(src),
              "-vf", vf, "-frames:v", str(frames), "-c:v", "libx264", "-crf", "18",
              "-preset", "medium", "-pix_fmt", "yuv420p", "-color_range", "tv",
              "-an", str(out)],
             timeout=900)
    if r.returncode != 0:
        raise FootageError(f"切片失败:{(r.stderr or '').strip()[-300:]}")
    got = count_frames(out)
    if got != frames:
        raise FootageError(f"切片帧数不符:want {frames} got {got}(源素材尾部不足?)")
    return {"path": out, "frames": frames, "duration_s": round(frames / fps, 6),
            "width": width, "height": height, "fps": fps}


def still_clip(src: str, out: str, at: float | None = None, frames: int = 0,
               fps: int = DEFAULT_FPS, width: int = DEFAULT_W, height: int = DEFAULT_H,
               zoom: str = "in", pan: str = "none") -> dict:
    """静帧+Ken Burns 推拉片段(素材库兜底最后一档),契约同 cut_clip:零公差帧数、
    规格归一、setsar=1、无音轨、count_frames 尾检——可直接进 concat 拼片。

    src 是图片时直接用;是视频时须给 --at(秒)先抽该帧。zoom in|out|none,
    pan left|right|none(pan 需要缩放余量,zoom=none 时自动垫 1.12 倍)。
    """
    require_tools("ffmpeg", "ffprobe")
    if frames <= 0:
        raise FootageError("still 必须显式给 --frames(累计取整口径 frames_for_beat)")
    if zoom not in ("in", "out", "none") or pan not in ("left", "right", "none"):
        raise FootageError(f"非法 zoom/pan:{zoom}/{pan}")
    src_path = Path(src)
    if not src_path.exists():
        raise FootageError(f"输入不存在:{src}")
    is_video = src_path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".bmp")
    if is_video and at is None:
        raise FootageError("视频输入必须给 --at <秒> 指定取帧点")
    n = frames
    # zoompan 输入先放大 2 倍再取景,消除亚像素抖动;z/x 表达式按 on(输出帧序号)线性走
    zexpr = {"in": f"1+0.12*on/{n}", "out": f"1.12-0.12*on/{n}",
             "none": "1.12" if pan != "none" else "1.001"}[zoom]
    xexpr = {"none": "(iw-iw/zoom)/2",
             "right": f"(iw-iw/zoom)*on/{n}",
             "left": f"(iw-iw/zoom)*(1-on/{n})"}[pan]
    # in_range=auto:out_range=tv:归一 range(jpg 是 full-range;同 cut_clip 注释);
    # 视频源免 jpg 中转(v4.2:jpg 有损 + 2 倍上采样是静帧糊感来源之一)——-ss 直取
    # 该帧,trim=end_frame=1 只放一帧进 zoompan;上采样用 lanczos 保细节
    vf = (f"trim=end_frame=1," if is_video else "") + (
          f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase"
          f":in_range=auto:out_range=tv:flags=lanczos,"
          f"crop={width * 2}:{height * 2},"
          f"zoompan=z='{zexpr}':x='{xexpr}':y='(ih-ih/zoom)/2'"
          f":d={n}:s={width}x{height}:fps={fps},setsar=1")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    src_args = (["-ss", str(at), "-i", str(src_path)] if is_video
                else ["-loop", "1", "-i", str(src_path)])
    r = _run(["ffmpeg", "-y", "-v", "error", *src_args,
              "-vf", vf, "-frames:v", str(n), "-c:v", "libx264", "-crf", "18",
              "-preset", "medium", "-pix_fmt", "yuv420p", "-color_range", "tv",
              "-an", str(out)],
             timeout=900)
    if r.returncode != 0:
        raise FootageError(f"still 生成失败:{(r.stderr or '').strip()[-300:]}")
    got = count_frames(out)
    if got != n:
        raise FootageError(f"still 帧数不符:want {n} got {got}")
    return {"path": out, "frames": n, "duration_s": round(n / fps, 6),
            "width": width, "height": height, "fps": fps,
            "zoom": zoom, "pan": pan}


def concat_clips(list_file: str, out: str) -> dict:
    """concat demuxer 无损拼接(要求各 clip 由 cut_clip 产出:同编码同参数)。"""
    require_tools("ffmpeg")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    r = _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
              "-i", str(list_file), "-c", "copy", str(out)], timeout=600)
    if r.returncode != 0:
        raise FootageError(f"拼接失败:{(r.stderr or '').strip()[-300:]}")
    return {"path": out, **probe_video(out)}


def mux_master(video: str, audio: str, out: str) -> dict:
    """成片封装:视频流 + 母带音频流逐字节拷贝。**不加 -shortest**(铁律)。

    要求视频时长 >= 音频时长(timeline 数学上应恰等;短了说明切片缺帧,直接报错
    而不是让 -shortest 静默截音频)。多出的视频尾由零漂移时间轴保证不存在。
    """
    require_tools("ffmpeg", "ffprobe")
    vd, ad = probe_video(video)["duration_s"], probe_video(audio)["duration_s"]
    if vd + 0.05 < ad:
        raise FootageError(
            f"视频({vd:.3f}s)短于母带({ad:.3f}s),缺帧;回查 clips 数量与各组帧数,"
            f"严禁用 -shortest 掩盖")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    r = _run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(audio),
              "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "copy",
              str(out)], timeout=600)
    if r.returncode != 0:
        raise FootageError(f"封装失败:{(r.stderr or '').strip()[-300:]}")
    return {"path": out, **probe_video(out)}


# ---------------- 台账(并发安全) ----------------

def _file_sha256(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ledger_update(sources: str, provider: str, source_id: str,
                  file: str | None = None, sha256: str | None = None,
                  used_by_add: str | None = None, mark: str | None = None) -> dict:
    """并发安全地更新 sources.json 的单个源条目(flock 独占锁 + 原子替换写)。

    条目必须已存在(登记归 curator/merge,本原语只补录);sha256 传 "auto" 时
    对 --file 现算。返回更新后的条目。
    """
    import fcntl
    p = Path(sources)
    if not p.exists():
        raise FootageError(f"台账不存在:{p}(条目登记归 curator;merge 单是否已跑?)")
    if sha256 == "auto":
        if not file:
            raise FootageError('--sha256 auto 需要同时给 --file')
        sha256 = _file_sha256(file)
    lock_fd = os.open(str(p) + ".lock", os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        data = json.loads(p.read_text())
        hit = next((s for s in (data.get("sources") or [])
                    if s.get("provider") == provider
                    and str(s.get("id")) == str(source_id)), None)
        if hit is None:
            raise FootageError(
                f"台账无此源:{provider}:{source_id}(条目登记归 curator/merge,本命令只补录)")
        if file is not None:
            hit["file"] = file
        if sha256 is not None:
            hit["sha256"] = sha256
        if used_by_add and used_by_add not in (hit.get("used_by") or []):
            hit.setdefault("used_by", []).append(used_by_add)
        if mark is not None:
            hit["status"] = mark
        tmp = Path(str(p) + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, p)
        return hit
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


# ---------------- doctor ----------------

def doctor() -> dict:
    """装机自检:外部命令、yt-dlp 版本与 JS runtime、检索连通性(一次真实元数据检索)。"""
    rep: dict = {"ok": True, "checks": {}}

    def _c(name: str, ok: bool, detail: str = ""):
        rep["checks"][name] = {"ok": bool(ok), "detail": detail}
        rep["ok"] = rep["ok"] and bool(ok)

    for t in ("yt-dlp", "ffmpeg", "ffprobe"):
        _c(f"tool_{t}", tool_available(t), shutil.which(t) or "未安装")
    _c("tool_node", tool_available("node"),
       shutil.which("node") or "未装 node:部分 YouTube 视频将 403(yt-dlp JS runtime)")
    if tool_available("yt-dlp"):
        v = (_run(["yt-dlp", "--version"], timeout=60).stdout or "").strip()
        _c("ytdlp_version", bool(v), v)
        try:
            hits = search_youtube("nature b-roll", limit=1)
            _c("youtube_search", bool(hits), f"{len(hits)} 条结果")
        except Exception as e:  # noqa: BLE001
            _c("youtube_search", False, str(e)[-200:])
    for prov in ("pexels", "pixabay"):
        try:
            _api_key(prov)
            _c(f"{prov}_key", True, "已配置")
        except FootageError:
            rep["checks"][f"{prov}_key"] = {
                "ok": True, "detail": "未配置(可选;CC0 兜底通道不可用)"}
    # 素材库清单(信息项,不判 FAIL;mashup v3 只用 library 通道,yt-dlp 缺失不拦它)
    try:
        fl = _footage_library()
        libs = [f"{p.name}({len((json.loads((p / 'clips.json').read_text()) or {}).get('clips') or [])} clips)"
                for p in sorted(fl.FOOTAGE_DIR.iterdir())
                if (p / "clips.json").is_file()] if fl.FOOTAGE_DIR.is_dir() else []
        rep["checks"]["footage_libraries"] = {
            "ok": True, "detail": ", ".join(libs) or f"无素材库({fl.FOOTAGE_DIR})"}
    except Exception as e:  # noqa: BLE001
        rep["checks"]["footage_libraries"] = {"ok": True, "detail": f"读取失败:{str(e)[-120:]}"}
    return rep


# ---------------- CLI ----------------

def _emit(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    s = sub.add_parser("search")
    s.add_argument("--query", required=True)
    s.add_argument("--provider", default="youtube", choices=sorted(SEARCH_PROVIDERS))
    s.add_argument("--limit", type=int, default=8)
    s.add_argument("--min-duration", type=float, default=None,
                   help="缺省:网络通道 5(挡废片),library 0(库内短 clip 是常态)")
    s.add_argument("--max-duration", type=float, default=1200)
    s.add_argument("--library", action="append", default=None,
                   help="provider=library 必填,可重复给多个库(data/footage/<name>)")
    s = sub.add_parser("catalog")
    s.add_argument("--library", action="append", required=True,
                   help="素材库名,可重复给多个库")
    s.add_argument("--brief", action="store_true",
                   help="只留 info 首行与 subtitle 截断(大库先扫概貌)")
    s = sub.add_parser("still")
    s.add_argument("--input", required=True, help="图片,或视频(配 --at 取帧)")
    s.add_argument("--at", type=float, default=None, help="视频取帧点(秒)")
    s.add_argument("--out", required=True)
    s.add_argument("--frames", type=int, required=True,
                   help="显式帧数(累计取整口径 frames_for_beat)")
    s.add_argument("--fps", type=int, default=DEFAULT_FPS)
    s.add_argument("--width", type=int, default=DEFAULT_W)
    s.add_argument("--height", type=int, default=DEFAULT_H)
    s.add_argument("--zoom", default="in", choices=["in", "out", "none"])
    s.add_argument("--pan", default="none", choices=["left", "right", "none"])
    s = sub.add_parser("preview")
    s.add_argument("--url", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--max-height", type=int, default=PREVIEW_HEIGHT)
    s.add_argument("--section", default=None)
    s = sub.add_parser("montage")
    s.add_argument("--input", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--tiles", default="4x3")
    s = sub.add_parser("frame")
    s.add_argument("--input", required=True)
    s.add_argument("--at", type=float, required=True)
    s.add_argument("--out", required=True)
    s = sub.add_parser("probe")
    s.add_argument("--input", required=True)
    s = sub.add_parser("fetch")
    s.add_argument("--url", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--section", default=None)
    s.add_argument("--pad", type=float, default=SECTION_PAD_S)
    s.add_argument("--max-height", type=int, default=FETCH_HEIGHT)
    s = sub.add_parser("cut")
    s.add_argument("--input", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--in-point", type=float, required=True)
    s.add_argument("--duration", type=float, required=True)
    s.add_argument("--fps", type=int, default=DEFAULT_FPS)
    s.add_argument("--width", type=int, default=DEFAULT_W)
    s.add_argument("--height", type=int, default=DEFAULT_H)
    s.add_argument("--crop-x", default="center", choices=["center", "left", "right"])
    s.add_argument("--frames", type=int, default=None,
                   help="显式帧数(累计取整口径 frames_for_beat;多镜拼片必传)")
    s = sub.add_parser("concat")
    s.add_argument("--list", required=True, dest="list_file")
    s.add_argument("--out", required=True)
    s = sub.add_parser("mux")
    s.add_argument("--video", required=True)
    s.add_argument("--audio", required=True)
    s.add_argument("--out", required=True)
    s = sub.add_parser("ledger")
    s.add_argument("--sources", required=True)
    s.add_argument("--provider", required=True)
    s.add_argument("--id", required=True, dest="source_id")
    s.add_argument("--file", default=None)
    s.add_argument("--sha256", default=None, help='十六进制,或 "auto"(对 --file 现算)')
    s.add_argument("--used-by-add", default=None, dest="used_by_add")
    s.add_argument("--mark", default=None, help="源状态标记,如 unavailable")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "doctor":
            rep = doctor()
            _emit(rep)
            return 0 if rep["ok"] else 1
        if a.cmd == "search":
            min_dur = a.min_duration if a.min_duration is not None else (
                0 if a.provider == "library" else 5)
            _emit(SEARCH_PROVIDERS[a.provider](a.query, limit=a.limit,
                                               min_duration=min_dur,
                                               max_duration=a.max_duration,
                                               libraries=a.library))
        elif a.cmd == "catalog":
            _emit(library_catalog(a.library, a.brief))
        elif a.cmd == "still":
            _emit(still_clip(a.input, a.out, a.at, a.frames,
                             a.fps, a.width, a.height, a.zoom, a.pan))
        elif a.cmd == "preview":
            _emit(download_preview(a.url, a.out, a.max_height, a.section))
        elif a.cmd == "montage":
            _emit({"path": extract_montage(a.input, a.out, a.tiles)})
        elif a.cmd == "frame":
            _emit({"path": extract_frame(a.input, a.at, a.out)})
        elif a.cmd == "probe":
            _emit(probe_video(a.input))
        elif a.cmd == "fetch":
            _emit(download_source(a.url, a.out, a.max_height, a.section, a.pad))
        elif a.cmd == "cut":
            _emit(cut_clip(a.input, a.out, a.in_point, a.duration,
                           a.fps, a.width, a.height, a.crop_x, a.frames))
        elif a.cmd == "concat":
            _emit(concat_clips(a.list_file, a.out))
        elif a.cmd == "mux":
            _emit(mux_master(a.video, a.audio, a.out))
        elif a.cmd == "ledger":
            _emit(ledger_update(a.sources, a.provider, a.source_id,
                                a.file, a.sha256, a.used_by_add, a.mark))
        return 0
    except FootageError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
