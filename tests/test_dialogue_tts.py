"""对白语音库(modules/dialogue_tts.py)与排轨(modules/dialogue_track.py)测试:
key 失效、惰性同步、未选角跳过、孤儿移出、排轨 overflow、字幕按实际起止、真 ffmpeg 混轨。"""
import json
import math
import shutil
import struct
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import dialogue_tts as dt  # noqa: E402
from modules import dialogue_track as trk  # noqa: E402
from modules import whitebox_subtitles as ws  # noqa: E402

CHANNEL = ("volcengine", "seed-tts-2.0")


def write_wav(path: Path, seconds: float, freq: float = 440.0, rate: int = 16000):
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(n)))


def make_project(tmp_path: Path, *, enabled=True, with_casting=True) -> Path:
    base = tmp_path / "proj"
    (base / "directing" / "ep01").mkdir(parents=True)
    (base / "bible" / "characters").mkdir(parents=True)
    (base / "assets" / "audio" / "voice" / "refs").mkdir(parents=True)
    (base / "assets" / "prompts" / "ep01").mkdir(parents=True)
    (base / "settings.json").write_text(json.dumps({"output": {"dialogue_tts": enabled, "dialogue_voice": "native"}}))
    (base / "bible" / "characters" / "index.json").write_text(json.dumps({"characters": [
        {"id": "CHAR-0001", "canonical_name": "阿一"}, {"id": "CHAR-0002", "canonical_name": "阿二"}]}, ensure_ascii=False))
    sl = {"shots": [
        {"shot_id": "sh001", "duration_s": 4.0, "dialogue_lines": [
            {"speaker": "CHAR-0001", "text": "你好。", "est_duration_s": 1.0},
            {"speaker": "CHAR-0002", "text": "你也好。", "est_duration_s": 1.2}]},
        {"shot_id": "sh002", "duration_s": 3.0, "dialogue_lines": [
            {"character_id": "CHAR-0001", "line": "走吧。", "est_duration_s": 0.8},
            {"speaker": "路人(群演)", "text": "让让!", "est_duration_s": 0.5}]},
        {"shot_id": "sh003", "duration_s": 2.0, "dialogue_lines": []},
    ], "generation_groups": [{"group_id": "grp001", "shots": ["sh001", "sh002", "sh003"]}]}
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    if with_casting:
        (base / "assets" / "audio" / "voice" / "casting.json").write_text(json.dumps({"castings": [
            {"character_id": "CHAR-0001", "variant": "default", "tts_model": "volcengine/seed-tts-2.0", "tts_voice": "zh_male_a", "speed": 1.0},
            {"character_id": "CHAR-0002", "variant": "default", "tts_model": "volcengine/seed-tts-2.0", "tts_voice": "zh_female_b", "speed": 1.1}]}))
    (base / "assets" / "audio" / "voice" / "refs" / "CHAR-0001_voiceprint.mp3").write_bytes(b"vp1")
    return base


def fake_tts(calls: list):
    def run(entry: dict, out: Path):
        calls.append((entry["shot_id"], entry["idx"], entry["speaker"], entry["text"]))
        write_wav(out, 0.5 + 0.1 * entry["idx"])
    return run


@pytest.fixture(autouse=True)
def fixed_channel(monkeypatch):
    monkeypatch.setattr(dt, "tts_channel", lambda: CHANNEL)


def test_collect_lines_compat_and_speaker_resolution(tmp_path):
    base = make_project(tmp_path)
    lines = dt.collect_lines(base, "ep01")
    assert [(l["shot_id"], l["idx"], l["speaker"]) for l in lines] == [
        ("sh001", 0, "CHAR-0001"), ("sh001", 1, "CHAR-0002"), ("sh002", 0, "CHAR-0001"), ("sh002", 1, "")]
    assert lines[2]["text"] == "走吧。" and lines[0]["group_id"] == "grp001"


def test_sync_is_lazy_and_skips_unbound(tmp_path):
    base = make_project(tmp_path)
    calls = []
    m = dt.sync(base, "ep01", tts=fake_tts(calls), probe=lambda p: 0.7)
    assert m["summary"] == {"total": 4, "ok": 3, "unbound": 1, "failed": 0, "removed": 0}
    assert len(calls) == 3 and m["checks"]["unbound_lines"] == ["sh002/l01"]
    assert (dt.lib_dir(base, "ep01") / "sh001_l00_CHAR-0001.mp3").is_file()
    # 再同步:什么都没变 → 零合成
    calls.clear()
    m2 = dt.sync(base, "ep01", tts=fake_tts(calls), probe=lambda p: 0.7)
    assert calls == [] and m2["synthesized"] == 0
    st = dt.status(base, "ep01")
    assert st["fresh"] == 3 and st["unbound"] == 1 and not st["needs_sync"]


