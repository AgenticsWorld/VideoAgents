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


def test_sketch_text_only_for_comfyui_drops_ref_sentence(monkeypatch):
    """2026-09-17 用户拍板:渠道 comfyui 没配收参考图的图生图时出草图走纯文生图,提示词不再说「附图是人物设定」
    (2026-09-19:改为按图生图配置判定,见 test_sketch_refs_follow_i2i_capacity)。"""
    import modules.genmedia as gm
    monkeypatch.setattr(gm, "image_ref_capacity", lambda cfg: 0)
    assert sbb.sketch_text_only("comfyui", {}) and sbb.sketch_text_only("ComfyUI", {})
    assert not sbb.sketch_text_only("volcengine") and not sbb.sketch_text_only("")
    scene = {"scene_no": "S01", "location": "居室", "time": "夜"}
    shot = {"key": "S01-01", "order": 1, "cast": ["CHAR-1"], "content": "她抬头", "size_hint": "近景"}
    with_refs, _ = sbb.build_prompt(scene, shot, {"CHAR-1": "韩生妻"})
    no_refs, _ = sbb.build_prompt(scene, shot, {"CHAR-1": "韩生妻"}, with_refs=False)
    assert "attached images" in with_refs and "attached images" not in no_refs
    assert "drawn from the text description" in no_refs and "韩生妻" in no_refs
    assert "standing, sitting" not in no_refs and "panel" not in no_refs.split("Shot size")[0]


def test_sketch_text_only_for_agentics_keeps_grid_but_drops_refs(monkeypatch):
    """2026-09-18 用户拍板:agentics 没配图生图时出草图走文生图(不传参考图);宫格照出,只有 comfyui 退化为单张。"""
    import modules.genmedia as gm
    monkeypatch.setattr(gm, "image_ref_capacity", lambda cfg: 0)
    assert sbb.sketch_text_only("agentics", {}) and not sbb.sketch_single_only("agentics")
    assert sbb.sketch_text_only("comfyui", {}) and sbb.sketch_single_only("comfyui")
    assert not sbb.sketch_single_only("volcengine")
    scene = {"scene_no": "S01", "location": "居室", "time": "夜"}
    panels = [(scene, {"key": "S01-01", "order": 1, "cast": ["CHAR-1"], "content": "她抬头", "size_hint": "近景"}),
              (scene, {"key": "S01-02", "order": 2, "cast": ["CHAR-1"], "content": "她起身", "size_hint": "中景"})]
    with_refs, _ = sbb.build_grid_prompt(panels, {"CHAR-1": "韩生妻"}, 2, 2)
    no_refs, _ = sbb.build_grid_prompt(panels, {"CHAR-1": "韩生妻"}, 2, 2, with_refs=False)
    assert "attached images" in with_refs and "attached images" not in no_refs
    assert "drawn from the text description" in no_refs and "韩生妻" in no_refs
    assert "2x2 grid" in no_refs and "stay blank white" in no_refs


def test_sketch_refs_follow_i2i_capacity(monkeypatch, tmp_path):
    """2026-09-19 用户拍板:comfyui / agentics 只有配置了收参考图的图生图才传人物 sheet,张数以其容量封顶;
    带参考图时用正向风格句 + 逐张点名 + 单实例句。"""
    import modules.genmedia as gm
    monkeypatch.setattr(gm, "image_ref_capacity", lambda cfg: 2)
    assert sbb.sketch_ref_capacity("comfyui", {}) == 2 and not sbb.sketch_text_only("agentics", {})
    assert sbb.sketch_ref_capacity("volcengine") is None
    for i in (1, 2, 3):
        (tmp_path / f"c{i}.png").write_bytes(b"x")
    catalog = {"characters": {f"CHAR-{i}": {"file": f"c{i}.png"} for i in (1, 2, 3)}, "creatures": {}}
    shot = {"key": "S01-01", "order": 1, "cast": ["CHAR-1", "CHAR-2", "CHAR-3"], "content": "三人对峙", "size_hint": "中景"}
    assert sbb.ref_cast_ids(tmp_path, shot, catalog, 2) == ["CHAR-1", "CHAR-2"]
    assert sbb.ref_cast_ids(tmp_path, shot, catalog) == ["CHAR-1", "CHAR-2", "CHAR-3"]
    prompt, _ = sbb.build_prompt({"scene_no": "S01"}, shot, {}, plain_style=True, ref_names=["甲", "乙"])
    head = prompt.split("Shot size")[0]
    assert "Picture 1 is 甲; Picture 2 is 乙" in head and "appears exactly once" in head
    assert "standing, sitting" not in head and "drawn from the text description" not in head


