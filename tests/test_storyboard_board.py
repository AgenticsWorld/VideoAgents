"""故事板草图提示词:人物姿态/动作(2026-09-14)——结构化 poses 优先、文字关键词兜底、宫格不裁、机检 pose_present。"""
import json

from modules import storyboard_board as sbb


def test_pose_hint_from_text_keywords():
    hint = sbb.pose_hint({"content": "老妇站在炉前拨火,韩生妻侧卧在炕上;他跪地磕头后跃起挥剑,对方闪身躲开。车站的走廊里俯拍。"})
    assert "standing" in hint and "lying on one side" in hint and "kneeling on the ground" in hint
    assert "swinging a sword" in hint and "dodging aside" in hint
    assert "walking" not in hint            # 车站/走廊/俯拍 被剔除
    assert hint.count("lying") == 1          # 侧卧 命中后同一处的 卧 不再单报
    assert sbb.pose_hint({"content": "He kneels by the door, then runs out."}) == "kneeling, running"
    assert sbb.pose_hint({"content": "窗纸泛黄"}) == ""
    # 否定词紧邻在前的动作不算(liaozhai3 S01-02「不坐起来」曾误报 sitting up)
    assert sbb.pose_hint({"content": "韩生妻不坐起来,只把头挪一点;老妇不再回身,伸手拨火"}) == "reaching out, stirring"


def test_pose_hint_structured_takes_priority():
    shot = {"content": "他站着", "poses": {"CHAR-1": {"pose": "kneel", "action": "挥剑"}, "CHAR-2": {"pose": "sit", "action": ""}}}
    assert sbb.pose_hint(shot, {"CHAR-1": "林昭"}) == "林昭 kneeling, 挥剑; CHAR-2 sitting"


def test_normalize_poses_variants():
    assert sbb.normalize_poses({"CHAR-1": "Sit ", "CHAR-2": {"pose": "LIE", "action": "翻身"}}) == {
        "CHAR-1": {"pose": "sit", "action": ""}, "CHAR-2": {"pose": "lie", "action": "翻身"}}
    assert sbb.normalize_poses([{"id": "CHAR-3", "pose": "prone"}, {"character": "CRE-1", "action": "扬蹄"}]) == {
        "CHAR-3": {"pose": "prone", "action": ""}, "CRE-1": {"pose": "", "action": "扬蹄"}}
    assert sbb.normalize_poses("sit") == {} and sbb.normalize_poses(None) == {}


def test_build_prompt_has_pose_sentence():
    scene = {"location": "土屋", "time_of_day": "深夜"}
    shot = {"size_hint": "中景", "cast": ["CHAR-1"], "content": "她坐在炕沿", "poses": {"CHAR-1": {"pose": "crouch", "action": "拨火"}}}
    prompt, _ = sbb.build_prompt(scene, shot, {"CHAR-1": "韩生妻"})
    assert "Body poses and actions (draw exactly as stated): 韩生妻 crouching, 拨火." in prompt
    assert prompt.index("Characters in frame") < prompt.index("Body poses") < prompt.index("What we see")
    assert "Pose every figure exactly as stated" in sbb.SKETCH_STYLE_PROMPT


def test_grid_prompt_keeps_poses_when_clipped():
    scene = {"scene_no": "S01", "location": "土屋"}
    panels = [(scene, {"key": f"S01-0{i}", "size_hint": "中景", "cast": ["CHAR-1"], "content": "很长的画面描述。" * 200,
                       "sketch": "构图" * 100, "poses": {"CHAR-1": {"pose": "kneel", "action": f"动作{i}"}}}) for i in range(1, 5)]
    prompt, _ = sbb.build_grid_prompt(panels, {"CHAR-1": "林昭"}, 2, 2)
    assert prompt.count("Poses: 林昭 kneeling, 动作") == 4
    assert "…" in prompt      # 画面内容确实被裁了,姿态句没裁