def test_key_invalidation_text_casting_voiceprint_channel(tmp_path, monkeypatch):
    base = make_project(tmp_path)
    calls = []
    dt.sync(base, "ep01", tts=fake_tts(calls), probe=lambda p: 0.7)
    # ① 改一句台词 → 只重出那一句
    sl = json.loads((base / "directing" / "ep01" / "shot_list.json").read_text())
    sl["shots"][0]["dialogue_lines"][0]["text"] = "你好呀。"
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    calls.clear()
    dt.sync(base, "ep01", tts=fake_tts(calls), probe=lambda p: 0.7)
    assert calls == [("sh001", 0, "CHAR-0001", "你好呀。")]
    # ② 改 CHAR-0002 的音色 → 只重出她的句子
    c = json.loads((base / "assets" / "audio" / "voice" / "casting.json").read_text())
    c["castings"][1]["tts_voice"] = "zh_female_c"
    (base / "assets" / "audio" / "voice" / "casting.json").write_text(json.dumps(c))
    calls.clear()
    dt.sync(base, "ep01", tts=fake_tts(calls), probe=lambda p: 0.7)
    assert [x[2] for x in calls] == ["CHAR-0002"]
    # ③ 换 voiceprint 样本 → CHAR-0001 两句重出
    (base / "assets" / "audio" / "voice" / "refs" / "CHAR-0001_voiceprint.mp3").write_bytes(b"vp1-new")
    calls.clear()
    dt.sync(base, "ep01", tts=fake_tts(calls), probe=lambda p: 0.7)
    assert sorted(x[0] for x in calls) == ["sh001", "sh002"] and {x[2] for x in calls} == {"CHAR-0001"}
    # ④ 换 TTS 渠道/模型 → 全部重出
    monkeypatch.setattr(dt, "tts_channel", lambda: ("minimax", "speech-2.8-hd"))
    calls.clear()
    dt.sync(base, "ep01", tts=fake_tts(calls), probe=lambda p: 0.7)
    assert len(calls) == 3


def test_removed_line_moves_file_to_prev(tmp_path):
    base = make_project(tmp_path)
    dt.sync(base, "ep01", tts=fake_tts([]), probe=lambda p: 0.7)
    sl = json.loads((base / "directing" / "ep01" / "shot_list.json").read_text())
    sl["shots"][0]["dialogue_lines"].pop()   # 删掉 sh001 第二句(CHAR-0002)
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    m = dt.sync(base, "ep01", tts=fake_tts([]), probe=lambda p: 0.7)
    assert m["summary"]["removed"] == 1
    assert not (dt.lib_dir(base, "ep01") / "sh001_l01_CHAR-0002.mp3").exists()
    assert (dt.lib_dir(base, "ep01") / "_prev" / "sh001_l01_CHAR-0002.mp3").is_file()


def test_cloud_provider_without_casting_is_unbound_but_desc_mode_binds(tmp_path, monkeypatch):
    base = make_project(tmp_path, with_casting=False)
    p = dt.plan(base, "ep01")
    assert {e["status"] for e in p["lines"]} == {"unbound"}
    assert any("casting.json" in e["reason"] for e in p["lines"])
    monkeypatch.setattr(dt, "tts_channel", lambda: ("volcengine", "seed-audio-1.0"))
    p = dt.plan(base, "ep01")
    # CHAR-0001 有 voiceprint 样本可按描述定制锚定;CHAR-0002 既无样本也无声纹卡 → unbound
    assert [e["status"] for e in p["lines"]] == ["missing", "unbound", "missing", "unbound"]


def test_failed_line_does_not_abort(tmp_path):
    base = make_project(tmp_path)

    def flaky(entry, out):
        if entry["speaker"] == "CHAR-0002":
            raise RuntimeError("quota")
        write_wav(out, 0.5)
    m = dt.sync(base, "ep01", tts=flaky, probe=lambda p: 0.5)
    assert m["summary"]["failed"] == 1 and m["summary"]["ok"] == 2
    assert m["checks"]["failed_lines"] == ["sh001/l01"]


def test_ensure_returns_none_when_disabled(tmp_path):
    base = make_project(tmp_path, enabled=False)
    assert dt.ensure(base, "ep01") is None
    assert dt.status(base, "ep01")["enabled"] is False


