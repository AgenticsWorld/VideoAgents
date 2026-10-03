# -*- coding: utf-8 -*-
"""花字设计 / 烧录的确定性逻辑(modules/captions.py、modules/captions_html.py;不起浏览器、不用 ffmpeg)。

覆盖机检 caption_schema_v2 / caption_groups_valid / caption_time_consistent / caption_assets_resolved /
caption_text_from_source 的判定核心 validate_captions,它的消息 → 机检名分桶(check_captions._bucket,
文案一改就可能归错桶),以及烧录侧的模版协议、字体解析与字形覆盖(caption_glyph_coverage)、锚点定位、
回执指纹(captions_rendered_all 判「要不要重渲」的依据)。
"""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import caption_catalog as cc  # noqa: E402
import captions as cap  # noqa: E402
import captions_html as chtml  # noqa: E402
import check_captions as chk  # noqa: E402
from _caption_helpers import TEMPLATE, font_template, make_font as _font  # noqa: E402

SHOT_LIST = {"generation_groups": [{"group_id": "grp001", "total_duration_s": 2},
                                   {"group_id": "grp002", "total_duration_s": 3}]}
SFX = {"sfx": [{"id": "whoosh_01", "file": "whoosh_01.wav"}]}


def _cap(**over):
    c = {"id": "cap001", "text": "青云宗", "type": "location", "group_id": "grp001", "local_start": 0.5, "local_end": 1.5,
         "template_ref": "template:title", "em_pct": 10, "position": "top_center"}
    c.update(over)
    return c


def _data(*caps):
    return {"schema_version": 3, "captions": list(caps) or [_cap()]}


@pytest.fixture()
def proj(tmp_path):
    tpl = tmp_path / "edit" / "caption_templates"
    tpl.mkdir(parents=True)
    (tpl / "title.html").write_text(TEMPLATE, encoding="utf-8")
    return tmp_path


def _issues(data, proj=None, **kw):
    return cap.validate_captions(data, kw.pop("shot_list", SHOT_LIST), proj_root=proj, **kw)


# ---------------------------------------------------------------- validate_captions

def test_valid_caption_has_no_issues(proj):
    assert _issues(_data(), proj, sfx_manifest=SFX) == []


@pytest.mark.parametrize("version", [None, 1, 2, "3"])
def test_only_schema_v3_is_accepted(version):
    data = _data()
    data["schema_version"] = version
    issues = _issues(data)
    assert len(issues) == 1 and "schema_version 必须为 3" in issues[0]


def test_cards_are_rejected_once():
    data = _data()
    data["cards"] = [{"id": "card1"}]
    assert [i for i in _issues(data) if "cards" in i] == ["v3 暂不支持 cards 图卡(需求出现时再移植)"]


def test_runaway_template_count_is_flagged():
    caps = [_cap(id=f"c{i}", template_ref=f"template:t{i}") for i in range(21)]
    assert any("21 个模版" in i for i in _issues(_data(*caps)))
    assert not any("个模版" in i for i in _issues(_data(*caps[:20])))


def test_duplicate_id_empty_text_unknown_type():
    issues = _issues(_data(_cap(), _cap(text="  "), _cap(id="cap002", type="nope")))
    assert "cap001: id 重复" in issues and "cap001: text 为空" in issues
    assert any(i.startswith("cap002: type 'nope' 不在") for i in issues)


def test_every_catalog_type_is_accepted():
    for t in cc.type_ids():
        lo, hi = cc.tier_em_range(cc.tier_of({"type": t}))
        assert _issues(_data(_cap(type=t, em_pct=(lo + hi) / 2))) == [], t


def test_unknown_group_stops_further_checks_for_that_caption():
    issues = _issues(_data(_cap(group_id="grp999", em_pct=999)))
    assert issues == ["cap001: group_id 'grp999' 不在 shot_list.generation_groups"]


@pytest.mark.parametrize("ls,le", [(1.5, 0.5), (1.0, 1.0), (-0.1, 1.0), ("0.5", 1.5), (None, 1.0), (0.5, None)])
def test_local_window_must_be_ordered_numbers(ls, le):
    assert any("local_start/local_end 非法" in i for i in _issues(_data(_cap(local_start=ls, local_end=le))))


def test_local_end_may_not_exceed_group_span():
    assert _issues(_data(_cap(local_end=2.04))) == []                                  # 0.05 s 容差内
    assert any("local_end 2.06 超出组时长 2.0" in i for i in _issues(_data(_cap(local_end=2.06))))


