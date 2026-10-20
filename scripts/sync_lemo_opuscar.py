#!/usr/bin/env python3
"""从 lemo-opuscar(MIT,github.com/lemomo-ai/lemo-opuscar)同步「演示视频」项目类型用到的上游文件。

用法:python3 scripts/sync_lemo_opuscar.py [--src <lemo-opuscar 克隆>] [--check]

同步内容(上游 commit 记进 modules/codevideo/lib/upstream.json):
- modules/codevideo/lib/core/        页面库 lib.js、three/post.js、post/crt.js、fonts/,音频 sampler.py / sfx.py / pluck.py
                                     (sampler.py 打两处补丁:采样库目录可由环境变量指定 + 缺 soxr 时退回 scipy)
- modules/codevideo/lib/vendor/three/   three.js r170:build/three.module.js + examples/jsm(去掉 draco 等大件);
                                     页面里仍写 /node_modules/three/build/three.module.js 与 /node_modules/three/examples/jsm/…,
                                     静态服务映射到 vendor/three/three.module.js 与 vendor/three/jsm/(仓库 .gitignore 忽略 node_modules/、build/)
- agents/workflows/codevideo/styles/<slug>/   43 个风格的 STYLE.md / DEMO.md / style.json(随包,Agent 必读)
- apps/web/static/styles/code/       新建向导风格画廊:code_styles.json + frames/<slug>.webp
- upstream.json 的 packs / files     按需下载清单:采样库与 HDRI 包(release assets,sha256)、各风格样片源码与海报、
                                     polyhaven 模型贴图(raw 文件,sha256)

样片源码(styles/*/demo/)与海报不随包,写完 TREATMENT.md 后由 `code/codevideo.py demo` 按清单下载。
--check 只比对不写盘(上游有更新时退出码 1)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = Path("/Users/ericfish/Downloads/333_Agentics/_archive/51-zhuguang/lemo-opuscar")
REPO = "lemomo-ai/lemo-opuscar"
LIB = ROOT / "modules" / "codevideo" / "lib"
STYLES_DST = ROOT / "agents" / "workflows" / "codevideo" / "styles"
GALLERY = ROOT / "apps" / "web" / "static" / "styles" / "code"

CORE_FILES = ["lib.js", "three/post.js", "post/crt.js", "fonts/fonts.css", "fonts/OFL.md",
              *[f"fonts/f{i}.woff2" for i in range(8)],
              "audio/sampler.py", "audio/sfx.py", "audio/pluck.py", "audio/INSTRUMENTS.md"]
# examples/jsm 里不带的大件(加载器的可选解码器、物理引擎等):演示片用不到,要用时 Agent 另行说明
JSM_SKIP_DIRS = {"libs/draco", "libs/basis", "libs/rhino3dm", "transpiler"}
JSM_SKIP_FILES = {"libs/ammo.wasm.js", "libs/ammo.wasm.wasm", "libs/lottie_canvas.module.js",
                  "libs/mmdparser.module.js", "libs/chevrotain.module.min.js", "libs/ecsy.module.js"}
FRAME_WIDTH = 640

SAMPLER_PATCHES = [
    # 采样库目录:VideoAgents 把按需下载的包放在 data/.videoagents/codevideo/lemo/core/audio/instruments
    ("ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instruments')",
     "ROOT = os.environ.get('CODEVIDEO_INSTRUMENTS') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instruments')"
     "  # VideoAgents patch: 采样库目录可指定"),
    ("import soxr\n",
     "try:  # VideoAgents patch: 没装 soxr 时退回 scipy 的多相重采样\n"
     "    import soxr\n"
     "except ImportError:\n"
     "    from scipy.signal import resample_poly as _rp\n"
     "    from fractions import Fraction as _Fr\n\n"
     "    class soxr:  # noqa: N801\n"
     "        @staticmethod\n"
     "        def resample(x, a, b, quality=None):\n"
     "            f = _Fr(int(round(b)), int(round(a))).limit_denominator(1000)\n"
     "            return _rp(x, f.numerator, f.denominator, axis=0)\n"),
    # 缺采样库:CODEVIDEO_AUTO_FETCH=1 时先调宿主下载(「生成模型」页代码作曲「缺采样库时自动下载」开关)
    ("        if not (os.path.isdir(os.path.join(ROOT, self.lib)) and os.path.exists(INDEX)):\n",
     "        if not (os.path.isdir(os.path.join(ROOT, self.lib)) and os.path.exists(INDEX)):\n"
     "            _auto_fetch(self.lib)  # VideoAgents patch\n"
     "        if not (os.path.isdir(os.path.join(ROOT, self.lib)) and os.path.exists(INDEX)):\n"),
    ("_IDX = None\n",
     "_IDX = None\n\n\n"
     "def _auto_fetch(lib):  # VideoAgents patch: 缺包时按开关调宿主下载,下载后重读索引\n"
     "    global _IDX\n"
     "    cli = os.environ.get('CODEVIDEO_FETCH_CLI')\n"
     "    if os.environ.get('CODEVIDEO_AUTO_FETCH') != '1' or not cli:\n"
     "        return\n"
     "    import subprocess, sys\n"
     "    subprocess.run([os.environ.get('CODEVIDEO_PYTHON') or sys.executable, cli, 'packs', '--fetch', 'instruments-' + lib],\n"
     "                   stdout=sys.stderr, check=False)\n"
     "    _IDX = None\n"),
]
SAMPLER_FETCH_HINT = "Run, from the library root: sh tools/fetch.sh instruments"


def git(src: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(src), *args], check=True, capture_output=True, text=True).stdout


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Writer:
    def __init__(self, check: bool):
        self.check, self.changed = check, []

    def bytes(self, dst: Path, data: bytes):
        if dst.is_file() and dst.read_bytes() == data:
            return
        self.changed.append(dst.relative_to(ROOT).as_posix())
        if not self.check:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)

    def copy(self, src: Path, dst: Path):
        self.bytes(dst, src.read_bytes())

    def text(self, dst: Path, s: str):
        self.bytes(dst, s.encode("utf-8"))


def patch_sampler(text: str) -> str:
    for old, new in SAMPLER_PATCHES:
        if old not in text:
            raise SystemExit(f"sampler.py 补丁点找不到(上游改了?):{old[:60]}")
        text = text.replace(old, new, 1)
    # 缺包提示:改为本仓库的下载命令(上游写法 "…instruments {self.lib}")
    if SAMPLER_FETCH_HINT not in text:
        raise SystemExit("sampler.py 缺包提示找不到(上游改了?)")
    text = text.replace(SAMPLER_FETCH_HINT + " {", "Run: python3 code/codevideo.py packs --fetch instruments-{")
    return text.replace("run again: sh tools/fetch.sh instruments {",
                        "run again: python3 code/codevideo.py packs --fetch instruments-{")


def tracked(src: Path, prefix: str) -> list[str]:
    return [l for l in git(src, "ls-files", prefix).splitlines() if l]


def file_rows(src: Path, paths: list[str]) -> list[dict]:
    return [{"path": p, "size": (src / p).stat().st_size, "sha256": sha256(src / p)} for p in paths]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    src = a.src.resolve()
    if not (src / "AGENTS.md").is_file() or not (src / "core" / "render").is_dir():
        raise SystemExit(f"不是 lemo-opuscar 克隆:{src}")
    rev = git(src, "rev-parse", "HEAD").strip()
    dirty = [l for l in git(src, "status", "--porcelain", "--", "core", "styles", "docs/frames", "styleboard/catalog.json",
                            "tools/assets.json", "LICENSE").splitlines() if l.strip()]
    if dirty:
        raise SystemExit("上游工作区有改动,不能按 commit 记录:\n" + "\n".join(dirty[:20]))
    w = Writer(a.check)

    # 1. 页面库与音频工具
    for rel in CORE_FILES:
        s = src / "core" / rel
        if rel == "audio/sampler.py":
            w.text(LIB / "core" / rel, patch_sampler(s.read_text(encoding="utf-8")))
        else:
            w.copy(s, LIB / "core" / rel)
    w.copy(src / "LICENSE", LIB / "LICENSE.lemo-opuscar")

    # 2. three.js r170
    three = src / "node_modules" / "three"
    ver = json.loads((three / "package.json").read_text())["version"]
    if not ver.startswith("0.170."):
        raise SystemExit(f"three 版本应为 0.170.x(core/three/post.js 按 r170 写),上游 node_modules 是 {ver}")
    w.copy(three / "LICENSE", LIB / "vendor" / "three" / "LICENSE")
    w.text(LIB / "vendor" / "three" / "package.json",
           json.dumps({"name": "three", "version": ver, "license": "MIT", "type": "module"}, indent=1) + "\n")
    w.copy(three / "build" / "three.module.js", LIB / "vendor" / "three" / "three.module.js")
    jsm = three / "examples" / "jsm"
    for f in sorted(jsm.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(jsm).as_posix()
        if rel in JSM_SKIP_FILES or any(rel.startswith(d + "/") for d in JSM_SKIP_DIRS):
            continue
        w.copy(f, LIB / "vendor" / "three" / "jsm" / rel)

    # 3. 风格库(随包)
    catalog = json.loads((src / "styleboard" / "catalog.json").read_text(encoding="utf-8"))
    slugs = [c["slug"] for c in catalog]
    for slug in slugs:
        for name in ("STYLE.md", "DEMO.md", "style.json"):
            w.copy(src / "styles" / slug / name, STYLES_DST / slug / name)
    w.copy(src / "styles" / "README.md", STYLES_DST / "README.md")
    w.copy(src / "LICENSE", STYLES_DST / "LICENSE.lemo-opuscar")

    # 4. 画廊数据
    from PIL import Image
    import io
    stamp_p = GALLERY / "frames" / "sources.json"     # 封面源 jpg 的 sha256:源图没变就不重编码(webp 编码不保证逐字节稳定)
    try:
        stamps = json.loads(stamp_p.read_text())
    except (OSError, ValueError):
        stamps = {}
    rows = []
    for c in catalog:
        rows.append({"slug": c["slug"], "num": c["num"], "en": c["en"], "cn": c["cn"],
                     "cat_en": c["cat_en"], "cat_cn": c["cat"], "film": c["film"],
                     "line": c["line"], "line_cn": c["line_cn"], "uses": c.get("uses") or [],
                     "dur": c.get("dur"), "frame": f"frames/{c['slug']}.webp",
                     "dim": "3d" if re.search(r"3D|three", (src / "styles" / c["slug"] / "STYLE.md").read_text(encoding="utf-8")[:3000]) else "2d",
                     "gallery": f"https://lemomo-ai.github.io/lemo-opuscar/#{c['slug']}"})
        jpg = src / "docs" / "frames" / f"{c['slug']}.jpg"
        h = sha256(jpg)
        webp = GALLERY / "frames" / f"{c['slug']}.webp"
        if stamps.get(c["slug"]) != h or not webp.is_file():
            im = Image.open(jpg).convert("RGB")
            im = im.resize((FRAME_WIDTH, round(im.height * FRAME_WIDTH / im.width)), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=82, method=6)
            w.bytes(webp, buf.getvalue())
            stamps[c["slug"]] = h
    w.text(stamp_p, json.dumps(stamps, indent=1, sort_keys=True) + "\n")
    cats = []
    for r in rows:
        if r["cat_en"] not in [x["en"] for x in cats]:
            cats.append({"en": r["cat_en"], "cn": r["cat_cn"]})
    w.text(GALLERY / "code_styles.json", json.dumps(
        {"source": f"https://github.com/{REPO}", "rev": rev, "categories": cats, "styles": rows},
        ensure_ascii=False, indent=1) + "\n")

    # 5. 按需下载清单
    assets = json.loads((src / "tools" / "assets.json").read_text())
    files = {}
    for slug in slugs:
        demo = [p for p in tracked(src, f"styles/{slug}") if "/demo/" in p or p.endswith(("poster.jpg", ".srt"))]
        files[f"demo:{slug}"] = file_rows(src, demo)
    files["assets:polyhaven"] = file_rows(src, tracked(src, "core/assets/polyhaven"))
    upstream = {"repo": REPO, "rev": rev, "three": ver,
                "release": {"tag": assets["tag"], "packs": {k: v for k, v in assets["packs"].items() if k != "voice"}},
                "files": files}
    w.text(LIB / "upstream.json", json.dumps(upstream, ensure_ascii=False, indent=1) + "\n")

    label = "需要更新" if a.check else "已写入"
    print(f"lemo-opuscar {rev[:12]} · three {ver} · {len(slugs)} 个风格 · {label} {len(w.changed)} 个文件")
    for p in w.changed[:40]:
        print("  ", p)
    if len(w.changed) > 40:
        print(f"   … 另 {len(w.changed) - 40} 个")
    return 1 if (a.check and w.changed) else 0


if __name__ == "__main__":
    sys.exit(main())