def test_rh_image_ref_slots_counts_loadimages_and_rejects_img2img():
    import modules.genmedia as gm
    base = {"3": {"class_type": "KSampler", "inputs": {"latent_image": ["5", 0], "positive": ["6", 0]}},
            "5": {"class_type": "EmptySD3LatentImage", "inputs": {"width": 1024, "height": 1024}},
            "6": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"prompt": "", "image1": ["7", 0], "image2": ["8", 0]}},
            "7": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
            "8": {"class_type": "LoadImage", "inputs": {"image": "b.png"}}}
    assert gm._rh_image_ref_slots(base) == 2
    img2img = {**base, "5": {"class_type": "VAEEncode", "inputs": {"pixels": ["7", 0]}}}
    assert gm._rh_image_ref_slots(img2img) == 0


def test_clean_prose_drops_production_metadata():
    """2026-09-29:【】段落、时间码分句、交组/续写类制作用语剔除,「A→B」只留起点。"""
    t = sbb.clean_prose("【长镜头前段·镜内分段 0–7s 起幅】日落后天幕冷青,焦段 200→85mm;7s 起匀速降落推轨;"
                        "组尾停在约 85mm(交组B 续写);近景,受力反馈=弦震与扬尘")
    assert "【" not in t and "7s" not in t and "交组" not in t and "受力反馈" not in t
    assert "日落后天幕冷青" in t and "焦段 200mm" in t


def test_size_lens_horizon_rules():
    assert sbb.size_rule({"size_hint": "大远景 → 远景推轨"}).startswith("extreme wide shot")
    assert sbb.size_rule({"size_hint": "中近景"}).startswith("medium close-up")
    assert sbb.size_rule({"size_hint": "MCU"}).startswith("medium close-up")
    assert sbb.size_rule({"size_hint": "over the head"}) == "over the head"
    # 时间码分句里的落幅焦段不算,取起幅
    assert sbb.lens_rule({"sketch": "22s 起焦段稳在 50mm 平视", "content": "焦段 85→50mm"}).startswith("85mm telephoto")
    assert sbb.lens_rule({"content": "24mm 贴地"}).startswith("24mm ultra-wide")
    assert sbb.lens_rule({"content": "没写焦段"}) == ""
    assert "high in the frame" in sbb.horizon_rule("high angle, profile")
    assert sbb.horizon_rule("profile") == ""


def test_build_prompt_panel_en_screen_direction_and_negative():
    scene = {"location": "敌楼", "time_of_day": "黄昏", "screen_direction": "Nezha holds screen left"}
    shot = {"cast": ["CHAR-1"], "size_hint": "近景", "content": "【分段 0–2s】她看向画右",
            "panel_en": "Nezha on screen left facing right."}
    prompt, neg = sbb.build_prompt(scene, shot, {"CHAR-1": "哪吒"})
    assert "What we see (start frame): Nezha on screen left facing right." in prompt
    assert "她看向画右" not in prompt          # 有 panel_en 不再拼中文散文
    assert "Screen direction for this scene" in prompt and "close shot" in prompt
    assert "scenery without people" in neg
    _, neg_empty = sbb.build_prompt(scene, {"content": "空镜,城楼"}, {})
    assert "scenery without people" not in neg_empty    # 空镜/定场不压人
    # --panel 覆盖(台账 _panel)优先于分镜层 panel_en
    p2, _ = sbb.build_prompt(scene, dict(shot, _panel="Override."), {"CHAR-1": "哪吒"})
    assert "(start frame): Override." in p2