def test_av_span_overrides_total_duration():
    sl = {"generation_groups": [{"group_id": "grp001", "total_duration_s": 2, "av_span_s": 5.5}]}
    assert _issues(_data(_cap(local_end=5.5)), shot_list=sl) == []
    assert _issues(_data(_cap(local_end=5.6)), shot_list=sl)


def test_episode_start_reconciled_against_group_audio_in():
    # av 项目:组有 audio_in_s 时集级 start 必须 = 组起点 + local_start(±0.1 s)
    sl = {"generation_groups": [{"group_id": "grp001", "total_duration_s": 2, "audio_in_s": 10.0}]}
    assert _issues(_data(_cap(start=10.5)), shot_list=sl) == []
    assert _issues(_data(_cap(start=10.59)), shot_list=sl) == []
    assert any("与 组起点10.0+local_start0.5 不一致" in i for i in _issues(_data(_cap(start=10.7)), shot_list=sl))
    # 主流程项目(组上没有 audio_in_s)不在这里对账——由 check_captions 按花字时间轴核
    assert _issues(_data(_cap(start=99.0))) == []


def test_template_ref_format_and_existence(proj):
    assert any("template_ref 须为 template:<name>" in i for i in _issues(_data(_cap(template_ref="title")), proj))
    assert any("模版不存在" in i for i in _issues(_data(_cap(template_ref="template:missing")), proj))
    assert any("模版名非法" in i for i in _issues(_data(_cap(template_ref="template:../x")), proj))
    assert _issues(_data(_cap(template_ref="template:missing"))) == []                 # 不给项目根就不查文件


def test_template_protocol_violation_is_reported_per_caption(proj):
    (proj / "edit" / "caption_templates" / "bad.html").write_text(TEMPLATE.replace("<div", "<style>@keyframes x{}</style><div"),
                                                                 encoding="utf-8")
    issues = _issues(_data(_cap(template_ref="template:bad")), proj)
    assert len(issues) == 1 and issues[0].startswith("cap001: 模版 template:bad 含 CSS animation")


@pytest.mark.parametrize("type_,em,ok", [
    ("location", 5, True), ("location", 18, True), ("location", 4.9, False), ("location", 18.1, False),
    ("keyword", 9, True), ("keyword", 8.9, False), ("headline", 26, True), ("headline", 11.9, False),
    ("location", None, False), ("location", "10", False),
])
def test_em_pct_range_follows_tier(type_, em, ok):
    issues = [i for i in _issues(_data(_cap(type=type_, em_pct=em))) if "em_pct" in i]
    assert (not issues) == ok, issues


def test_explicit_tier_overrides_type_default():
    assert _issues(_data(_cap(type="location", tier="headline", em_pct=24))) == []
    assert any("em_pct 24 不在 label 档" in i for i in _issues(_data(_cap(type="location", em_pct=24))))
    assert any("tier 'giant' 不在" in i for i in _issues(_data(_cap(tier="giant"))))


def test_params_and_position():
    assert any("params 须为对象" in i for i in _issues(_data(_cap(params=["x"]))))
    assert _issues(_data(_cap(params={"glow": "#fff"}))) == []
    assert any("position 'middle' 不在" in i for i in _issues(_data(_cap(position="middle"))))
    for pos in list(cap.POSITIONS) + list(cap._POSITION_ALIASES) + [None]:
        assert _issues(_data(_cap(position=pos))) == [], pos


def test_sfx_must_be_in_manifest():
    ok = _cap(sfx={"sfx_id": "whoosh_01"})
    assert _issues(_data(ok), sfx_manifest=SFX) == []
    bad = _cap(sfx=[{"sfx_id": "whoosh_01"}, {"sfx_id": "boom"}])
    assert _issues(_data(bad), sfx_manifest=SFX) == ["cap001: sfx_id 'boom' 不在 sfx manifest"]
    assert _issues(_data(bad)) == []                                                   # 没给 manifest 时不查(由调用方报缺 manifest)


def test_segments_must_spell_the_text():
    assert _issues(_data(_cap(segments=[{"text": "青云"}, {"text": "宗"}]))) == []
    assert any("segments 拼接" in i for i in _issues(_data(_cap(segments=[{"text": "青云"}, {"text": "门"}]))))


