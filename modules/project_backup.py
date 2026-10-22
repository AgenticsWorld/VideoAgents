"""项目自动备份(版本管理页「自动备份」,2026-10-22):签字后把项目目录整份打成 zip,放进用户指定的目录。

文件名 `<项目名>-<YYYYMMDD-HHMMSS>.zip`(同一秒重名追加 -2、-3…);zip 内套一层 `<项目名>/` 目录,
可直接用顶栏项目下拉「› 导入项目…」还原(导入按顶层目录名取项目名,同名自动改名)。
先写 `.<文件名>.part` 再原子改名,备份目录里只会出现完整的 zip。签字后总制片会立刻续跑、
H3P 签字同时清理版本文件,打包时项目目录可能还在变:中途消失 / 读不了的文件跳过并计数,
写备份失败(磁盘满等)照常抛出。
"""
from __future__ import annotations

import os
import re
import time
import zipfile
from pathlib import Path

# 本身已压缩的格式直接存储,不再 deflate(成片 / 素材视频动辄 GB,再压一遍只费 CPU)
STORED_EXTS = frozenset({
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".heic",
    ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac",
    ".zip", ".gz", ".7z", ".rar", ".glb", ".spz", ".ply", ".ttf", ".otf", ".woff", ".woff2",
})
_SKIP_ERRORS = (FileNotFoundError, PermissionError, IsADirectoryError, NotADirectoryError)


def backup_pattern(project: str) -> re.Pattern:
    """某项目的备份文件名(按项目名精确匹配:demo 不会认领 demo-2 的备份)。"""
    return re.compile(rf"{re.escape(project)}-\d{{8}}-\d{{6}}(?:-\d+)?\.zip")


def backup_name(project: str, dest_dir: Path, now: float | None = None) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    name, n = f"{project}-{ts}.zip", 2
    while (dest_dir / name).exists():
        name, n = f"{project}-{ts}-{n}.zip", n + 1
    return name


def is_inside(child: Path, parent: Path) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def list_backups(dest_dir: Path, project: str) -> list[dict]:
    """备份目录里该项目的 zip,新的在前:[{file, bytes, mtime}]。目录不存在 / 读不了返回空表。"""
    pat = backup_pattern(project)
    rows = []
    try:
        for f in Path(dest_dir).iterdir():
            if pat.fullmatch(f.name) and f.is_file():
                st = f.stat()
                rows.append({"file": f.name, "bytes": st.st_size, "mtime": st.st_mtime})
    except OSError:
        return []
    rows.sort(key=lambda r: (r["mtime"], r["file"]), reverse=True)
    return rows


def zip_project(src: Path, dest_dir: Path, project: str, now: float | None = None) -> dict:
    """把 src 整个目录打成 dest_dir/<project>-<时间戳>.zip。
    返回 {file, path, files, skipped, bytes};写失败时删掉 .part 并原样抛出。"""
    src, dest_dir = Path(src), Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = backup_name(project, dest_dir, now)
    final, part = dest_dir / name, dest_dir / f".{name}.part"
    # 备份目录落在项目目录里(设置接口已拒绝,这里兜底):不把备份打进备份
    skip = dest_dir.resolve() if is_inside(dest_dir, src) else None
    files = skipped = 0
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, allowZip64=True,
                             strict_timestamps=False) as zf:
            for root, dirs, names in os.walk(src):
                rp = Path(root)
                if skip is not None:
                    dirs[:] = [d for d in dirs if (rp / d).resolve() != skip]
                dirs.sort()
                rel = rp.relative_to(src)
                for fn in sorted(names):
                    f = rp / fn
                    ctype = zipfile.ZIP_STORED if f.suffix.lower() in STORED_EXTS else zipfile.ZIP_DEFLATED
                    try:
                        zf.write(f, (Path(project) / rel / fn).as_posix(), compress_type=ctype)
                        files += 1
                    except _SKIP_ERRORS:
                        skipped += 1
        os.replace(part, final)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return {"file": name, "path": str(final), "files": files, "skipped": skipped,
            "bytes": final.stat().st_size}