def test_build_prompt_whitebox_layout_sentence():
    prompt, _ = sbb.build_prompt({}, {"cast": ["CHAR-1"]}, {"CHAR-1": "哪吒"}, layout=True,
                                 whitebox_legend=[("哪吒", "red")])
    assert "3D blocking render" in prompt and "the red mannequin is 哪吒" in prompt
    assert "hand-drawn layout" not in prompt
    grid, _ = sbb.build_grid_prompt([({"scene_no": "S01"}, {"cast": ["CHAR-1"]}), ({"scene_no": "S01"}, {})],
                                    {"CHAR-1": "哪吒"}, 2, 2, whitebox_legend=[])
    assert "same 2x2 grid" in grid and "mannequin is" not in grid


def test_sketch_style_packs_konte(tmp_path):
    """2026-09-30 画风包:konte 为铅笔絵コンテ(允许少量茶色、不压 anime),film 仍为默认;非法值回落 film。"""
    shot = {"cast": ["CHAR-1"], "size_hint": "近景"}
    film, film_neg = sbb.build_prompt({}, shot, {"CHAR-1": "甲"})
    konte, konte_neg = sbb.build_prompt({}, shot, {"CHAR-1": "甲"}, style="konte")
    assert film.startswith("Professional live-action film storyboard") and "anime" in film_neg
    assert konte.startswith("Japanese animation production storyboard (e-konte)") and "sepia" in konte
    assert "anime" not in konte_neg and "handwriting" in konte_neg
    assert "Ghibli" not in konte and "Miyazaki" not in konte
    # 纯文生图 / plain_style 同样按画风切换,且仍以 TEXT_ONLY_TAIL 结尾可截
    t_only, _ = sbb.build_prompt({}, shot, {}, with_refs=False, style="konte")
    assert t_only.startswith("One Japanese animation production storyboard")
    plain, _ = sbb.build_prompt({}, shot, {}, plain_style=True, ref_names=["甲"], style="konte")
    assert "Picture 1 is 甲" in plain and "sepia" in plain
    grid, gneg = sbb.build_grid_prompt([({"scene_no": "S01"}, shot), ({"scene_no": "S01"}, {})], {}, 2, 2, style="konte")
    assert grid.startswith("A page from a Japanese animation production storyboard") and "handwriting" in gneg
    assert sbb.normalize_sketch_style("bogus") == "film"
    assert sbb.resolve_sketch_style(tmp_path) == "film"
    (tmp_path / "settings.json").write_text(json.dumps({"output": {"sketch_style": "konte"}}))
    assert sbb.resolve_sketch_style(tmp_path) == "konte"


def test_sketch_style_packs_ink_digital():
    """2026-09-30:动作分镜(粗犷线稿,纯黑白)/ 数字分镜(灰调 + 单一重点色)两套画风包齐全,纯文生图版无否定句。"""
    shot = {"cast": ["CHAR-1"]}
    ink, ink_neg = sbb.build_prompt({}, shot, {}, style="ink")
    dig, dig_neg = sbb.build_prompt({}, shot, {}, style="digital")
    assert ink.startswith("live-action action-movie storyboard panel") and "cross hatching" in ink and "grey wash" in ink_neg
    assert "at most one accent color" in dig and "Accent color: none" in dig and "multiple accent colors" in dig_neg
    for st in ("ink", "digital"):
        t_only, _ = sbb.build_prompt({}, shot, {}, with_refs=False, style=st)
        head = t_only[:-len(sbb.SKETCH_TEXT_ONLY_TAIL)]
        assert " no " not in head.lower() and " not " not in head.lower()
        grid, _ = sbb.build_grid_prompt([({"scene_no": "S01"}, shot), ({"scene_no": "S01"}, {})], {}, 2, 2, style=st)
        assert "strict 2x2 grid" in grid
    assert set(sbb.SKETCH_STYLE_LABELS) == set(sbb.SKETCH_STYLES) == {"film", "konte", "ink", "digital"}