def test_text_must_come_from_master_audio_transcript():
    beat = "那一年他上了青云宗拜师学艺"
    assert _issues(_data(_cap(text="青云宗")), beat_text=beat) == []
    assert _issues(_data(_cap(text="青云宗!A1")), beat_text=beat) == []                # 标点 / 拉丁字符不参与
    issues = _issues(_data(_cap(text="青云宗·无敌剑")), beat_text=beat)
    assert issues == ["cap001: 文本片段「无敌剑」不在母带原文中(禁造词,caption_text_from_source)"]
    assert _issues(_data(_cap(text="青云宗·无敌剑"))) == []                            # 主流程项目(无母带台本)不查


# ---------------------------------------------------------------- 消息 → 机检名分桶

@pytest.mark.parametrize("caption,kwargs,bucket", [
    (dict(group_id="grp999"), {}, "caption_groups_valid"),
    (dict(local_start=1.5, local_end=0.5), {}, "caption_groups_valid"),
    (dict(local_end=9.0), {}, "caption_groups_valid"),
    (dict(start=50.0), {"shot_list": {"generation_groups": [{"group_id": "grp001", "total_duration_s": 2, "audio_in_s": 10.0}]}},
     "caption_time_consistent"),
    (dict(sfx={"sfx_id": "boom"}), {"sfx_manifest": SFX}, "caption_assets_resolved"),
    (dict(text="无敌剑"), {"beat_text": "青云宗"}, "caption_text_from_source"),
    (dict(type="nope"), {}, "caption_schema_v2"),
    (dict(text=""), {}, "caption_schema_v2"),
    (dict(em_pct=99), {}, "caption_schema_v2"),
    (dict(position="middle"), {}, "caption_schema_v2"),
    (dict(template_ref="x"), {}, "caption_schema_v2"),
    (dict(params=1), {}, "caption_schema_v2"),
    (dict(segments=[{"text": "x"}]), {}, "caption_schema_v2"),
    (dict(tier="giant"), {}, "caption_schema_v2"),
])
def test_each_violation_lands_in_its_check(caption, kwargs, bucket):
    issues = _issues(_data(_cap(**caption)), **kwargs)
    assert issues, caption
    assert {chk._bucket(m) for m in issues} == {bucket}, issues


def test_template_and_schema_messages_bucket_as_schema(proj):
    assert {chk._bucket(m) for m in _issues(_data(_cap(template_ref="template:missing")), proj)} == {"caption_schema_v2"}
    data = _data()
    data["schema_version"] = 2
    assert {chk._bucket(m) for m in _issues(data)} == {"caption_schema_v2"}


@pytest.mark.parametrize("cap_id", ["group_id_cap", "manifest_01", "local_start", "母带原文"])
def test_caption_id_cannot_steer_the_bucket(cap_id):
    # 条目 id 是 Agent 自己起的:id 里带着别的桶的关键词,消息也仍按正文归桶
    issues = _issues(_data(_cap(id=cap_id, type="nope")))
    assert issues and {chk._bucket(m) for m in issues} == {"caption_schema_v2"}
    issues = _issues(_data(_cap(id=cap_id, sfx={"sfx_id": "boom"})), sfx_manifest=SFX)
    assert {chk._bucket(m) for m in issues} == {"caption_assets_resolved"}


# ---------------------------------------------------------------- 模版协议

def test_protocol_accepts_minimal_template():
    assert chtml.protocol_issues(TEMPLATE) == []


@pytest.mark.parametrize("html,needle", [
    (TEMPLATE.replace("window.seek", "window.go"), "缺 window.seek"),
    (TEMPLATE.replace("__anchorEl", "__anchor"), "缺 window.__anchorEl"),
    (TEMPLATE + "<style>@keyframes spin{}</style>", "CSS animation"),
    (TEMPLATE + "<style>#t{animation: spin 1s}</style>", "CSS animation"),
    (TEMPLATE + "<script>requestAnimationFrame(f)</script>", "自走时钟"),
    (TEMPLATE + "<script>setInterval(f,16)</script>", "自走时钟"),
    (TEMPLATE + "<script>setTimeout (f,16)</script>", "自走时钟"),
    (TEMPLATE + '<link href="https://fonts.example.com/a.css">', "外部网络引用"),
    (TEMPLATE + '<img src="http://x/y.png">', "外部网络引用"),
])
def test_protocol_violations(html, needle):
    issues = chtml.protocol_issues(html)
    assert len(issues) == 1 and needle in issues[0]


def test_template_path_resolution(proj):
    assert chtml.template_path(proj, "template:title") == proj / "edit" / "caption_templates" / "title.html"
    for ref in ("title", "template:", "template:a/b", "template:a.b", "template:nope"):
        with pytest.raises(RuntimeError):
            chtml.template_path(proj, ref)


