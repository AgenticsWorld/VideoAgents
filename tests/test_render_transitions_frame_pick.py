# -*- coding: utf-8 -*-
"""render_transitions check 按帧号取帧(issue #115 / #116):帧时间戳略早于名义值(时间基 1/12288 的拼接片)时,
按秒 -ss 正落在帧边界会多跳一帧;_gray_frame_n 按 -ss (n−½)/fps 取第 n 帧不受影响。合成小片,不依赖真实数据。"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

import render_transitions as rt  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
FPS = 24.0


def test_frame_no_half_up_and_shift_invariant():
    assert rt._frame_no(0, FPS) == 0
    assert rt._frame_no(-0.5, FPS) == 0
    assert rt._frame_no(851.9999999 / FPS, FPS) == 852          # 浮点误差不丢帧
    assert rt._frame_no(35.5, FPS) == 852
    # 恰半帧一律进位(不用银行家舍入):整数帧平移后取号差恰为平移量,源 / 成片两侧对位
    for k in range(6):
        assert rt._frame_no((k + 0.5) / FPS, FPS) == k + 1
        assert rt._frame_no((k + 7 + 0.5) / FPS, FPS) - rt._frame_no((k + 0.5) / FPS, FPS) == 7


def _early_pts_clip(path: Path):
    """48 帧,第 n 帧亮度 5n+10;n≥1 的帧 pts 比 n/24 早 4/12288 s(复刻 cut_post.mp4 的 35.499674)。"""
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "nullsrc=s=160x90:r=24:d=2,format=gray,geq=lum='N*5+10',settb=1/12288,setpts='N*512-4*gte(N\\,1)'",
                    "-fps_mode", "passthrough", "-enc_time_base", "1/12288", "-video_track_timescale", "12288",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-qp", "0", "-bf", "0", str(path)], check=True)


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_gray_frame_n_picks_exact_frame_despite_early_pts(tmp_path):
    clip = tmp_path / "early.mp4"
    _early_pts_clip(clip)
    lum = lambda b: round(rt._mean(b))  # noqa: E731
    # 夹具确实复现了旧问题:按秒取第 12 帧时刻拿到的是第 13 帧
    assert lum(rt._gray_frame(clip, 12 / FPS, 16, 9)) == 13 * 5 + 10
    for n in (0, 1, 12, 24, 46, 47):
        assert lum(rt._gray_frame_n(clip, n, FPS, 16, 9)) == n * 5 + 10
    # 末帧之后抽空 → 退回 _gray_frame 的逐级回退(issue #88),拿到末尾附近一帧而非空
    assert lum(rt._gray_frame_n(clip, 48, FPS, 16, 9)) >= 45 * 5 + 10
