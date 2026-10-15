from modules.entity_ids import is_creature_id


def test_creature_prefix_both_styles():
    assert is_creature_id("CRE-0001")
    assert is_creature_id("cre_bishuishou")
    assert is_creature_id("cre-aobing")
    assert is_creature_id("Cre_x")


def test_non_creatures():
    assert not is_creature_id("CHAR-0007")
    assert not is_creature_id("creature_x")
    assert not is_creature_id("crew")
    assert not is_creature_id(None)
    assert not is_creature_id(12)


# ---------------------------------------------------------------- #117 人物 / 生物编号提取
from modules.entity_ids import extract_actor_id, is_actor_id, known_actor_ids, normalize_actor_id  # noqa: E402


def test_numeric_ids_stop_at_digits_like_before():
    """数字编号只取到数字段:与 2026-10-08 之前的 CHAR-\\d+ 逐字同口径(liaozhai 车夫甲 CHAR-0093-v1 回归)。"""
    assert extract_actor_id("车夫甲(CHAR-0093-v1)") == "CHAR-0093"
    assert extract_actor_id("S01/CHAR-0002:你好") == "CHAR-0002"
    assert extract_actor_id("CHAR-0001abc") == "CHAR-0001"
    assert extract_actor_id("哮天犬 CRE-0002") == "CRE-0002"
    assert extract_actor_id("哮天犬 CRE-0002", char_only=True) is None
    assert extract_actor_id("路人") is None and extract_actor_id(None) is None and extract_actor_id("CHAR-") is None


def test_slug_ids_and_known_prefix():
    known = frozenset({"CHAR-alice", "CHAR-alice-sister", "CHAR-jie-rui-er"})
    assert extract_actor_id("Jerril (CHAR-jie-rui-er):") == "CHAR-jie-rui-er"
    assert extract_actor_id("（CHAR-hari-seldon）说") == "CHAR-hari-seldon"          # 中文 / 全角括号截断
    assert extract_actor_id("CHAR-alice_young_voiceprint") == "CHAR-alice"            # 下划线截断
    assert extract_actor_id("Alice (CHAR-alice-v1)") == "CHAR-alice-v1"               # 不给已知编号:原样
    assert extract_actor_id("Alice (CHAR-alice-v1)", known) == "CHAR-alice"           # 最长已知前缀
    assert extract_actor_id("CHAR-alice-sister", known) == "CHAR-alice-sister"
    assert normalize_actor_id("CHAR-nobody-v1", known) == "CHAR-nobody-v1"
    assert is_actor_id("CHAR-jie-rui-er") and is_actor_id("CRE-0001") and not is_actor_id("CHAR-0093-v1")
    assert not is_actor_id("CRE-0001", char_only=True) and not is_actor_id("Jerril")


def test_known_actor_ids(tmp_path):
    import json
    c = tmp_path / "bible" / "characters"
    (c / "CHAR-alice").mkdir(parents=True)
    (c / "notes").mkdir()
    (c / "index.json").write_text(json.dumps({"characters": [{"id": "CHAR-alice-sister"}, {"name": "无编号"}]}))
    cr = tmp_path / "bible" / "creatures"
    cr.mkdir(parents=True)
    (cr / "index.json").write_text(json.dumps({"creatures": [{"id": "cre_bishuishou"}]}))
    assert known_actor_ids(tmp_path) == {"CHAR-alice", "CHAR-alice-sister", "cre_bishuishou"}
    assert known_actor_ids(tmp_path / "missing") == frozenset()


def test_timbre_selector_character_key():
    """#117:本地音色库按输出文件名推人物编号,slug 编号不再落空(否则被当旁白挑音色);CHAR_0001 下划线旧写法照旧。"""
    from modules import timbre_selector as ts
    assert ts._normalize_character("CHAR_0001_voiceprint.mp3") == "CHAR-0001"
    assert ts._normalize_character("char-0002") == "CHAR-0002"
    assert ts._normalize_character("CHAR-0093-v1_voiceprint") == "CHAR-0093"
    assert ts._normalize_character("x/CHAR-alice-sister_young_voiceprint.wav") == "CHAR-alice-sister"
    assert ts._normalize_character("narrator.mp3") == ""
