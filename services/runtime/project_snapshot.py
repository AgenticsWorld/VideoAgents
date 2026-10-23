"""项目快照:派单起引擎前把项目目录克隆一份到数据目录之外,Agent 误删后可从这里找回。

2026-10-22 事故里 Agent 一条 rm -rf 删光了整个桌面端数据目录(见 delete_guard.py)。删除守卫只拦得住
命令里认得出的删除,脚本里 rmtree 等仍可能误删当前项目,快照是最后一道兜底。

- macOS(APFS)用 clonefile 整目录一次克隆:写时复制,刚克隆时不占额外空间(15 GB / 2 万个文件实测
  0.65 s);之后被改写或删除的文件,旧数据留在快照里才开始占空间。
- 其它平台 / 非 APFS 退回硬链接树:防删除,不防原地覆盖写(同一 inode)。
- 快照目录与项目不在同一卷时(克隆、硬链接都做不了)跳过,不做整份复制。

存放:<快照根>/<数据目录标识>/<项目>/<YYYYmmdd-HHMMSS>。快照根默认在数据目录之外(macOS
~/Library/Application Support/VideoAgents-Snapshots,Windows %LOCALAPPDATA%,其它 ~/.local/share),
VIDEOAGENTS_SNAPSHOT_DIR 可改;VIDEOAGENTS_SNAPSHOT=0 关闭。同一项目距上次快照不足
VIDEOAGENTS_SNAPSHOT_INTERVAL 秒(缺省 1800)不重拍。保留:VIDEOAGENTS_SNAPSHOT_DAYS 天内(缺省 7)
最近 VIDEOAGENTS_SNAPSHOT_KEEP 份(缺省 3)+ 每天最新一份,更早的删掉。

还原(只在命令行,不覆盖已有目录):
  python -m services.runtime.project_snapshot list [<快照根>]
  python -m services.runtime.project_snapshot restore <快照目录> <新项目目录>
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import sys
import time
from pathlib import Path

_STAMP = "%Y%m%d-%H%M%S"
_CLONE_NOFOLLOW, _CLONE_NOOWNERCOPY = 0x1, 0x2


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def enabled() -> bool:
    return (os.environ.get("VIDEOAGENTS_SNAPSHOT") or "1").strip().lower() not in ("0", "false", "no", "off")


def snapshot_root() -> Path:
    custom = os.environ.get("VIDEOAGENTS_SNAPSHOT_DIR")
    if custom:
        return Path(custom).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "VideoAgents-Snapshots"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "VideoAgents-Snapshots"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "videoagents-snapshots"


def store_dir(data_dir: Path, root: Path | None = None) -> Path:
    """数据目录对应的快照子目录:同一台机器上的多个实例(桌面端 / 源码版 / 隔离测试实例)同名项目互不覆盖。"""
    data_dir = Path(data_dir).resolve()
    digest = hashlib.sha1(str(data_dir).encode("utf-8")).hexdigest()[:8]
    name = "-".join(p for p in (data_dir.parent.name, data_dir.name, digest) if p)
    return (root or snapshot_root()) / name


def _clonefile(src: Path, dst: Path) -> bool:
    if sys.platform != "darwin":
        return False
    try:
        fn = ctypes.CDLL(None, use_errno=True).clonefile
    except (OSError, AttributeError):
        return False
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
    fn.restype = ctypes.c_int
    return fn(os.fsencode(src), os.fsencode(dst), _CLONE_NOFOLLOW | _CLONE_NOOWNERCOPY) == 0


def _link_tree(src: Path, dst: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(src):
        rel = Path(dirpath).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for name in dirnames:     # os.walk 不进入目录符号链接,照原样复制链接
            s = Path(dirpath) / name
            if s.is_symlink():
                os.symlink(os.readlink(s), dst / rel / name)
        for name in filenames:
            s = Path(dirpath) / name
            if s.is_symlink():
                os.symlink(os.readlink(s), dst / rel / name)
            else:
                os.link(s, dst / rel / name)


def _same_volume(a: Path, b: Path) -> bool:
    while not b.exists():
        b = b.parent
    return a.stat().st_dev == b.stat().st_dev


def clone_dir(src: Path, dst: Path) -> str:
    """把 src 整个克隆到 dst(dst 不得存在);返回所用方式 clone / hardlink。失败时清掉半成品并抛 OSError。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if _clonefile(src, dst):
        return "clone"
    shutil.rmtree(dst, ignore_errors=True)
    try:
        _link_tree(src, dst)
    except OSError:
        shutil.rmtree(dst, ignore_errors=True)
        raise
    return "hardlink"