# ---------------------------------------------------------------- 字体解析与字形覆盖

@pytest.fixture()
def font_proj(proj):
    _font(proj / "refs" / "fonts" / "kai.ttf", "Test Kai", "青云宗A1")
    (proj / "edit" / "caption_templates" / "kai.html").write_text(font_template("proj:TestKai"), encoding="utf-8")
    return proj


def test_project_fonts_join_the_manifest(font_proj):
    manifest = cap.load_fonts_manifest(font_proj / "no-global-manifest.json", font_proj)
    assert [(f["id"], f["family"], f["source"], f["cjk"]) for f in manifest["fonts"]] == [("proj:TestKai", "Test Kai", "project", False)]
    assert cap.resolve_font("proj:TestKai", manifest)["path"].endswith("kai.ttf")
    with pytest.raises(RuntimeError, match="不在 fonts manifest"):
        cap.resolve_font("user:Nope", manifest)
    with pytest.raises(RuntimeError, match="fonts-scan"):
        cap.load_fonts_manifest(font_proj / "no-global-manifest.json", require=True)


def test_project_font_shadows_global_entry_with_same_id(font_proj, tmp_path):
    import json
    gm = tmp_path / "global.json"
    gm.write_text(json.dumps({"fonts": [{"id": "proj:TestKai", "path": "/elsewhere.ttf"}, {"id": "user:Other", "path": "/o.ttf"}]}))
    manifest = cap.load_fonts_manifest(gm, font_proj)
    assert [f["id"] for f in manifest["fonts"]] == ["proj:TestKai", "user:Other"]      # 项目字体排前、同 id 取项目的
    assert manifest["fonts"][0]["path"].endswith("kai.ttf")


def test_template_fonts_resolve_to_files(font_proj):
    manifest = cap.load_fonts_manifest(font_proj / "none.json", font_proj)
    files = chtml.template_font_files(font_proj, "template:kai", manifest)
    assert [p.name for p in files] == ["kai.ttf"]
    assert chtml.template_font_files(font_proj, "template:title", manifest) == []       # 模版没写 font://
    html, ids = chtml.resolve_template(font_proj, "template:kai", manifest)
    assert ids == ["proj:TestKai"] and "font://" not in html and files[0].as_uri() in html
    with pytest.raises(RuntimeError, match="不在 fonts manifest"):
        chtml.template_font_files(font_proj, "template:kai", {"fonts": []})


def test_glyph_coverage_reports_missing_characters(font_proj):
    files = [font_proj / "refs" / "fonts" / "kai.ttf"]
    assert chtml.glyph_missing("青云宗 A1", files) == {}
    assert chtml.glyph_missing("青云宗!,。?", files) == {str(files[0]): "。"}            # ASCII 标点不查,全角标点要有字形
    assert chtml.glyph_missing("青云门B", files) == {str(files[0]): "B门"}
    assert chtml.glyph_missing("任何字", []) == {}


# ---------------------------------------------------------------- 估宽 / 样式 / 锚点

def test_width_estimate_distinguishes_cjk_and_latin():
    assert chtml.estimate_css_width("") == 0
    assert chtml.estimate_css_width("青") == 150 + 26
    assert chtml.estimate_css_width("A") == pytest.approx(150 * 0.62 + 26)
    assert chtml.estimate_css_width("青云A") == pytest.approx(2 * 176 + 119)
    assert chtml.estimate_css_width("青", spacing_px=0) == 150


def test_style_rejects_bad_em_and_params():
    assert chtml._style_v3(_cap(params={"a": 1})) == ("template:title", 10.0, {"a": 1})
    for bad in (dict(em_pct=6.9), dict(em_pct=26.1), dict(em_pct=None), dict(params=[1])):
        with pytest.raises(RuntimeError):
            chtml._style_v3(_cap(**bad))


BBOX = {"x": 100, "y": 40, "w": 400, "h": 100}      # 文字块在贴片内的位置
W, H = 1920, 1080


def _text_box(position):
    x, y = chtml._anchor_xy(position, BBOX, W, H)
    return x + BBOX["x"], y + BBOX["y"], x + BBOX["x"] + BBOX["w"], y + BBOX["y"] + BBOX["h"]


