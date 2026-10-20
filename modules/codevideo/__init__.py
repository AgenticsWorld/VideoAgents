"""演示视频(项目类型 code-video,2026-10-20)的宿主工具链:语言模型写网页、无头浏览器逐帧截图、ffmpeg 出片。

管线与页面契约沿用 lemo-opuscar(MIT,github.com/lemomo-ai/lemo-opuscar;同步脚本 scripts/sync_lemo_opuscar.py):
页面暴露 window.DUR / window.render(t) / window.READY(必需)、window.EV(音效事件)、window.TEXTS(t)(阅读时长自检)。

- lib/            上游页面库与音频工具(core/…)、three.js r170(vendor/three/),upstream.json 记 commit 与按需下载清单
- serve.py        静态服务:/core/、/node_modules/three/ → 宿主库;/@film/ → 项目 codevideo/(渲染)或
                  /@film/<项目>/(代码预览页侧车,带 CSP 与预览桥)
- page.py         Playwright 打开页面、等 READY、页面报错即停
- render.py       静帧 / 联系表 / 按段渲染 / 关键镜故事板
- timeline.py     codevideo/timeline.json 校验与帧边界,导出 directing/ep01/{shot_list,storyboard}.json
- fetch.py        采样库 / HDRI 包与样片源码按需下载(固定 commit,sha256 校验)
- audio.py        作曲 / 音效 / 混音脚本在宿主库环境下运行;两遍 loudnorm 出成片
- checks.py       机检(contract / timeline / clips / final / delivery)

命令行入口 code/codevideo.py;契约细节见 agents/workflows/codevideo.md。
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = Path(__file__).resolve().parent
LIB_DIR = PKG / "lib"
VENDOR_THREE = LIB_DIR / "vendor" / "three"
STYLES_DIR = ROOT / "agents" / "workflows" / "codevideo" / "styles"
GALLERY_JSON = ROOT / "apps" / "web" / "static" / "styles" / "code" / "code_styles.json"
CLI = ROOT / "code" / "codevideo.py"
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
PROJECTS_DIR = DATA_DIR / "projects"
CACHE_DIR = DATA_DIR / ".videoagents" / "codevideo"
MIRROR = CACHE_DIR / "lemo"               # 按需下载的上游文件,保持上游仓库布局(core/audio/instruments/…、styles/<slug>/demo/…)
INSTRUMENTS_DIR = MIRROR / "core" / "audio" / "instruments"

NS = "codevideo"                          # 项目内过程件目录 = 一个 lemo 片子工程目录
EP = "ep01"                               # 一个项目 = 一部片
FPS_CHOICES = (24, 25, 30)
MAX_DUR_S = 600
SHORT_SIDE = {"480p": 480, "720p": 720, "1080p": 1080, "2k": 1440, "4k": 2160}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,47}$")


class CodeVideoError(RuntimeError):
    """可预期的失败(输入不合规、页面报错、ffmpeg 失败…):命令行打印原因并以 1 退出。"""


@lru_cache(maxsize=1)
def upstream() -> dict:
    return json.loads((LIB_DIR / "upstream.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def gallery() -> dict:
    return json.loads(GALLERY_JSON.read_text(encoding="utf-8"))


def style_slugs() -> list[str]:
    return [s["slug"] for s in gallery()["styles"]]


def style_row(slug: str) -> dict | None:
    return next((s for s in gallery()["styles"] if s["slug"] == slug), None)


def project_base(project: str) -> Path:
    name = str(project or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-]*", name):
        raise CodeVideoError(f"项目名不合法:{project!r}")
    base = PROJECTS_DIR / name
    if not base.is_dir():
        raise CodeVideoError(f"项目不存在:{base}")
    return base


def film_dir(base: Path) -> Path:
    return base / NS


def project_settings(base: Path) -> dict:
    try:
        return json.loads((base / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def project_block(base: Path) -> dict:
    blk = project_settings(base).get("project")
    return blk if isinstance(blk, dict) else {}


def render_size(settings: dict) -> tuple[int, int]:
    """渲染尺寸:短边按成片分辨率档(默认 1080p),长边按画幅,宽高取偶数。"""
    out = settings.get("output") if isinstance(settings.get("output"), dict) else {}
    short = SHORT_SIDE.get(str(out.get("final_resolution") or "1080p"), 1080)
    preset = str(out.get("aspect_preset") or "youtube")
    ratio = {"youtube": (16, 9), "douyin": (9, 16), "cinema": (2.35, 1)}.get(preset)
    if ratio is None:
        m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*", str(out.get("aspect_custom") or ""))
        ratio = (float(m.group(1)), float(m.group(2))) if m and float(m.group(2)) > 0 else (16, 9)
    w, h = ratio
    if w >= h:
        size = (short * w / h, short)
    else:
        size = (short, short * h / w)
    return tuple(int(round(v / 2)) * 2 for v in size)  # type: ignore[return-value]


def ffmpeg() -> str:
    try:
        from modules.captions import ffmpeg_bin
        return ffmpeg_bin()
    except Exception:  # noqa: BLE001 — 精简环境里 captions 依赖不全时退回 PATH
        import shutil
        return shutil.which("ffmpeg") or "ffmpeg"


def ffprobe() -> str:
    import shutil
    ff = Path(ffmpeg())
    sib = ff.with_name("ffprobe" + ff.suffix)
    return str(sib) if sib.is_file() else (shutil.which("ffprobe") or "ffprobe")