def test_check_poses_rules():
    board = {"scenes": [{"cast": ["CHAR-1", "CHAR-2"], "creatures": ["CRE-1"], "shots": [
        {"key": "S01-01", "cast": ["CHAR-1", "CHAR-2", "CRE-1"], "poses": {"CHAR-1": {"pose": "stand", "action": ""}, "CHAR-9": {"pose": "fly", "action": ""}}},
        {"key": "S01-02", "cast": ["CHAR-1"], "poses": {}},
        {"key": "S01-03", "cast": ["CHAR-1"], "poses": {"CHAR-1": {"pose": "", "action": "挥剑"}}},
    ]}]}
    errs, warns = sbb.check_poses(board)
    assert any("S01-01/CHAR-9: pose 'fly'" in e for e in errs)
    assert any("S01-01/CHAR-2: 出场但 poses 无条目" in e for e in errs)
    assert any("S01-01/CRE-1" in w for w in warns) and any("S01-01/CHAR-9: poses 里的 id" in w for w in warns)
    assert any("S01-02: 缺 poses" in w for w in warns) and not any("S01-02" in e for e in errs)
    assert any("S01-03/CHAR-1: 缺 pose" in e for e in errs)
    errs_strict, _ = sbb.check_poses(board, strict=True)
    assert any("S01-02: 缺 poses" in e for e in errs_strict)


def test_load_board_normalizes_poses(tmp_path):
    base = tmp_path / "p"
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "directing" / "ep01" / "storyboard.json").write_text(json.dumps({"scenes": [{
        "scene_no": "S01", "shots_draft": [
            {"order": 1, "content": "a", "cast": ["CHAR-1"], "poses": {"CHAR-1": {"pose": "Kneel", "action": "磕头"}}},
            {"order": 2, "content": "b", "cast": ["CHAR-1"]}]}]}, ensure_ascii=False))
    board = sbb.load_board(base, "ep01")
    shots = board["scenes"][0]["shots"]
    assert shots[0]["poses"] == {"CHAR-1": {"pose": "kneel", "action": "磕头"}}
    assert shots[1]["poses"] == {}


# ---------------- 旁白挂镜(2026-09-15) ----------------

def _narr(nid, scene, anchor, text="旁白正文", est=5.0):
    return {"id": nid, "scene": scene, "anchor": anchor, "text": text, "est_s": est, "tone": None}


def _board_scenes():
    def shot(key, order, content, final=None, ref=None):
        return {"key": key, "order": order, "content": content, "action": "", "sketch": "", "duration_hint_s": 4.0,
                "final": [{"shot_id": f, "duration_s": 4.0} for f in (final or [])], "narration_ref": ref or []}
    return [
        {"scene_no": "S01", "scene_id": "SCN-0001", "shots": [
            shot("S01-01", 1, "韩生妻侧卧炕上,面朝墙。老妇拨火", ["sh001"]),
            shot("S01-02", 2, "脚步声起,门被推开一线", ["sh002"])]},
        {"scene_no": "S02", "scene_id": "SCN-0002", "shots": [
            shot("S02-01", 1, "堂屋正面,双扇门大开", ["sh003"], ref=["N-03"]),
            shot("S02-02", 2, "她把灯捻小到只剩豆大", ["sh004"])]},
    ]


def test_parse_narration_md_keeps_full_anchor():
    from modules import script_breakdown as sbd
    md = "[N-01 | anchor: S01 | SCN-0018 | ev1000 | 场首「CHAR-0009 侧卧炕上,面朝墙」一镜,脚步声起之前 | est_duration_s: 9.9 | window_min_s: 11.4 | source: ch181#s01]\n正文一句。\n"
    items = sbd.parse_narration_md(md)
    assert len(items) == 1 and items[0]["id"] == "N-01" and items[0]["scene"] == "S01"
    assert items[0]["anchor"] == "S01 | SCN-0018 | ev1000 | 场首「CHAR-0009 侧卧炕上,面朝墙」一镜,脚步声起之前"
    assert items[0]["est_s"] == 9.9 and items[0]["source"] == "ch181#s01" and items[0]["text"] == "正文一句。"


