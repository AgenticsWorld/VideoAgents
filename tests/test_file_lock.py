"""Run directly with python3 -m unittest discover -s tests -p test_file_lock.py."""
import errno
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from modules import file_lock

ROOT = Path(__file__).resolve().parent.parent

# 曾在顶层 / 调用路径上直接 import fcntl 的模块:Windows 下导入即失败(build_role_prompt「No module named 'fcntl'」)
LOCKING_MODULES = ('ark_assets', 'dialogue_tts', 'footage', 'image_canvas', 'npc_staging', 'script_keypoints',
                   'script_notes', 'shot_plates', 'storyboard_board')


class FileLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lock = Path(self.temp.name) / 'sub' / 'x.lock'

    def test_locked_is_mutually_exclusive(self):
        order = []

        def worker(tag):
            with file_lock.locked(self.lock):
                order.append(f'{tag}+')
                time.sleep(0.05)
                order.append(f'{tag}-')

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(self.lock.is_file())
        for i in range(0, len(order), 2):       # 每次进出成对,没有交错
            self.assertEqual(order[i][:-1], order[i + 1][:-1])

    def test_msvcrt_path_retries_until_locked(self):
        calls = []

        def locking(fd, mode, n):
            calls.append((mode, n))
            if mode == 'LK_LOCK' and len(calls) < 3:
                raise OSError(errno.EDEADLK, 'Resource deadlock avoided')

        fake = mock.Mock(LK_LOCK='LK_LOCK', LK_UNLCK='LK_UNLCK', locking=locking)
        with mock.patch.object(file_lock, 'fcntl', None), mock.patch.object(file_lock, 'msvcrt', fake):
            with file_lock.locked(self.lock):
                pass
        self.assertEqual(calls, [('LK_LOCK', 1)] * 3 + [('LK_UNLCK', 1)])

    def test_lock_errors_degrade_to_unlocked(self):
        fake = mock.Mock(LK_LOCK=1, LK_UNLCK=0, locking=mock.Mock(side_effect=OSError(errno.EACCES, 'denied')))
        with mock.patch.object(file_lock, 'fcntl', None), mock.patch.object(file_lock, 'msvcrt', fake):
            with file_lock.locked(self.lock):
                ran = True
        self.assertTrue(ran)

    def test_modules_import_without_fcntl(self):
        code = ("import sys; sys.modules['fcntl'] = None\n"
                f"for m in {LOCKING_MODULES!r}: __import__('modules.' + m)\n"
                "from modules import file_lock; assert file_lock.fcntl is None\n")
        r = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == '__main__':
    unittest.main()
