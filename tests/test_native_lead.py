# -*- coding: utf-8 -*-
"""原生先入 modules/native_lead.py(三期):前提判定 / 自动建议 / 写回 / prompt 同步往返 / 机检(离线;模型族用 monkeypatch 固定)。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT))

from modules import native_lead as nl  # noqa: E402
from modules.shot_timing import KIND_SD25  # noqa: E402


def _proj(tmp_path: Path, sound_split="auto", voice="native") -> Path:
    base = tmp_path / "p"
    (base / "directing" / "ep01" / "shots" / "s3").mkdir(parents=True)
    (base / "assets" / "prompts" / "ep01").mkdir(parents=True)
    (base / "settings.json").write_text(json.dumps({"output": {"sound_split": sound_split, "dialogue_voice": voice}}), encoding="utf-8")
    sl = {"shots": [
        {"shot_id": "s1", "duration_s": 3.0, "size": "全景", "characters": [], "dialogue_lines": []},
        {"shot_id": "s2", "duration_s": 1.0, "size": "近景(主观)", "characters": [], "dialogue_lines": []},
        {"shot_id": "s3", "duration_s": 2.4, "size": "近景", "characters": ["CHAR-0001", "CHAR-0002"],
         "dialogue_lines": [{"speaker": "CHAR-0001", "text": "你射死我门人,还推不知?", "est_duration_s": 2.37},
                            {"speaker": "CHAR-0001", "text": "说!", "est_duration_s": 0.7}]},
        {"shot_id": "s4", "duration_s": 0.8, "size": "特写", "characters": ["CHAR-0002"], "dialogue_lines": []},
        {"shot_id": "s5", "duration_s": 3.0, "size": "近景", "characters": ["CHAR-0002"],
         "dialogue_lines": [{"speaker": "CHAR-0002", "text": "弟子不敢", "est_duration_s": 1.2}]},
        {"shot_id": "s6", "duration_s": 2.0, "size": "近景", "characters": ["CHAR-0001"],
         "dialogue_lines": [{"speaker": "CHAR-0001", "text": "你若查不出来,我问你师父要你!", "est_duration_s": 3.0}]},
    ], "generation_groups": [
        {"group_id": "grp001", "shots": ["s1", "s2", "s3", "s4", "s5"], "audio_plan": "dialogue",
         "blocking_map": {"characters": [{"id": "CHAR-0001", "label": "石矶"}, {"id": "CHAR-0002", "label": "李靖"}]}},
        {"group_id": "grp002", "shots": ["s6"], "audio_plan": "dialogue"}],
        "narration_anchors": []}
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False, indent=2), encoding="utf-8")
    (base / "directing" / "ep01" / "shots" / "s3" / "blocking.json").write_text(json.dumps({"characters": [
        {"id": "CHAR-0001", "performance": {"trigger": {"word": "还推不知"}}}]}), encoding="utf-8")
    return base


@pytest.fixture(autouse=True)
def _sd25(monkeypatch):
    monkeypatch.setattr(nl, "group_kind", lambda base, ep, gid: KIND_SD25)
    monkeypatch.setattr(nl, "ui_lang_is_zh", lambda: True)


def test_lead_split_prefers_punctuation():
    assert nl.lead_split("你射死我门人,还推不知?", 0.9, 2.37) == ("你射死我门人,", "还推不知?")
    lead, rest = nl.lead_split("原来是你啊真的是你", 0.4, 2.0)
    assert lead + rest == "原来是你啊真的是你" and 2 <= len(lead) <= 6
    assert nl.lead_seconds(1.0, 2.37) == pytest.approx(0.948) and nl.lead_seconds(0.6, 5.0) == 0.6


def test_suggest_and_prereqs(tmp_path):
    base = _proj(tmp_path)
    sugg = nl.suggest(base, "ep01")
    assert [(s["shot_id"], s["prev"], s["trigger"]) for s in sugg] == [("s3", "s2", "N1")]   # s5 的前镜 s4 只有听者但句无句读 → 不建议;s6 组首镜
    assert sugg[0]["lead"] == "你射死我门人," and sugg[0]["s"] == pytest.approx(0.948)
    # 关掉 auto / 后期配音 → 不建议
    assert nl.suggest(_proj(tmp_path / "b", sound_split="script_only"), "ep01") == []
    assert nl.suggest(_proj(tmp_path / "c", voice="dubbing"), "ep01") == []


def test_set_line_rejects_bad_prereqs(tmp_path):
    base = _proj(tmp_path)
    with pytest.raises(ValueError):
        nl.set_line(base, "ep01", "s3", 1)          # 不是本镜第一句
    with pytest.raises(ValueError):
        nl.set_line(base, "ep01", "s6", 0)          # 组首镜
    with pytest.raises(ValueError):
        nl.set_line(base, "ep01", "s3", 0, s=1.4)   # 超过前镜 / 上限
    ln = nl.set_line(base, "ep01", "s3", 0, reason="手动")
    assert ln["native_lead"]["shot"] == "s2" and ln["native_lead"]["reason"]["trigger"] == "U" and ln["native_lead"]["source"] == "user"
    assert nl.validate(base, "ep01") == []
    # P8:先入文本含触发词 → validate 报
    sl = json.loads((base / "directing" / "ep01" / "shot_list.json").read_text(encoding="utf-8"))
    (base / "directing" / "ep01" / "shots" / "s3" / "blocking.json").write_text(json.dumps({"characters": [
        {"id": "CHAR-0001", "performance": {"trigger": {"word": "门人"}}}]}), encoding="utf-8")
    assert any("P8" in m for m in nl.validate(base, "ep01", sl))
    # 模式关 → validate 报
    (base / "settings.json").write_text(json.dumps({"output": {"sound_split": "off"}}), encoding="utf-8")
    assert any("声画分离=off" in m for m in nl.validate(base, "ep01"))


def _prompt(split: bool) -> str:
    s3 = ("Shot 3: 近景。石矶坐着:{你射死我门人,}前半句字头咬得实。说到『还推不知』时眉心亮:{还推不知?}问完后唇线压平。" if split
          else "Shot 3: 近景。石矶坐着说:{你射死我门人,还推不知?}问完后唇线压平。")
    return ("Overall visual style: x。\n\nShot 1: 全景。\n\nShot 2: 李靖主观镜,箭在地上滚。<滚动声>\n\n" + s3 +
            "\n\nShot 4: 李靖手部特写。\n\nShot 5: 李靖:{弟子不敢}\n\nGlobal constraints: no music.")


@pytest.mark.parametrize("split", [False, True])
def test_sync_roundtrip(tmp_path, split):
    base = _proj(tmp_path)
    pp = base / "assets" / "prompts" / "ep01" / "grp001.json"
    pp.write_text(json.dumps({"video_prompt": _prompt(split)}, ensure_ascii=False), encoding="utf-8")
    orig = _prompt(split)
    nl.set_line(base, "ep01", "s3", 0, reason={"trigger": "N1", "evidence": "x"}, source="directing")
    res = nl.sync_episode(base, "ep01", write=False)
    assert any("native_lead_bound" in e for e in res["errors"])        # 未写 = 机检 FAIL
    res = nl.sync_episode(base, "ep01", write=True)
    assert res["errors"] == [] and res["updated_prompts"] == ["grp001"]
    d = json.loads(pp.read_text(encoding="utf-8"))
    vp = d["video_prompt"]
    s2 = vp[vp.index("Shot 2"):vp.index("Shot 3")]
    s3 = vp[vp.index("Shot 3"):vp.index("Shot 4")]
    assert "【原生先入】" in s2 and "{你射死我门人,}" in s2 and "石矶" in s2
    assert "{还推不知?}" in s3 and "{你射死我门人,还推不知?}" not in s3 and s3.count("{你射死我门人,}") == 0 and "不再重说" in s3
    assert d["native_leads"][0]["mode"] == ("split" if split else "whole")
    # 幂等
    vp1 = vp
    res = nl.sync_episode(base, "ep01", write=True)
    assert res["errors"] == [] and json.loads(pp.read_text(encoding="utf-8"))["video_prompt"] == vp1
    # 清掉 → 精确还原
    nl.set_line(base, "ep01", "s3", 0, on=False)
    res = nl.sync_episode(base, "ep01", write=True)
    d2 = json.loads(pp.read_text(encoding="utf-8"))
    assert res["errors"] == [] and d2["native_leads"] == [] and d2["video_prompt"].replace("\n\n", "\n").strip() == orig.replace("\n\n", "\n").strip()
    # 残留标记句 → bound FAIL
    d2["video_prompt"] = d2["video_prompt"] + " 【原生先入】残留。"
    pp.write_text(json.dumps(d2, ensure_ascii=False), encoding="utf-8")
    assert any("残留" in e for e in nl.sync_episode(base, "ep01", write=False)["errors"])


def test_check_prompt_detects_duplicate_full_line():
    vp = ("Shot 1: a\n\nShot 2: 【原生先入】本镜起即由石矶在画外开口:{你射死我门人,}——说话人石矶不在画内,画面不出现任何多出的人物,话未说完即切到下一镜。\n\n"
          "Shot 3: {你射死我门人,还推不知?} 【原生先入】本镜开场时石矶正说到一半,上一镜画外已说出的「你射死我门人,」不再重说,接着说完。")
    errs = nl.check_prompt(vp, prev_no=2, shot_no=3, full="你射死我门人,还推不知?", lead="你射死我门人,", rest="还推不知?", h3=False)
    assert any("缺 {还推不知?}" in e for e in errs) and any("会重说" in e for e in errs)


# ---------------------------------------------------------------- #155 句末标点 / 破折号可切、英文按词取整、已写入记录不漂移

def test_lead_split_sentence_end_dash_and_abbreviations():
    assert nl.lead_split("Wait. Come here.", 0.5, 2.0) == ("Wait.", "Come here.")
    assert nl.lead_split("放回去。快走！", 0.5, 1.6) == ("放回去。", "快走！")
    assert nl.lead_split("放回去!快走!", 0.5, 1.6) == ("放回去!", "快走!")          # 中文台词里的半角感叹号
    assert nl.lead_split("Over here — get down now!", 0.5, 2.0) == ("Over here —", "get down now!")
    assert nl.lead_split("Over here--get down now!", 0.5, 2.0) == ("Over here--", "get down now!")
    assert nl.lead_split("Dr. Who? He left.", 0.5, 2.0) == ("Dr. Who?", "He left.")      # Dr. 不切
    assert nl.lead_split("Mr. Lee, come in now please.", 0.5, 2.0) == ("Mr. Lee,", "come in now please.")
    assert nl.lead_split("No. Not now.", 0.5, 2.0) == ("No.", "Not now.")                # 单说 No. 是一句
    assert nl.lead_split("No. 5. Go there right now.", 0.5, 2.0) == ("No. 5.", "Go there right now.")   # No. 5 是编号
    assert nl.punct_cut("Mr. Smith is here now") is None
    # 省略号不是切点
    assert nl.punct_cut("Wait... you came back for me") is None and nl.punct_cut("Wait… you came back for me") is None
    # 「首分句 ≤60%」上限保留:破折号太靠后 → 按词取整
    assert nl.punct_cut("This way — go!") is None
    assert nl.lead_split("This way — go!", 0.5, 2.0) == ("This", "way — go!")
    # 先入去尾部空白
    assert nl.lead_split("This way — against them! Keep your head down!", 1.0, 3.0) == ("This way —", "against them! Keep your head down!")


@pytest.mark.parametrize("s", [0.2, 0.45, 0.7, 1.0, 1.6])
def test_lead_split_word_text_snaps_to_word_boundary(s):
    text = "It opens on purpose and nobody knows why"          # 无句读英文:按比例切也落在词边界
    lead, rest = nl.lead_split(text, s, 3.0)
    assert lead and rest and lead == lead.rstrip() and text == lead + " " + rest
    assert nl.lead_split("原来是你啊真的是你", 0.4, 2.0)[0] == "原来"                     # 汉字句仍按字比例切(口径不变)


def _set_text(base: Path, sid: str, text: str, est: float) -> None:
    p = base / "directing" / "ep01" / "shot_list.json"
    sl = json.loads(p.read_text(encoding="utf-8"))
    for sh in sl["shots"]:
        if sh["shot_id"] == sid:
            sh["dialogue_lines"][0].update(text=text, est_duration_s=est)
    p.write_text(json.dumps(sl, ensure_ascii=False, indent=2), encoding="utf-8")


def test_suggest_accepts_sentence_end_and_dash(tmp_path):
    for i, (text, lead) in enumerate([("放回去。快走！", "放回去。"), ("Over here — get down now!", "Over here —"),
                                      ("Wait. Come here, quickly.", "Wait."), ("This way — go!", None), ("Wait... you came back", None)]):
        base = _proj(tmp_path / f"c{i}")
        _set_text(base, "s3", text, 2.0)
        sugg = [x for x in nl.suggest(base, "ep01") if x["shot_id"] == "s3"]
        assert [x["lead"] for x in sugg] == ([lead] if lead else []), text


def _prompt_line(text: str) -> str:
    return ("Overall visual style: x。\n\nShot 1: 全景。\n\nShot 2: 李靖主观镜,箭在地上滚。\n\nShot 3: 近景。石矶:{" + text + "}说完转身。"
            "\n\nShot 4: 李靖手部特写。\n\nShot 5: 李靖:{弟子不敢}\n\nGlobal constraints: no music.")


def test_recorded_split_kept_when_full_line_unchanged(tmp_path, monkeypatch):
    base = _proj(tmp_path)
    text = "放回去。快走！"
    _set_text(base, "s3", text, 1.6)
    pp = base / "assets" / "prompts" / "ep01" / "grp001.json"
    pp.write_text(json.dumps({"video_prompt": _prompt_line(text)}, ensure_ascii=False), encoding="utf-8")
    nl.set_line(base, "ep01", "s3", 0, reason={"trigger": "N1", "evidence": "x"}, source="directing")
    with monkeypatch.context() as m:       # 旧规则(不认句号、按字比例切)写入的记录
        m.setattr(nl, "lead_split", lambda t, s, est: ("放回", "去。快走！"))
        assert nl.sync_episode(base, "ep01", write=True)["errors"] == []
    before = pp.read_bytes()
    assert json.loads(before)["native_leads"][0]["lead"] == "放回"
    # 新规则会在句号处切,但整句没变 → 沿用记录:check 不 FAIL,--write 不改写(H3V 指纹不变)
    assert nl.lead_split(text, 0.64, 1.6)[0] == "放回去。"
    assert [(r["lead"], r["rest"]) for r in nl.collect(base, "ep01")] == [("放回", "去。快走！")]
    assert nl.sync_episode(base, "ep01", write=False)["errors"] == [] and nl.validate(base, "ep01") == []
    res = nl.sync_episode(base, "ep01", write=True)
    assert res["errors"] == [] and res["updated_prompts"] == [] and pp.read_bytes() == before
    # 只差空白也算同一句
    _set_text(base, "s3", text + " ", 1.6)
    assert nl.collect(base, "ep01")[0]["lead"] == "放回"
    # s 改了 → 按新规则重切
    sl_p = base / "directing" / "ep01" / "shot_list.json"
    sl = json.loads(sl_p.read_text(encoding="utf-8"))
    sl["shots"][2]["dialogue_lines"][0]["native_lead"]["s"] = 0.5
    sl_p.write_text(json.dumps(sl, ensure_ascii=False), encoding="utf-8")
    assert nl.collect(base, "ep01")[0]["lead"] == "放回去。"
    # 台词改了 → 按新规则切
    sl["shots"][2]["dialogue_lines"][0]["native_lead"]["s"] = 0.64
    sl["shots"][2]["dialogue_lines"][0]["text"] = "放回去。快走吧！"
    sl_p.write_text(json.dumps(sl, ensure_ascii=False), encoding="utf-8")
    assert [(r["lead"], r["rest"]) for r in nl.collect(base, "ep01")] == [("放回去。", "快走吧！")]
    # 坏的 prompt json 不影响 collect
    pp.write_text("{bad", encoding="utf-8")
    assert nl.collect(base, "ep01")[0]["lead"] == "放回去。"


def test_p8_judged_on_kept_lead(tmp_path, monkeypatch):
    base = _proj(tmp_path)
    text = "放回去。快走！"
    _set_text(base, "s3", text, 1.6)
    pp = base / "assets" / "prompts" / "ep01" / "grp001.json"
    pp.write_text(json.dumps({"video_prompt": _prompt_line(text)}, ensure_ascii=False), encoding="utf-8")
    nl.set_line(base, "ep01", "s3", 0, reason={"trigger": "N1", "evidence": "x"}, source="directing")
    with monkeypatch.context() as m:
        m.setattr(nl, "lead_split", lambda t, s, est: ("放回去。快", "走！"))
        assert nl.sync_episode(base, "ep01", write=True)["errors"] == []
    # 触发词「快」在记录的先入里(新规则的「放回去。」里没有)→ P8 按实际写进 prompt 的先入报
    (base / "directing" / "ep01" / "shots" / "s3" / "blocking.json").write_text(json.dumps({"characters": [
        {"id": "CHAR-0001", "performance": {"trigger": {"word": "快"}}}]}), encoding="utf-8")
    assert any("P8" in e and "放回去。快" in e for e in nl.validate(base, "ep01"))