def test_attach_narration_priority_and_fallbacks():
    scenes = _board_scenes()
    narration = [
        _narr("N-01", "S01", "S01 | SCN-0001 | ev1 | 「CHAR-9 侧卧炕上,面朝墙」一镜"),     # 引文相似度 → S01-01
        _narr("N-02", "S01", "S01 | SCN-0001 | ev1 | 场末,转黑场之前"),                    # 无引文,场末 → S01-02
        _narr("N-03", "S02", "S02 | SCN-0002 | ev2 | 「灯捻小」"),                          # 草案镜 narration_ref 优先(S02-01),不按引文落 S02-02
        _narr("N-04", "S02", "S02 | SCN-0002 | ev2 | 「完全不相干的一句话」"),               # 推不出 → 场级未定位
        _narr("N-05", "S09", "S09 | SCN-0009 | ev9 | 「x」"),                                # 场次不存在 → 集级未定位
        _narr("N-06", None, "SCN-0002 | 「双扇门大开」"),                                     # 无场次号,按 SCN id 找场
    ]
    sl = {"narration_anchors": [{"narration_id": "N-02", "anchor_shots": ["sh001", "sh002"]}]}   # shot_list 挂点优先于文字推定
    summ = sbb.attach_narration(scenes, narration, sl, {"CHAR-9": "韩生妻"})
    got = {n["id"]: (sh["key"], n["source"]) for sc in scenes for sh in sc["shots"] for n in sh["narration"]}
    assert got["N-01"] == ("S01-01", "anchor_text") and got["N-03"] == ("S02-01", "storyboard")
    assert got["N-02"] == ("S01-01", "shot_list")
    assert got["N-06"] == ("S02-01", "anchor_text")
    assert [u["id"] for u in scenes[1]["narration_unplaced"]] == ["N-04"]
    assert [u["id"] for u in summ["unplaced"]] == ["N-05"]
    assert summ["items"] == 6 and summ["placed"] == 5 and summ["by_source"] == {"storyboard": 1, "shot_list": 1, "anchor_text": 2, "scene": 1}
    assert scenes[0]["shots"][0]["narration"][0]["text"] == "旁白正文" and scenes[0]["shots"][0]["narration"][0]["est_s"] == 5.0


def test_attach_narration_keeps_unknown_ref_visible():
    scenes = _board_scenes()
    scenes[0]["shots"][1]["narration_ref"] = ["N-99"]
    sbb.attach_narration(scenes, [], {}, {})
    assert scenes[0]["shots"][1]["narration"] == [{"id": "N-99", "text": "", "est_s": None, "anchor": "", "source": "storyboard", "sim": 0.0}]


def test_check_narration_rules():
    scenes = _board_scenes()
    scenes[0]["shots"][0]["narration_ref"] = ["N-01", "N-02"]
    scenes[0]["shots"][1]["narration_ref"] = ["N-01"]           # 同条挂两镜 → WARN
    # S02-01 已引 N-03;N-04 锚在 S01 却挂在 S02 → FAIL
    scenes[1]["shots"][1]["narration_ref"] = ["N-04", "N-77"]   # N-77 不在旁白稿 → FAIL
    narration = [_narr("N-01", "S01", "S01"), _narr("N-02", "S01", "S01", est=30.0), _narr("N-03", "S02", "S02"),
                 _narr("N-04", "S01", "S01"), _narr("N-05", "S02", "S02")]   # N-05 没人引 → 缺省 WARN / strict FAIL
    board = {"scenes": scenes}
    errs, warns = sbb.check_narration(board, narration)
    assert any("S02-02: narration_ref N-77 不在旁白稿里" in e for e in errs)
    assert any("S02-02: N-04 锚在 S01,却挂在 S02" in e for e in errs)
    assert any(w.startswith("N-01: 挂到了 2 镜") for w in warns)
    assert any("N-02 估时 30s" in w for w in warns)
    assert any(w.startswith("N-05: 旁白稿条目没有任何镜引用") for w in warns) and not any("N-05" in e for e in errs)
    errs_s, _ = sbb.check_narration(board, narration, strict=True)
    assert any(e.startswith("N-05:") for e in errs_s)