def test_place_lines_sequential_and_overflow():
    audio = {("sh001", 0): {"path": "a.wav", "duration_s": 1.0, "speaker": "CHAR-0001", "text": "a"},
             ("sh001", 1): {"path": "b.wav", "duration_s": 3.5, "speaker": "CHAR-0002", "text": "b"},
             ("sh002", 0): {"path": "c.wav", "duration_s": 0.8, "speaker": "CHAR-0001", "text": "c"}}
    timeline = {"sh001": {"start": 0.0, "end": 4.0, "group_id": "grp001"}, "sh002": {"start": 4.0, "end": 7.0, "group_id": "grp001"}}
    pls, overflow = trk.place_lines(audio, timeline)
    # 上一句拖进 sh002(4.75 说完):sh002 第一句顺延到 4.75+GAP,不叠着说
    assert [(p["shot_id"], p["idx"], p["start"]) for p in pls] == [("sh001", 0, 0.1), ("sh001", 1, 1.25), ("sh002", 0, 4.9)]
    assert pls[2]["delay_s"] == 0.8 and "delay_s" not in pls[0]
    assert overflow == [{"shot_id": "sh001", "idx": 1, "speaker": "CHAR-0002", "start": 1.25, "end": 4.75, "overflow_s": 0.75}]
    cues = trk.cues_from_placements(pls, {"CHAR-0001": "阿一"})
    assert cues[0]["text"] == "阿一:a" and cues[2]["start"] == 4.9


def test_place_lines_delay_is_capped():
    """上一句拖得再长,本镜第一句最多顺延到本镜时长一半处(不至于整句挪出本镜)。"""
    audio = {("sh001", 0): {"path": "a.wav", "duration_s": 9.0, "speaker": "CHAR-0001", "text": "a"},
             ("sh002", 0): {"path": "b.wav", "duration_s": 1.0, "speaker": "CHAR-0002", "text": "b"}}
    timeline = {"sh002": {"start": 4.0, "end": 7.0}, "sh001": {"start": 0.0, "end": 4.0}}
    pls, _ = trk.place_lines(audio, timeline)
    assert [(p["shot_id"], p["start"]) for p in pls] == [("sh001", 0.1), ("sh002", 5.5)]


def test_pace_plan_compresses_pauses_then_tempo():
    # 2.0s:0.6 说话 + 0.8 停顿 + 0.6 说话;估时 1.0 → 停顿压到 0.3(1.5s)→ 再 x1.5 贴到 1.0
    pp = dt.pace_plan([(0.6, 1.4)], 2.0, 1.0, max_tempo=1.5)
    assert pp["pause_s"] == 0.5 and pp["tempo"] == 1.5 and pp["duration_s"] == 1.0
    # 压完停顿已装得下:不变速
    assert dt.pace_plan([(0.6, 1.4)], 2.0, 1.6)["tempo"] == 1.0
    # 倍率封顶
    assert dt.pace_plan([], 3.0, 1.0, max_tempo=1.3)["tempo"] == 1.3


def write_wav_with_pause(path: Path, rate: int = 16000):
    path.parent.mkdir(parents=True, exist_ok=True)
    tone = lambda sec: b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(int(sec * rate)))  # noqa: E731
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(tone(0.6) + b"\x00\x00" * int(0.8 * rate) + tone(0.6))


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg 不可用")
def test_sync_writes_paced_copy_for_reels(tmp_path):
    """节奏贴合(2026-09-28):超估时的句子另出 _paced/ 贴合版给样片,库文件保持自然语速;后处理不重新合成。"""
    base = make_project(tmp_path)
    calls = []

    def tts(entry, out):
        calls.append(entry["shot_id"])
        write_wav_with_pause(out)                      # 每句 2.0s,估时 1.0 / 1.2 / 0.8
    m = dt.sync(base, "ep01", tts=tts, trim=False)
    ok = [e for e in m["lines"] if e["status"] == "ok"]
    assert len(calls) == 3 and all(e["pace"] for e in ok) and m["pace"]["lines"] == 3
    first = ok[0]
    assert abs(first["duration_s"] - 2.0) < 0.1                                  # 库文件仍是自然语速
    assert abs(first["pace"]["tempo"] - 1.5) < 0.01 and abs(first["pace"]["duration_s"] - 1.0) < 0.12
    ldir = dt.lib_dir(base, "ep01")
    natural, paced = dt.line_audio(base, "ep01", m), dt.line_audio(base, "ep01", m, paced=True)
    assert natural[("sh001", 0)]["path"] == ldir / first["file"]
    assert paced[("sh001", 0)]["path"] == ldir / "_paced" / first["file"]
    assert paced[("sh001", 0)]["duration_s"] == first["pace"]["duration_s"]
    assert dt.library_fingerprint(m) != dt.library_fingerprint({"lines": [dict(e, pace=None) for e in m["lines"]]})
    # 再同步:不重新合成、不重出贴合版
    m2 = dt.sync(base, "ep01", tts=tts, trim=False)
    assert len(calls) == 3 and m2["pace"]["repaced"] == 0 and m2["pace"]["lines"] == 3
    assert dt.library_fingerprint(m2) == dt.library_fingerprint(m)
    # 倍率上限改小:只重出贴合版
    m3 = dt.sync(base, "ep01", tts=tts, trim=False, max_tempo=1.2)
    assert len(calls) == 3 and m3["pace"]["repaced"] == 3
    assert [e for e in m3["lines"] if e["status"] == "ok"][0]["pace"]["tempo"] == 1.2
    # 1.0 = 关闭:贴合版清掉,样片退回自然语速
    m4 = dt.sync(base, "ep01", tts=tts, trim=False, max_tempo=1.0)
    assert m4["pace"]["lines"] == 0 and not (ldir / "_paced" / first["file"]).exists()
    assert dt.line_audio(base, "ep01", m4, paced=True)[("sh001", 0)]["path"] == ldir / first["file"]


