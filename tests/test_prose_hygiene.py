import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.prose_hygiene import annotation_hits, prompt_annotation_hits  # noqa: E402


def labels(text, **kw):
    return {h[0] for h in annotation_hits(text, **kw)}


def test_clean_prose_passes():
    assert annotation_hits('站在长案东侧,画右,自侧位扑来抓住丈夫袖子') == []
    assert annotation_hits('seated beside the round table on screen-right, tissue fragment on his cheek') == []
    assert annotation_hits('从厅门走到长案西南侧,面向李靖') == []


def test_decision_annotation_flagged():
    text = ('站在长案东侧。导演裁决A：自东侧[2.8,0,1.3]扑向丈夫，2.6秒停在案东净空；'
            '导演裁决A：自东侧[2.8,0,1.3]扑向丈夫，2.6秒停在案东净空')
    got = labels(text)
    assert {'裁决/修订批注', '坐标', '时间码', '重复句'} <= got


def test_whitebox_values_and_ids_flagged():
    assert '白模/关键帧术语' in labels('28.2秒visible:false')
    assert '白模/关键帧术语' in labels('朝力士锚点下令,yaw=-1.16')
    assert '选项引用' in labels('哪吒按001-A在台心')
    assert '内部编号' in labels('在石阶脚下停住至sh013镜尾')
    assert '时间码' in labels('组内23.5秒原切点省略行走')


def test_decimal_metres_warn_only():
    assert labels('递箭时向西北挪近0.24米') == set()
    assert '小数米数' in labels('递箭时向西北挪近0.24米', include_warn=True)


def test_prompt_body_skips_host_whitebox_blocks():
    vp = ('Whitebox facing: 李靖 yaw=1.2 faces camera\n\n'
          'Shot 1: 0-1秒：李靖站在厅中央,不采用其中的白模外观。')
    assert prompt_annotation_hits(vp) == []
    assert prompt_annotation_hits('Shot 1: 李靖站定。导演裁决A：28.2秒visible:false')
