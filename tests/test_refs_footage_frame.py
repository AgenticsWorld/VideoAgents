"""参考文件页「🎞 素材库」截图(2026-10-26):选素材库项目,播原片找画面,服务端用 ffmpeg 从原片截当前帧存进 refs/<板块>/。
帧对齐用一段「亮度 = 帧号 × 5」的合成视频验:页面 currentTime 落在某帧的显示区间里,截出来的就是那一帧。"""
import asyncio
import io
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "apps/web/static"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")


def _make_video(out: Path, size: str = "64x36", seconds: int = 2, fps: int = 25):
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c=black:s={size}:r={fps}:d={seconds},format=gray,geq=lum='N*5'",
                    "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", str(out)], check=True)


def _footage(lib, name: str, **src):
    d = lib.FOOTAGE_DIR / name
    (d / "source").mkdir(parents=True)
    (d / "project.json").write_text(json.dumps({"name": name, "title": "", "status": "ready", "source": src}),
                                    encoding="utf-8")
    return d


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    from services.runtime import core

    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    core.PROJECTS_DIR.mkdir()
    core.ensure_project("p")
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "zh")
    lib = core._footage_lib()
    monkeypatch.setattr(lib, "FOOTAGE_DIR", tmp_path / "footage")
    lib.FOOTAGE_DIR.mkdir()
    return core, lib, core.PROJECTS_DIR / "p" / "refs"


def _grab(core, **kw):
    return asyncio.run(core.api_refs_footage_frame({"project": "p", "footage": "fp1", "category": "scenes", **kw}))


def _luma(path: Path) -> float:
    from PIL import Image, ImageStat
    return ImageStat.Stat(Image.open(path).convert("L")).mean[0]


@needs_ffmpeg
def test_grabs_the_frame_on_screen_and_records_source(runtime):
    core, lib, refs = runtime
    d = _footage(lib, "fp1", file="source/source.mp4", fps=25.0, duration_s=2.0, width=64, height=36,
                 title="哪吒法宝", url="https://example.com/v")
    _make_video(d / "source" / "source.mp4")
    a = _grab(core, t=1.03)                      # 显示区间 [1.00, 1.04) → 第 25 帧
    b = _grab(core, t=1.04)                      # 正好落在第 26 帧开头
    assert a["path"] == "scenes/footage_fp1_f000025.jpg" and a["frame"] == 25 and not a["existed"]
    assert b["path"] == "scenes/footage_fp1_f000026.jpg"
    la, lb = _luma(refs / a["path"]), _luma(refs / b["path"])
    assert lb - la > 3                                                                 # 相邻两帧亮度差 5 级
    ref = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(d / "source/source.mp4"), "-vf", r"select=eq(n\,25)",
                          "-frames:v", "1", "-q:v", "2", "-f", "image2", "-c:v", "mjpeg", "pipe:1"], capture_output=True).stdout
    from PIL import Image, ImageStat
    assert abs(ImageStat.Stat(Image.open(io.BytesIO(ref)).convert("L")).mean[0] - la) < 1.5   # 就是第 25 帧
    assert _grab(core, t=0.0)["frame"] == 0

    again = _grab(core, t=1.035)                 # 同一帧同一板块不重复存
    assert again["existed"] and again["path"] == a["path"]
    other = _grab(core, t=1.03, category="characters")
    assert other["path"] == "characters/footage_fp1_f000025.jpg" and not other["existed"]

    ann = json.loads((refs / "annotations.json").read_text(encoding="utf-8"))[a["path"]]
    assert ann["note"] == "" and ann["footage"]["project"] == "fp1" and ann["footage"]["title"] == "哪吒法宝"
    assert ann["footage"]["t"] == 1.0 and ann["footage"]["frame"] == 25 and ann["footage"]["source"] == "https://example.com/v"
    files = {f["path"]: f for c in asyncio.run(core.api_refs_list("p"))["categories"] for f in c["files"]}
    assert files[a["path"]]["footage"]["frame"] == 25 and files[a["path"]]["source"] is None
    asyncio.run(core.api_refs_note({"project": "p", "path": a["path"], "note": "山门参考"}))
    assert f"- `{a['path']}`:山门参考(素材库截图:哪吒法宝 @ 00:01.000)" in (refs / "NOTES.md").read_text(encoding="utf-8")
    asyncio.run(core.api_refs_note({"project": "p", "path": a["path"], "note": ""}))
    assert json.loads((refs / "annotations.json").read_text(encoding="utf-8"))[a["path"]]["footage"]   # 清空注释留着来源
    assert not (refs / "CREDITS.md").exists()

    rows = {r["name"]: r for r in lib.list_projects()}
    assert rows["fp1"]["has_source"] and rows["fp1"]["thumb"] == "" and rows["fp1"]["source"]["width"] == 64


@needs_ffmpeg
def test_long_side_capped_and_validation(runtime):
    core, lib, refs = runtime
    d = _footage(lib, "fp1", file="source/source.mp4", fps=25.0, duration_s=1.0, width=3000, height=200)
    _make_video(d / "source" / "source.mp4", size="3000x200", seconds=1)
    from PIL import Image
    assert Image.open(refs / _grab(core, t=0.5)["path"]).size == (2560, 171)           # 长边 ≤2560,等比
    for kw, code in (({"category": "music"}, 400), ({"t": "abc"}, 400), ({"t": -1}, 400), ({"t": 9}, 400),
                     ({"footage": "nope", "t": 0}, 404)):
        with pytest.raises(core.ServiceError) as e:
            _grab(core, **{"t": 0.2, **kw})
        assert e.value.status_code == code, kw
    _footage(lib, "fp2", title="空项目")
    with pytest.raises(core.ServiceError) as e:
        _grab(core, footage="fp2", t=0)
    assert e.value.status_code == 400
    (d / "clips").mkdir()
    for n in ("fp1_clip_002.jpg", "fp1_clip_001.sheet.jpg", "fp1_clip_001.jpg"):
        (d / "clips" / n).write_bytes(b"x")
    rows = {r["name"]: r for r in lib.list_projects()}
    assert rows["fp1"]["thumb"] == "clips/fp1_clip_001.jpg" and not rows["fp2"]["has_source"]


def test_seek_alignment_math():
    from services.runtime.core import _footage_frame_seek as seek
    assert seek(1.03, 25) == (0.98, 25) and seek(1.04, 25) == (1.02, 26)
    assert seek(0.01, 25) == (0.0, 0) and seek(5.0, 0) == (5.0, -1)                     # 没有帧率:按 t 原样截


def test_page_component_and_dictionaries():
    page = (STATIC / "preview_refs.html").read_text(encoding="utf-8")
    assert "/static/footage-frame.js" in page
    assert page.index('class="gencat"') < page.index('class="footcat"') < page.index('class="stockcat"')   # 生成 / 素材库 / 图库
    comp = (STATIC / "footage-frame.js").read_text(encoding="utf-8")
    assert "/references/footage-frame" in comp and "/api/v1/footage/projects" in comp and "source/proxy.mp4" in comp
    keys = set(re.findall(r"T\('([^']+)'\)", comp)) | set(re.findall(r"F\('([^']+)'", comp)) | {"从素材库的视频里截一帧加入本板块"}
    for path in sorted((STATIC / "i18n").glob("??.js")):
        text = path.read_text(encoding="utf-8")
        missing = [k for k in keys if f"\n{json.dumps(k, ensure_ascii=False)}: " not in text]
        assert not missing, (path.name, missing)