def test_subtitle_cues_follow_actual_audio(tmp_path):
    base = make_project(tmp_path)
    dt.sync(base, "ep01", tts=fake_tts([]), probe=lambda p: 0.9)
    audio = dt.line_audio(base, "ep01")
    assert len(audio) == 3
    pls, _ = ws.episode_dialogue_placements(base, "ep01", ["grp001"], {"grp001": 9.0}, audio)
    cues = ws.episode_subtitle_cues(base, "ep01", ["grp001"], {"grp001": 9.0}, pls)
    d = [c for c in cues if c["kind"] == "dialogue"]
    assert [c["text"] for c in d] == ["阿一:你好。", "阿二:你也好。", "阿一:走吧。", "路人(群演):让让!"]
    assert d[0]["start"] == 0.1 and d[0]["end"] == 1.0 and d[1]["start"] == 1.15   # 按实际音频起止
    assert d[3]["start"] > d[2]["start"]                                          # 未选角句按估时比例兜底


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg 不可用")
def test_build_track_real_ffmpeg(tmp_path):
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    write_wav(a, 0.5), write_wav(b, 0.5, freq=660)
    pls = [{"path": a, "start": 0.2}, {"path": b, "start": 1.0}]
    out = trk.build_track(pls, 2.0, tmp_path / "track.wav")
    dur = dt.probe_duration(out)
    assert dur is not None and abs(dur - 2.0) < 0.05


def test_dubbing_mode_forces_enabled(tmp_path):
    """对白配音=后期配音时「生成对白语音」必开(2026-09-23):即便 settings 里 dialogue_tts=false 也视为开启。"""
    base = make_project(tmp_path, enabled=False)
    (base / "settings.json").write_text(json.dumps({"output": {"dialogue_tts": False, "dialogue_voice": "dubbing"}}))
    assert dt.enabled(base) is True
    (base / "settings.json").write_text(json.dumps({"output": {"dialogue_tts": False, "dialogue_voice": "native"}}))
    assert dt.enabled(base) is False


# ---------------------------------------------------------------- 声画分离(2026-10-03):画外 / V.O. 句透传与排轨

def _stub_offscreen(monkeypatch):
    """modules/offscreen_lines 尚未落地时装一个最小桩(契约:placement / heard_in / SOURCE_FX);真模块在就用真的。"""
    import types
    try:
        from modules import offscreen_lines  # noqa: F401
        return
    except ImportError:
        pass
    m = types.ModuleType("offscreen_lines")
    m.SOURCE_FX = {"plain": [], "phone": [], "door": [], "distance": [], "inner": [], "memory": []}
    m.placement = lambda ln: (str(ln.get("placement") or "on").lower() if str(ln.get("placement") or "on").lower() in ("on", "os", "vo") else "on")
    m.heard_in = lambda ln, sid: [h for h in (ln.get("heard_in") or []) if isinstance(h, str)] or [sid]
    monkeypatch.setitem(sys.modules, "offscreen_lines", m)
    monkeypatch.setitem(sys.modules, "modules.offscreen_lines", m)