def _snapshots(project_store: Path) -> list[tuple[float, Path]]:
    out = []
    try:
        entries = list(project_store.iterdir())
    except OSError:
        return out
    for p in entries:
        if p.name.startswith("."):
            continue
        try:
            out.append((time.mktime(time.strptime(p.name, _STAMP)), p))
        except ValueError:
            continue
    return sorted(out, reverse=True)


def prune(project_store: Path, *, keep: int, days: int, now: float) -> list[Path]:
    """days 天内保留最近 keep 份 + 每天最新一份,其余及更早的删掉;顺带清理一小时前没收尾的 .part。"""
    kept_days, removed = set(), []
    for i, (ts, path) in enumerate(_snapshots(project_store)):
        day = time.strftime("%Y%m%d", time.localtime(ts))
        if now - ts <= days * 86400 and (i < keep or day not in kept_days):
            kept_days.add(day)
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path)
    try:
        for p in project_store.glob(".*.part"):
            if now - p.lstat().st_mtime > 3600:
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass
    return removed


def take(project_dir: Path, project_store: Path, *, interval: int | None = None,
         keep: int | None = None, days: int | None = None, now: float | None = None) -> dict:
    """距上次快照超过 interval 秒才拍。返回 {status: taken / fresh / skipped, path, method, seconds, reason}。"""
    interval = _env_int("VIDEOAGENTS_SNAPSHOT_INTERVAL", 1800) if interval is None else interval
    keep = max(1, _env_int("VIDEOAGENTS_SNAPSHOT_KEEP", 3) if keep is None else keep)
    days = max(1, _env_int("VIDEOAGENTS_SNAPSHOT_DAYS", 7) if days is None else days)
    now = time.time() if now is None else now
    project_dir, project_store = Path(project_dir), Path(project_store)
    if not project_dir.is_dir():
        return {"status": "skipped", "reason": "project missing"}
    existing = _snapshots(project_store)
    if existing and now - existing[0][0] < interval:
        return {"status": "fresh", "path": str(existing[0][1])}
    if not _same_volume(project_dir, project_store):
        return {"status": "skipped", "reason": f"{project_store} 与项目不在同一卷,无法克隆"}
    name = time.strftime(_STAMP, time.localtime(now))
    final = project_store / name
    if final.exists():
        return {"status": "fresh", "path": str(final)}
    part = project_store / f".{name}.part"
    shutil.rmtree(part, ignore_errors=True)
    t0 = time.time()
    method = clone_dir(project_dir, part)
    os.replace(part, final)
    # 同一数据目录下所有项目一起修剪:久未运行的项目(含已删除的)快照也按天数过期
    for store in [project_store, *(p for p in project_store.parent.iterdir()
                                   if p.is_dir() and p != project_store)]:
        prune(store, keep=keep, days=days, now=now)
    return {"status": "taken", "path": str(final), "method": method, "seconds": round(time.time() - t0, 2)}


def _cli(argv: list[str]) -> int:
    if argv[:1] == ["list"]:
        root = Path(argv[1]) if len(argv) > 1 else snapshot_root()
        for store in sorted(p for p in root.glob("*") if p.is_dir()):
            for project in sorted(p for p in store.iterdir() if p.is_dir()):
                for _, snap in _snapshots(project):
                    print(snap)
        return 0
    if len(argv) == 3 and argv[0] == "restore":
        src, dst = Path(argv[1]), Path(argv[2])
        if not src.is_dir():
            print(f"快照不存在:{src}", file=sys.stderr)
            return 1
        if dst.exists():
            print(f"目标已存在,不覆盖:{dst}", file=sys.stderr)
            return 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not _clonefile(src, dst):
            shutil.copytree(src, dst, symlinks=True)   # 还原用整份复制,不与快照共用 inode
        print(dst)
        return 0
    print(__doc__.split("还原")[1].strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
