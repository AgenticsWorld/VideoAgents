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