def test_collect_lines_passes_placement_fields(tmp_path, monkeypatch):
    _stub_offscreen(monkeypatch)
    base = make_project(tmp_path)
    sl = json.loads((base / "directing" / "ep01" / "shot_list.json").read_text())
    sl["shots"][0]["dialogue_lines"][1].update(placement="os", heard_in=["sh002"], source_fx="door", offset_s=0.8)
    sl["shots"][1]["dialogue_lines"][0].update(placement="vo")
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    lines = dt.collect_lines(base, "ep01")
    # idx 序号不变(os/vo 句占位),画内句缺省 on + heard_in = 自己
    assert [(l["shot_id"], l["idx"], l["placement"]) for l in lines] == [
        ("sh001", 0, "on"), ("sh001", 1, "os"), ("sh002", 0, "vo"), ("sh002", 1, "on")]
    assert lines[0]["heard_in"] == ["sh001"] and "source_fx" not in lines[0]
    assert lines[1]["heard_in"] == ["sh002"] and lines[1]["source_fx"] == "door" and lines[1]["offset_s"] == 0.8
    assert lines[2]["heard_in"] == ["sh002"] and lines[2]["source_fx"] == "inner" and lines[2]["offset_s"] == 0.4   # vo 缺省
    assert [l["idx"] for l in dt.onscreen_only(lines)] == [0, 1]


def test_place_lines_offscreen_anchors_at_heard_in():
    """画外句锚在 heard_in 首镜起点 + offset_s,不按自己的镜排、不参与画内避让链;同窗口多句顺序叠排。"""
    audio = {("sh001", 0): {"path": "a.wav", "duration_s": 1.0, "speaker": "CHAR-0001", "text": "a"},
             ("sh001", 1): {"path": "b.wav", "duration_s": 1.0, "speaker": "CHAR-0002", "text": "b",
                            "placement": "os", "heard_in": ["sh002"], "offset_s": 0.5},
             ("sh001", 2): {"path": "c.wav", "duration_s": 2.0, "speaker": "CHAR-0002", "text": "c",
                            "placement": "vo", "heard_in": ["sh002", "sh003"], "offset_s": 0.5},
             ("sh002", 0): {"path": "d.wav", "duration_s": 0.8, "speaker": "CHAR-0001", "text": "d"}}
    timeline = {"sh001": {"start": 0.0, "end": 4.0, "group_id": "grp001"}, "sh002": {"start": 4.0, "end": 7.0, "group_id": "grp001"},
                "sh003": {"start": 7.0, "end": 9.0, "group_id": "grp001"}}
    pls, overflow = trk.place_lines(audio, timeline)
    by = {(p["shot_id"], p["idx"]): p for p in pls}
    assert by[("sh001", 0)]["start"] == 0.1 and by[("sh002", 0)]["start"] == 4.1      # 画内句照旧,b 没把 d 顺延
    assert by[("sh001", 1)]["start"] == 4.5 and by[("sh001", 1)]["anchor_shot"] == "sh002" and by[("sh001", 1)]["placement"] == "os"
    assert by[("sh001", 2)]["start"] == pytest.approx(4.5 + 1.0 + trk.GAP_S)          # 同窗口叠排
    assert by[("sh001", 2)]["overflow_s"] == 0 and overflow == []                     # 窗口到 sh003 末尾 9.0
    cues = trk.cues_from_placements(pls, {"CHAR-0002": "阿二"})
    texts = {c["start"]: c["text"] for c in cues}
    assert texts[4.5] == "阿二(画外):b" and texts[by[("sh001", 2)]["start"]] == "阿二(V.O.):c"


def test_subtitle_cues_offscreen_fallback_uses_heard_in(tmp_path, monkeypatch):
    """没有库音频时,画外句字幕按 heard_in 首镜 + offset 估位,画内句的估时比例分配不受它影响。"""
    _stub_offscreen(monkeypatch)
    base = make_project(tmp_path)
    sl = json.loads((base / "directing" / "ep01" / "shot_list.json").read_text())
    sl["shots"][0]["dialogue_lines"][1].update(placement="os", heard_in=["sh002"], offset_s=0.5)
    (base / "directing" / "ep01" / "shot_list.json").write_text(json.dumps(sl, ensure_ascii=False))
    cues = [c for c in ws.episode_subtitle_cues(base, "ep01", ["grp001"], {"grp001": 9.0}, None) if c["kind"] == "dialogue"]
    by = {c["text"]: c for c in cues}
    assert by["阿一:你好。"]["start"] == 0.0 and by["阿一:你好。"]["end"] == 4.0       # sh001 只剩一句画内,占满
    assert by["阿二(画外):你也好。"]["start"] == 4.5 and by["阿二(画外):你也好。"]["shot_id"] == "sh002"
