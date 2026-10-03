"""Seedance 2.5 编辑/延长意图正则:只认对视频素材下达的明确指令,普通参考生成散文不得误判(2026-09-09 dzg6 grp005/grp010 反例)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import modules.genmedia as g  # noqa: E402

NEGATIVE = [
    '光照延续 Shot 1 的黄昏侧逆光,暖而不橙;场景环境声延续同一夏日黄昏路边声底。',
    '[Video 1] is the camera-view whitebox previs; follow it for framing. 增加一道暖轮廓边,修改后的站位保持不变。',
    'Shot 2: 他把公文包换到左手,去掉领带,替换成松开的衬衫领口。',
    '向前走半步,延长的影子落在路面上。',
]
EDIT = [
    '严格编辑@视频1，将其中的红色自行车修改为电动巡逻车。',
    '@视频1是唯一编辑母版，负责原始场景、机位、镜头运动。',
    '删除视频1里的路人,其余保持不变。',
    '编辑视频1，把招牌改成中性墙面。',
    '修改@视频2中的天空颜色。',
]
EXTEND = [
    '向后延长@视频1。延长片段的第一个画面直接承接@视频1的尾帧。',
    '@视频1是需要向后延长的原视频。',
    '向前延长 <视频1>',
    '续写@视频2',
]


def test_negative_prose_not_flagged():
    for t in NEGATIVE:
        assert not g._V25_EDIT_RE.search(t), t
        assert not g._V25_EXTEND_RE.search(t), t


def test_explicit_edit_and_extend_flagged():
    for t in EDIT:
        assert g._V25_EDIT_RE.search(t), t
    for t in EXTEND:
        assert g._V25_EXTEND_RE.search(t), t
