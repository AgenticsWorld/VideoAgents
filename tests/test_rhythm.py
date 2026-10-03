"""叙事节奏(2026-09-07):目录 / brief.md「## 叙事节奏」小节的写入与回读。"""
from services.runtime import core, rhythm


def test_catalog_ids_unique_and_names_unique():
    for items in (rhythm.EPISODE_RHYTHMS, rhythm.SEASON_RHYTHMS):
        ids = [r["id"] for r in items]
        names = [r["name"] for r in items]
        assert len(ids) == len(set(ids))
        assert len(names) == len(set(names))
        assert rhythm.CUSTOM_ID not in ids and rhythm.CUSTOM_NAME not in names
        for r in items:
            assert r["beats"] and r["detail"] and r["fit"]


def test_format_and_parse_roundtrip_catalog():
    r = {"episode": "comedy", "season": "serial"}
    sec = rhythm.format_section(r)
    assert "### 单集节奏:喜剧包袱" in sec and "### 跨集节奏:连续剧" in sec
    assert "节拍链:建立情境与预期 → 强化预期 → 意外转折 → 笑点释放 → 回扣或新包袱" in sec
    assert rhythm.parse_section(sec) == {
        "episode": "comedy", "episode_custom": "", "season": "serial", "season_custom": ""}


def test_custom_roundtrip_and_unknown_name_becomes_custom():
    r = {"episode": "custom", "episode_custom": "A → B → C", "season": ""}
    sec = rhythm.format_section(r)
    assert sec == "### 单集节奏:自定义\nA → B → C"
    assert rhythm.parse_section(sec)["episode_custom"] == "A → B → C"
    got = rhythm.parse_section("### 跨集节奏:不认识的名字\n正文")
    assert got["season"] == "custom" and got["season_custom"] == "不认识的名字\n正文"


def test_normalize_rejects_unknown_and_empty_custom():
    assert rhythm.normalize({"episode": "bogus", "season": "custom", "season_custom": ""}) == {
        "episode": "", "episode_custom": "", "season": "", "season_custom": ""}
    assert rhythm.format_section(None) == ""


def test_brief_md_roundtrip_with_rhythm_and_legacy():
    r = {"episode": "short_drama", "season": "window_dev_close"}
    text = core.format_brief("构想", "风格", r)
    assert text.index(core.BRIEF_SECTION) < text.index(core.STYLE_SECTION) < text.index(core.RHYTHM_SECTION)
    brief, style, got = core.parse_brief(text)
    assert (brief, style) == ("构想", "风格")
    assert got["episode"] == "short_drama" and got["season"] == "window_dev_close"
    # 小节顺序颠倒也能回读
    swapped = f"{core.BRIEF_HEADER}\n\n{core.BRIEF_SECTION}\n\n构想\n\n{core.RHYTHM_SECTION}\n\n{rhythm.format_section(r)}\n\n{core.STYLE_SECTION}\n\n风格\n"
    assert core.parse_brief(swapped)[:2] == ("构想", "风格")
    assert core.parse_brief(swapped)[2]["episode"] == "short_drama"
    # 旧格式
    assert core.parse_brief("纯文本") == ("纯文本", "", rhythm.normalize(None))
    assert core.format_brief("", "", {}) == ""
    assert core.RHYTHM_SECTION in core.format_brief("", "", r)


def test_brief_roundtrip_keeps_unknown_sections_verbatim():
    """#68:「## 叙事节奏规约…」等长标题不得被当成节奏小节;未知小节 parse→format 一字不丢、位置不变。"""
    r = {"episode": "short_drama"}
    src = (f"{core.BRIEF_HEADER}\n\n{core.BRIEF_SECTION}\n\n构想正文\n\n"
           "## 叙事节奏规约(自定义)\n\n每集三段:开场→对撞→钩子\n\n"
           "## 其它小节 X\n\n- 条目一\n- 条目二\n\n"
           f"{core.STYLE_SECTION}\n\n风格正文\n\n"
           f"{core.RHYTHM_SECTION}\n\n{rhythm.format_section(r)}\n\n"
           "## 附录\n\n附录正文 ### 不是标题\n")
    brief, style, got, layout = core.parse_brief_layout(src)
    assert "## 叙事节奏规约(自定义)" in brief and "## 其它小节 X" in brief and "- 条目二" in brief
    assert style == "风格正文" and got["episode"] == "short_drama"
    assert layout["rhythm_tail"] == "## 附录\n\n附录正文 ### 不是标题"
    out = core.format_brief(brief, style, got, layout)
    assert out == src
    # 模拟视频节奏弹窗只改 rhythm:其它小节全部保留
    out2 = core.format_brief(brief, style, {"episode": "comedy"}, layout)
    for frag in ("## 叙事节奏规约(自定义)\n\n每集三段:开场→对撞→钩子", "## 其它小节 X\n\n- 条目一\n- 条目二",
                 "## 附录\n\n附录正文 ### 不是标题", "构想正文", "风格正文"):
        assert frag in out2
    assert core.parse_brief(out2)[2]["episode"] == "comedy"
    # 清空节奏后附录仍在
    assert "## 附录" in core.format_brief(brief, style, {}, layout)


def test_rhythm_section_unstructured_text_falls_back_to_custom():
    got = rhythm.parse_section("手写的节奏说明\n第二行")
    assert got["episode"] == "custom" and got["episode_custom"] == "手写的节奏说明\n第二行"
    got = rhythm.parse_section("前言\n\n### 单集节奏:自定义\nA → B")
    assert got["episode"] == "custom" and got["episode_custom"] == "前言\n\nA → B"
