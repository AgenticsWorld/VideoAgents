"""按需下载上游大件(固定在 upstream.json 记录的 commit,sha256 校验),放进 data/.videoagents/codevideo/lemo/(保持上游仓库布局)。

- 包(release assets,tar):instruments-<库>(采样乐器,代码作曲用)、hdri(3D 风格的环境贴图)
- 文件(raw.githubusercontent.com/<repo>/<rev>/<path>):demo:<slug>(样片源码与海报,写完 TREATMENT.md 后才下)、
  assets:polyhaven(3D 风格用的 CC0 模型与贴图)
下载可断点续传;包解压前逐条检查成员路径(不出镜像目录、不含设备文件与越界链接)。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import CACHE_DIR, MIRROR, CodeVideoError, upstream

MARKERS = MIRROR / ".packs"
UA = {"User-Agent": "VideoAgents-codevideo/1"}


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def packs() -> dict:
    return upstream()["release"]["packs"]


def pack_status() -> list[dict]:
    rows = []
    for name, p in packs().items():
        m = MARKERS / f"{name}.json"
        try:
            ok = json.loads(m.read_text()).get("sha256") == p["sha256"]
        except (OSError, ValueError):
            ok = False
        rows.append({"name": name, "file": p["file"], "size": p["size"], "installed": ok,
                     "kind": "instruments" if name.startswith("instruments-") else name})
    return rows


def installed_libs() -> list[str]:
    return [r["name"].split("-", 1)[1] for r in pack_status() if r["installed"] and r["kind"] == "instruments"]


def _download(url: str, tmp: Path, size: int | None, progress=None):
    tmp.parent.mkdir(parents=True, exist_ok=True)
    have = tmp.stat().st_size if tmp.is_file() else 0
    if size is not None and have > size:
        tmp.unlink()
        have = 0
    if size is not None and have == size:
        return
    fails = 0
    while True:
        req = urllib.request.Request(url, headers={**UA, **({"Range": f"bytes={have}-"} if have else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                append = have > 0 and r.status == 206
                with tmp.open("ab" if append else "wb") as f:
                    done = have if append else 0
                    last = time.time()
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if progress and time.time() - last > 1:
                            progress(done, size)
                            last = time.time()
                    if progress:
                        progress(done, size)
            return
        except urllib.error.HTTPError as e:
            fails += 1
            if e.code == 416:      # 已经下完
                return
            if e.code < 500 or fails > 3:
                raise CodeVideoError(f"下载失败 HTTP {e.code}:{url}") from e
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            fails += 1
            if fails > 3:
                raise CodeVideoError(f"下载失败({e}):{url};网络恢复后重跑同一条命令会断点续传") from e
        time.sleep(2 * fails)
        have = tmp.stat().st_size if tmp.is_file() else 0


def _safe_extract(tar: Path, dest: Path):
    root = dest.resolve()
    with tarfile.open(tar) as t:
        members = t.getmembers()
        for m in members:
            target = (root / m.name).resolve()
            if m.name.startswith("/") or m.isdev() or not (target == root or root in target.parents):
                raise CodeVideoError(f"包里有不安全的条目({m.name}),没有解压")
            if (m.issym() or m.islnk()):
                link = ((target.parent / m.linkname) if m.issym() else (root / m.linkname)).resolve()
                if not (link == root or root in link.parents):
                    raise CodeVideoError(f"包里有越界链接({m.name}),没有解压")
        if hasattr(tarfile, "data_filter"):
            t.extractall(root, filter="data")
        else:
            t.extractall(root)


def fetch_pack(name: str, progress=None) -> dict:
    p = packs().get(name)
    if not p:
        raise CodeVideoError(f"没有这个包:{name}(可选 {', '.join(packs())})")
    st = next(r for r in pack_status() if r["name"] == name)
    if st["installed"]:
        return {"name": name, "installed": True, "skipped": True}
    MIRROR.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(MIRROR).free
    if free < p["size"] * 2.2:
        raise CodeVideoError(f"磁盘空间不足:{p['file']} {p['size'] / 1e9:.2f} GB,下载加解压约需 {p['size'] * 2.2 / 1e9:.1f} GB,"
                             f"现有 {free / 1e9:.1f} GB")
    up = upstream()
    url = f"https://github.com/{up['repo']}/releases/download/{up['release']['tag']}/{p['file']}"
    tmp = CACHE_DIR / ".download" / (p["file"] + ".part")
    _download(url, tmp, p["size"], progress)
    if _sha256(tmp) != p["sha256"]:
        tmp.unlink(missing_ok=True)
        raise CodeVideoError(f"{p['file']} 校验失败(sha256 不符),已删除下载件;重跑一次")
    _safe_extract(tmp, MIRROR)
    tmp.unlink(missing_ok=True)
    MARKERS.mkdir(parents=True, exist_ok=True)
    (MARKERS / f"{name}.json").write_text(json.dumps({"sha256": p["sha256"], "at": time.strftime("%Y-%m-%dT%H:%M:%S")}))
    return {"name": name, "installed": True, "skipped": False}


def remove_pack(name: str) -> dict:
    if name not in packs():
        raise CodeVideoError(f"没有这个包:{name}")
    if name.startswith("instruments-"):
        shutil.rmtree(MIRROR / "core" / "audio" / "instruments" / name.split("-", 1)[1], ignore_errors=True)
    (MARKERS / f"{name}.json").unlink(missing_ok=True)
    return {"name": name, "installed": False}


def file_set(key: str) -> list[dict]:
    rows = upstream()["files"].get(key)
    if rows is None:
        raise CodeVideoError(f"上游清单里没有 {key}")
    return rows


def fetch_files(key: str, progress=None) -> dict:
    up = upstream()
    rows = file_set(key)
    got = skipped = 0
    for row in rows:
        dst = MIRROR / row["path"]
        if dst.is_file() and dst.stat().st_size == row["size"] and _sha256(dst) == row["sha256"]:
            skipped += 1
            continue
        tmp = dst.with_name(dst.name + ".part")
        tmp.unlink(missing_ok=True)
        url = f"https://raw.githubusercontent.com/{up['repo']}/{up['rev']}/" + "/".join(
            urllib.request.quote(x) for x in row["path"].split("/"))
        _download(url, tmp, row["size"])
        if _sha256(tmp) != row["sha256"]:
            tmp.unlink(missing_ok=True)
            raise CodeVideoError(f"{row['path']} 校验失败(sha256 不符)")
        tmp.replace(dst)
        got += 1
        if progress:
            progress(got + skipped, len(rows))
    return {"key": key, "files": len(rows), "downloaded": got, "cached": skipped,
            "root": str(MIRROR)}


def files_ready(key: str) -> bool:
    try:
        rows = file_set(key)
    except CodeVideoError:
        return False
    return all((MIRROR / r["path"]).is_file() and (MIRROR / r["path"]).stat().st_size == r["size"] for r in rows)


def env_auto_fetch() -> bool:
    return os.environ.get("CODEVIDEO_AUTO_FETCH") == "1"