def test_screen_direction_drops_motion_not_in_shot():
    scene = {"screen_direction": "R1:红线自画左→画右斜穿、从画右偏上出画(T1 前半);西面开口在画右"}
    look = {"content": "哪吒探身出开口去看——什么都没有了。", "cast": ["CHAR-1"]}
    arrow = {"content": "一道红线自画左向画右斜穿,从画右偏上出画"}
    assert sbb.screen_direction(scene, [look], {"CHAR-1": "哪吒"}) == "西面开口在画右"
    assert "从画右偏上出画" in sbb.screen_direction(scene, [arrow]) and "红线" in sbb.screen_direction(scene, [arrow])
    assert "红线" in sbb.screen_direction(scene)          # 不传 shots = 原文
    p, _ = sbb.build_prompt(scene, look, {"CHAR-1": "哪吒"})
    assert "红线" not in p and "西面开口在画右" in p
    g, _ = sbb.build_grid_prompt([(dict(scene, scene_no="S02"), look), (dict(scene, scene_no="S02"), arrow)], {}, 2, 2)
    assert "红线" in g                                    # 宫格按本场各格并集


def test_digital_accent_per_shot():
    shot = {"content": "哪吒张望"}
    p, _ = sbb.build_prompt({}, shot, {}, style="digital")
    assert "Accent color: none" in p and "exactly one accent color" not in p
    p, _ = sbb.build_prompt({}, dict(shot, accent={"element": "the arrow streak", "color": "red"}), {}, style="digital")
    assert "Accent color: red, used only on the arrow streak" in p
    assert sbb.accent_of({"accent": "none"}) is None and sbb.accent_of({"accent": "箭光|red"}) == ("箭光", "red")
    g, _ = sbb.build_grid_prompt([({"scene_no": "S01"}, shot), ({"scene_no": "S01"}, {})], {}, 2, 2, style="digital")
    assert "Accent: none" in g
    film, _ = sbb.build_prompt({}, shot, {})
    assert "Accent" not in film


# ---------------- 空镜与画外条目(#105) ----------------

def test_explicit_empty_characters_means_no_people(tmp_path):
    """镜草案显式写 characters: [] = 本镜无人,不回退到组草案人物;没写该键的镜照旧回退。"""
    base = tmp_path / "p"
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "directing" / "ep01" / "storyboard.json").write_text(json.dumps({"scenes": [{
        "scene_no": "S01",
        "groups_draft": [{"characters": ["CHAR-1", "CHAR-2"], "shot_orders": [1, 2]}],
        "shots_draft": [
            {"order": 1, "content": "空庭,落叶", "characters": []},
            {"order": 2, "content": "两人对坐"}]}]}, ensure_ascii=False))
    shots = sbb.load_board(base, "ep01")["scenes"][0]["shots"]
    assert shots[0]["cast"] == []
    assert shots[1]["cast"] == ["CHAR-1", "CHAR-2"]


def test_offscreen_pose_entries_stay_out_of_sketch_prompt(tmp_path):
    """poses 里 action 以「画外」开头的人物不进出场句、姿态句,也不挂参考图。"""
    names = {"CHAR-1": "哪吒", "CHAR-2": "李靖"}
    shot = {"size_hint": "特写", "cast": ["CHAR-1", "CHAR-2"], "content": "案上的令箭",
            "poses": {"CHAR-1": {"pose": "stand", "action": "握拳"}, "CHAR-2": {"pose": "stand", "action": "画外:只闻其声"}}}
    assert sbb.frame_cast(shot) == ["CHAR-1"]
    prompt, _ = sbb.build_prompt({"location": "大殿"}, shot, names)
    assert "Characters in frame: 哪吒." in prompt and "李靖" not in prompt
    (tmp_path / "a.png").write_bytes(b"x"); (tmp_path / "b.png").write_bytes(b"x")
    catalog = {"characters": {"CHAR-1": {"file": "a.png"}, "CHAR-2": {"file": "b.png"}}, "creatures": {}}
    assert sbb.ref_cast_ids(tmp_path, shot, catalog) == ["CHAR-1"]
    empty = {"size_hint": "空镜", "cast": [], "content": "空庭,有人影晃动",
             "poses": {"CHAR-2": {"pose": "stand", "action": "画外:踱步"}}}
    prompt, negative = sbb.build_prompt({"location": "庭院"}, empty, names)
    assert "Characters in frame" not in prompt and "Body poses" not in prompt
    assert sbb.SKETCH_NEGATIVE_PEOPLE_TAIL not in negative
