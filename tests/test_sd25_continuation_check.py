# -*- coding: utf-8 -*-
"""#84:sd25_prompt_structure 对尾段续接视频(*.continuation.mp4)认宿主续接块,免写 `[Video n] 用于…`。
用 modules/continuity_refs.apply_prompt 的真实产出(continuous 与 cut 两分支)。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "code"))

from modules import continuity_refs as cr  # noqa: E402
import prompt_skill_check as psc  # noqa: E402

TAIL = "assets/clips/ep01/grp001.continuation.mp4"
BODY = ("【人物】林青@Image 1。\n【动作与声音】[Video 1] 用于提供转身动作节奏。\n"
        "Shot 1: 林青走到窗边。使用：林青@Image 1。不采用：无。\n"
        "【未采用素材】无\n【保持一致】服装一致。")


def _prompt(extra_videos=("assets/refs/motion.mp4",)):
    return {"refs": ["assets/concepts/characters/CHR-001/main.png"], "video_refs": list(extra_videos),
            "video_prompt": BODY}


def _cont(boundary):
    return {"mode": "tail_video", "video": TAIL, "boundary": boundary, "previous_group": "grp001"}


@pytest.mark.parametrize("boundary", ["continuous", "cut"])
def test_tail_video_block_satisfies_role_sentence(boundary):
    out = cr.apply_prompt(_prompt(), _cont(boundary))
    assert out["video_refs"][-1] == TAIL
    n = len(out["video_refs"])
    marker = "Extend [Video %d] forward" % n if boundary == "continuous" else "Continue from the final moment of [Video %d]" % n
    assert marker in out["video_prompt"]
    errs = psc.sd25_structure_errors(out, "grp002")
    assert not [e for e in errs if "[Video" in e], errs


def test_tail_video_sentence_without_block_markers():
    """块标记被改写掉,但续接句式仍在 → 也认。"""
    out = cr.apply_prompt(_prompt(), _cont("cut"))
    out["video_prompt"] = out["video_prompt"].replace("Continuation reference:", "").replace("End continuation reference.", "")
    assert not [e for e in psc.sd25_structure_errors(out, "g") if "[Video" in e]


def test_non_continuation_video_still_requires_role():
    out = cr.apply_prompt(_prompt(), _cont("cut"))
    out["video_prompt"] = out["video_prompt"].replace("[Video 1] 用于提供转身动作节奏。", "")
    errs = psc.sd25_structure_errors(out, "g")
    assert any("[Video 1]" in e for e in errs) and not any("[Video 2]" in e for e in errs)


def test_continuation_without_host_block_still_fails():
    d = _prompt((TAIL,))
    d["video_prompt"] = BODY.replace("[Video 1] 用于提供转身动作节奏。", "")
    errs = psc.sd25_structure_errors(d, "g")
    assert any("[Video 1]" in e for e in errs)