@pytest.mark.parametrize("position,expect", [
    ("top_center", (760, 129.6, 1160, 229.6)),       # 水平居中于 0.5W,上沿在 0.12H
    ("center", (760, 382, 1160, 482)),               # 以 (0.5W, 0.4H) 为中心
    ("top_left", (153.6, 129.6, 553.6, 229.6)),      # 左上角在 (0.08W, 0.12H)
    ("top_right", (1366.4, 129.6, 1766.4, 229.6)),   # 右上角在 (0.92W, 0.12H)
    ("lower_center", (760, 677.6, 1160, 777.6)),     # 下沿在 0.72H
])
def test_anchor_places_text_box(position, expect):
    assert _text_box(position) == pytest.approx(expect)


def test_anchor_alias_and_unknown_fall_back():
    assert _text_box("top") == _text_box("top_center")
    assert _text_box("bottom") == _text_box("lower_center")
    assert _text_box("nowhere") == _text_box("center")


def test_anchor_keeps_text_inside_safe_area():
    wide = {"x": 0, "y": 0, "w": 1000, "h": 100}
    x, _y = chtml._anchor_xy("top_right", wide, W, H)                # 右对齐后左边仍在画面内
    assert x >= 0.02 * W - 1e-6 and x + 1000 <= 0.98 * W + 1e-6
    x, _y = chtml._anchor_xy("mid_left", {"x": 0, "y": 0, "w": 1900, "h": 100}, W, H)
    assert x + 1900 <= 0.98 * W + 1e-6                               # 太宽:以不出右边距为准
    tall = {"x": 0, "y": 0, "w": 400, "h": 500}
    _x, y = chtml._anchor_xy("lower_center", tall, W, H)
    assert y + 500 <= 0.82 * H + 1e-6                                # 不进底部字幕安全区
    _x, y = chtml._anchor_xy("top_center", {"x": 0, "y": 0, "w": 400, "h": 1000}, W, H)
    assert y == pytest.approx(0.02 * H)                              # 上下都放不下时贴上边距


# ---------------------------------------------------------------- 回执指纹

def _hash(proj, caps):
    return chtml._captions_hash_v3(caps, proj, cap.load_fonts_manifest(proj / "none.json", proj))


def test_hash_ignores_fields_that_do_not_change_pixels(proj):
    base = _hash(proj, [_cap()])
    same = _cap(start=12.5, end=13.5, type="keyword", sfx={"sfx_id": "whoosh_01"}, note="改了备注", id="renamed", group_id="grp002")
    assert _hash(proj, [same]) == base


@pytest.mark.parametrize("change", [
    dict(text="青云门"), dict(local_start=0.6), dict(local_end=1.6), dict(em_pct=11), dict(position="center"),
    dict(params={"glow": "#fff"}), dict(segments=[{"text": "青云宗", "color": "#f00"}]), dict(_final_t0=3.0),
])
def test_hash_changes_with_rendered_fields(proj, change):
    assert _hash(proj, [_cap(**change)]) != _hash(proj, [_cap()])


def test_hash_tracks_template_content_and_order(proj):
    base = _hash(proj, [_cap()])
    two = [_cap(), _cap(id="cap002", text="藏经阁")]
    assert _hash(proj, two) != _hash(proj, list(reversed(two)))
    (proj / "edit" / "caption_templates" / "title.html").write_text(TEMPLATE + "<!-- v2 -->", encoding="utf-8")
    assert _hash(proj, [_cap()]) != base


def test_hash_tracks_font_file(font_proj):
    caps = [_cap(template_ref="template:kai")]
    base = _hash(font_proj, caps)
    _font(font_proj / "refs" / "fonts" / "kai.ttf", "Test Kai", "青云宗A1门")           # 同名字体换了文件
    assert _hash(font_proj, caps) != base


def test_hash_requires_existing_template(proj):
    with pytest.raises(RuntimeError, match="模版不存在"):
        _hash(proj, [_cap(template_ref="template:gone")])


def test_episode_hash_follows_final_timeline(proj):
    fonts = cap.load_fonts_manifest(proj / "none.json", proj)
    items = [{"cap": _cap(), "t0": 1.5, "t1": 2.5}]
    base = chtml.episode_items_hash(items, proj, fonts)
    assert chtml.episode_items_hash(copy.deepcopy(items), proj, fonts) == base
    assert chtml.episode_items_hash([{"cap": _cap(), "t0": 2.5, "t1": 3.5}], proj, fonts) != base      # 片头变长 → 整体后移
    assert chtml.episode_items_hash([{"cap": _cap(), "t0": 1.5004, "t1": 2.5}], proj, fonts) == base   # 毫秒以下抖动不算
