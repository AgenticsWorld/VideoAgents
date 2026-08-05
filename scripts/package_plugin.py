#!/usr/bin/env python3
"""把声明式 Agent 插件打成可分发 zip(设置 → 高级 → 插件 → 安装插件包)。

用法:
  python3 scripts/package_plugin.py plugin-src/audio-to-video
  python3 scripts/package_plugin.py plugin-src/audio-to-video --out-dir dist

产出:
  dist/<name>-<version>.zip        插件包(套一层同名目录)
  dist/<name>-<version>.json       元数据(name/version/sha256/size/文件清单)

设计取舍(依据 services/runtime/core.py 的 api_plugins_upload 实测行为):
  - **套一层与插件同名的目录**(zip 内 `<name>/plugin.json`)。安装器两种布局都吃
    (取层级最浅的 plugin.json,其所在目录即插件根),但套层的好处是:手动解压即得一个
    可直接拷进 plugins/ 的目录,且 macOS 的 __MACOSX/.DS_Store 因不在 prefix 下被自动丢弃。
  - **程序化构建,不 shell 出 `zip`**,避免 __MACOSX、._ 资源叉、.DS_Store 混入。
  - **固定 date_time + 排序遍历** → 可复现构建:同样的输入必得同样的 sha256。
  - **拒绝可执行文件**:插件是纯声明式的(WORKFLOW.md §10.4),包内不许有 .py/.sh 等。
    这把「纯声明式」从口头纪律变成打包期机检——机检脚本随 VideoAgents 本体发布,不进包。
  - 不写执行位/符号链接/空目录:安装器一律 `dest.write_bytes()`,这些元数据本就会被丢弃。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 允许进包的文件(声明式产物 + 说明文档)
ALLOWED_SUFFIXES = {".json", ".yaml", ".yml", ".md"}
# 明确拒绝的可执行/脚本类扩展名(命中即报错退出,不是静默跳过)
DENIED_SUFFIXES = {".py", ".sh", ".bash", ".zsh", ".rb", ".pl", ".js", ".mjs", ".cjs",
                   ".ts", ".exe", ".bin", ".dylib", ".so", ".command", ".bat", ".ps1"}
# 构建噪音,静默跳过
SKIP_NAMES = {".DS_Store", "Thumbs.db"}
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode", "node_modules"}

PLUGIN_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{0,59}")
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)     # 固定时间戳 → 可复现


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect(src: Path) -> list[Path]:
    """排序收集待打包文件;命中拒绝清单即抛错。"""
    files, denied = [], []
    for p in sorted(src.rglob("*")):
        if p.is_dir():
            continue
        if set(p.relative_to(src).parts) & SKIP_DIRS or p.name in SKIP_NAMES:
            continue
        if p.name.startswith("._"):          # macOS 资源叉
            continue
        rel = p.relative_to(src).as_posix()
        if p.suffix.lower() in DENIED_SUFFIXES:
            denied.append(rel)
        elif p.suffix.lower() in ALLOWED_SUFFIXES:
            files.append(p)
        else:
            print(f"  [skip] {rel}(扩展名不在允许清单)")
    if denied:
        raise SystemExit(
            "错误:插件包内不允许可执行代码(WORKFLOW.md §10.4「插件是纯声明式的」)。\n"
            "命中:\n" + "".join(f"  - {d}\n" for d in denied) +
            "机检脚本应随 VideoAgents 本体发布(modules/ 与 code/),不进插件包。")
    return files


def validate(src: Path, manifest: dict) -> None:
    """打包前先按安装器的口径自检,避免装上去才发现 manifest 不合法。"""
    name = str(manifest.get("name") or "").strip()
    if not PLUGIN_NAME_RE.fullmatch(name):
        raise SystemExit(f"错误:非法插件名 {name!r}")
    if name != src.name:
        raise SystemExit(f"错误:manifest name({name})与目录名({src.name})不一致——"
                         "安装后会被判为无效插件")
    agents = manifest.get("agents") or []
    if not agents:
        raise SystemExit("错误:manifest 未声明任何 agents")
    for a in agents:
        aid = a if isinstance(a, str) else str(a.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_\-]+/[A-Za-z0-9_\-]+", aid):
            raise SystemExit(f"错误:非法 agent id(须为「类别/名字」两段):{aid!r}")
        if not (src / "agents" / aid / "SOUL.md").is_file():
            raise SystemExit(f"错误:缺少 agents/{aid}/SOUL.md")
    for w in manifest.get("workflows") or []:
        if ".." in str(w).split("/") or not str(w).endswith((".yaml", ".yml")):
            raise SystemExit(f"错误:非法 workflow 路径:{w}")
        if not (src / w).is_file():
            raise SystemExit(f"错误:workflow 文件不存在:{w}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="插件源码目录(如 plugin-src/audio-to-video)")
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "dist"), help="产出目录,缺省 dist/")
    args = ap.parse_args()

    src = Path(args.source).resolve()
    if not (src / "plugin.json").is_file():
        raise SystemExit(f"错误:{src}/plugin.json 不存在")
    manifest = json.loads((src / "plugin.json").read_text(encoding="utf-8"))
    validate(src, manifest)

    name = manifest["name"]
    version = str(manifest.get("version") or "0.0.0")
    files = collect(src)
    if not files:
        raise SystemExit("错误:没有可打包的文件")

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{name}-{version}.zip"
    package = out_dir / filename

    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in files:
            arcname = f"{name}/{p.relative_to(src).as_posix()}"     # 套一层同名目录
            info = zipfile.ZipInfo(arcname, date_time=ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, p.read_bytes())

    meta = {
        "schema": 1,
        "name": name,
        "version": version,
        "description": manifest.get("description", ""),
        "filename": filename,
        "sha256": sha256(package),
        "size": package.stat().st_size,
        "files": [f"{name}/{p.relative_to(src).as_posix()}" for p in files],
        "agents": [a if isinstance(a, str) else a.get("id") for a in manifest.get("agents", [])],
        "outputs_ns": manifest.get("outputs_ns", ""),
        "requires": manifest.get("requires", {}),
        "host_note": ("宿主须自带 modules/avsync.py 与 code/check_av_sync.py("
                      "随 VideoAgents 本体发布);运行时不校验版本依赖,"
                      "缺失时插件仍可安装启用,但流程会停在 av0-ingest 报错"),
    }
    (out_dir / f"{name}-{version}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\n✅ {package}")
    print(f"   {len(files)} 个文件 / {meta['size']} 字节")
    print(f"   sha256 {meta['sha256']}")
    print(f"\n安装:curl -X POST --data-binary @{package} "
          f"http://127.0.0.1:8630/api/v1/plugins")


if __name__ == "__main__":
    sys.exit(main())
