"""跨进程文件独占锁(台账「读-改-写」事务用)。

POSIX 用 fcntl.flock;Windows 无 fcntl,用 msvcrt.locking 锁锁文件首字节(空文件也可锁),
同样是跨进程互斥。两者都不可用或加锁报错才退化为无锁,不让写台账因锁失败而中断。

用法:
    with file_lock.locked(path / "index.json.lock"):
        d = load(...); ...; os.replace(tmp, path / "index.json")
"""
from __future__ import annotations

import contextlib
import errno
import os
from pathlib import Path

try:
    import fcntl
except ImportError:                     # Windows
    fcntl = None
try:
    import msvcrt
except ImportError:                     # POSIX
    msvcrt = None


def _fd(f) -> int:
    return f if isinstance(f, int) else f.fileno()


def acquire(f) -> None:
    """对已打开的锁文件(文件对象或 fd)加独占锁,阻塞到拿到为止。"""
    fd = _fd(f)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        elif msvcrt is not None:
            # msvcrt 从当前位置起锁,先回到 0;LK_LOCK 每秒重试、10 次后报 EDEADLOCK,
            # 持锁方慢(出图入库可达数十秒)时接着等,与 flock 的阻塞语义一致
            while True:
                os.lseek(fd, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    return
                except OSError as e:
                    if e.errno != errno.EDEADLK:
                        raise
    except OSError:
        pass


def release(f) -> None:
    fd = _fd(f)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        elif msvcrt is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


@contextlib.contextmanager
def locked(path):
    """打开(必要时创建)锁文件并持有独占锁;退出时先解锁再关闭(Windows 关闭时不保证立即释放)。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a+") as fh:
        acquire(fh)
        try:
            yield
        finally:
            release(fh)