def test_load_board_attaches_narration_from_md(tmp_path):
    base = tmp_path / "p"
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "story" / "episodes" / "ep01").mkdir(parents=True)
    (base / "directing" / "ep01" / "storyboard.json").write_text(json.dumps({"scenes": [{
        "scene_no": "S01", "shots_draft": [
            {"order": 1, "content": "老妇拨火", "narration_ref": ["N-01"]},
            {"order": 2, "content": "她把灯捻小到只剩豆大"}]}]}, ensure_ascii=False))
    (base / "story" / "episodes" / "ep01" / "narration.md").write_text(
        "# 旁白\n\n[N-01 | anchor: S01 | 场首 | est_duration_s: 3.0]\n夜深了。\n\n[N-02 | anchor: S01 | 「灯捻小到只剩豆大」一镜 | est_duration_s: 4.0]\n灯灭之前,她想起一件事。\n")
    board = sbb.load_board(base, "ep01")
    shots = board["scenes"][0]["shots"]
    assert [(n["id"], n["source"], n["text"]) for n in shots[0]["narration"]] == [("N-01", "storyboard", "夜深了。")]
    assert [(n["id"], n["source"]) for n in shots[1]["narration"]] == [("N-02", "anchor_text")]
    assert board["narration"]["items"] == 2 and board["narration"]["total_est_s"] == 7.0


def test_notes_roundtrip(tmp_path):
    """用户注释(2026-09-15):整集 * / 场次 S01 / 镜 S01-03 三级键,空文本删除,全空删文件,坏键拒收。"""
    base = tmp_path
    assert sbb.load_notes(base, "ep01")["notes"] == {}
    d = sbb.update_note(base, "ep01", "S01-03", "  这一镜要仰拍,保留门框前景  ", {"scene_no": "S01", "order": 3, "content": "林昭推门"})
    rec = d["notes"]["S01-03"]
    assert rec["text"] == "这一镜要仰拍,保留门框前景" and rec["level"] == "shot" and rec["order"] == 3 and rec["content"] == "林昭推门"
    assert rec["created_at"] and rec["updated_at"]
    sbb.update_note(base, "ep01", "*", "整集节奏放慢")
    sbb.update_note(base, "ep01", "S01", "本场多用中景")
    saved = json.loads(sbb.notes_path(base, "ep01").read_text())
    assert saved["schema"] == sbb.NOTES_SCHEMA and set(saved["notes"]) == {"S01-03", "*", "S01"} and "_readme" in saved
    assert saved["notes"]["*"]["level"] == "episode" and saved["notes"]["S01"]["level"] == "scene"
    # 改文本保留 created_at,清空删条目,全清删文件
    created = rec["created_at"]
    d = sbb.update_note(base, "ep01", "S01-03", "改成平视")
    assert d["notes"]["S01-03"]["text"] == "改成平视" and d["notes"]["S01-03"]["created_at"] == created
    sbb.update_note(base, "ep01", "S01-03", "")
    assert "S01-03" not in sbb.load_notes(base, "ep01")["notes"]
    sbb.update_note(base, "ep01", "*", "")
    sbb.update_note(base, "ep01", "S01", "   ")
    assert not sbb.notes_path(base, "ep01").exists() and sbb.load_notes(base, "ep01")["notes"] == {}
    import pytest
    with pytest.raises(ValueError):
        sbb.update_note(base, "ep01", "../x", "bad")
    with pytest.raises(ValueError):
        sbb.update_note(base, "ep01", "S01", "x" * (sbb.NOTE_MAX_CHARS + 1))
    assert sbb.note_key_ok("*") and sbb.note_key_ok("S01-03") and not sbb.note_key_ok("") and not sbb.note_key_ok("S01/03")


def test_note_meta_from_board():
    board = {"title": "初入", "scenes": [{"scene_no": "S01", "scene_id": "SCN-0012", "location": "大殿",
                                          "shots": [{"key": "S01-01", "order": 1, "shot_id": "S01-D01", "content": "林昭推门" * 30}]}]}
    assert sbb.note_meta(board, "*") == {"title": "初入"}
    assert sbb.note_meta(board, "S01") == {"scene_no": "S01", "scene_id": "SCN-0012", "location": "大殿"}
    m = sbb.note_meta(board, "S01-01")
    assert m["scene_no"] == "S01" and m["order"] == 1 and m["shot_id"] == "S01-D01" and len(m["content"]) == 80
    assert sbb.note_meta(board, "S09-09") == {}
