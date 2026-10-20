"""数字人克隆配音(2026-10-20):code/dialogue_clone.py + genmedia tts --clone-from。"""
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

from modules import genmedia

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dialogue_clone", ROOT / "code" / "dialogue_clone.py")
dc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dc)

ENGINE = (True, "test-engine", "test")
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg")


def _tone(path: Path, seconds: float, freq: int = 440, lead: float = 0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    src = f"sine=f={freq}:d={seconds:.2f}"
    if lead:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono:d={lead}",
                        "-f", "lavfi", "-i", src, "-filter_complex", "[0][1][0]concat=n=3:v=0:a=1", str(path)], check=True)
    else:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, str(path)], check=True)


def _fake_tts(calls):
    def run(text, sample, out):
        calls.append((text, Path(sample).name))
        _tone(Path(out), 0.12 * len(text), lead=0.3)
    return run


def test_parse_script_labels_and_colons():
    rows, issues = dc.parse_script("[00:01.0-00:02.0] 主持人:欢迎\n# 注释\n\n嘉宾：他说:好。\n")
    assert issues == [] and [(r["speaker"], r["text"]) for r in rows] == [("主持人", "欢迎"), ("嘉宾", "他说:好。")]
    rows, _ = dc.parse_script("第一句。\n第二句:有冒号。")          # 不是每行都带人名 = 单人稿
    assert {r["speaker"] for r in rows} == {dc.DEFAULT_SPEAKER} and rows[1]["text"] == "第二句:有冒号。"
    rows, issues = dc.parse_script("主持人:你好\n台词里写着:时间到了", known={"主持人", "嘉宾"})
    assert len(rows) == 1 and "第 2 行" in issues[0]
    rows, _ = dc.parse_script("他说:好\n然后走了", known={"旁白人"})   # 只有一个说话人:冒号不当人名
    assert [r["speaker"] for r in rows] == ["旁白人", "旁白人"] and rows[0]["text"] == "他说:好"
    assert dc.parse_script("  \n# x\n")[1] == ["台词稿是空的"]


def test_plan_reports_missing_samples_and_unsupported_channel(tmp_path):
    (tmp_path / "refs" / "text").mkdir(parents=True)
    (tmp_path / "refs" / "text" / "transcript.txt").write_text("主持人:一\n嘉宾:二\n", encoding="utf-8")
    p = dc.plan(tmp_path, "ep01", engine=(False, "渠道 x 不支持", "x"))
    assert any("嘉宾 没有对应的克隆声音样本" in i for i in p["issues"])
    assert "渠道 x 不支持" in p["issues"]
    assert dc.plan(tmp_path / "nope", "ep01", engine=ENGINE)["missing_script"]


@needs_ffmpeg
def test_auto_mapping_by_name_and_single_sample(tmp_path):
    (tmp_path / "refs" / "text").mkdir(parents=True)
    (tmp_path / "refs" / "text" / "transcript.txt").write_text("host:hello\nguest:hi\n", encoding="utf-8")
    _tone(tmp_path / "refs" / "voices" / "Host.mp3", 6)
    _tone(tmp_path / "refs" / "voices" / "someone.wav", 6, 330)
    p = dc.plan(tmp_path, "ep01", engine=ENGINE)
    assert p["issues"] == [] and p["cast"] == {"host": "refs/voices/Host.mp3", "guest": "refs/voices/someone.wav"}


@needs_ffmpeg
def test_synth_builds_master_and_transcript_accepted_by_dialogue_video(tmp_path):
    from modules.dialogue_video import build_dialogue_track
    (tmp_path / "refs" / "text").mkdir(parents=True)
    script = tmp_path / "refs" / "text" / "transcript.txt"
    script.write_text("主持人:欢迎收听本期节目。\n嘉宾:谢谢邀请,今天聊电影。\n主持人:我们开始吧。\n", encoding="utf-8")
    _tone(tmp_path / "refs" / "voices" / "host.mp3", 8, 220)
    _tone(tmp_path / "refs" / "voices" / "guest.mp3", 8, 330)
    calls = []
    ov = {"主持人": "refs/voices/host.mp3", "嘉宾": "refs/voices/guest.mp3"}
    r = dc.synth(tmp_path, "ep01", ov, tts=_fake_tts(calls), engine=ENGINE, log=lambda m: None)
    assert r["ok"] and r["synthesized"] == 3
    assert [c[1] for c in calls] == ["host.mp3", "guest.mp3", "host.mp3"]
    assert (tmp_path / dc.CAST_REL).is_file()
    assert dc.check(tmp_path, "ep01")["ok"]
    images = {}
    for name in ov:
        img = tmp_path / "refs" / "characters" / f"{len(images)}.png"
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(b"\x89PNG")
        images[name] = {"image": str(img)}
    track = build_dialogue_track(str(tmp_path / "assets/audio/master/ep01.mp3"),
                                 str(tmp_path / dc.TRANSCRIPT_REL), speaker_images=images)
    assert [u["speaker"] for u in track["utterances"]] == ["主持人", "嘉宾", "主持人"]
    assert track["utterances"][0]["start"] == 0.0

    # 没改的句子不重出;台词稿改了 → check 判过期,重跑只合成新句
    again = dc.synth(tmp_path, "ep01", None, tts=_fake_tts(calls), engine=ENGINE, log=lambda m: None)
    assert again["ok"] and again["synthesized"] == 0
    script.write_text(script.read_text(encoding="utf-8") + "嘉宾:再见。\n", encoding="utf-8")
    assert "台词稿" in dc.check(tmp_path, "ep01")["errors"][0]
    assert dc.synth(tmp_path, "ep01", None, tts=_fake_tts(calls), engine=ENGINE, log=lambda m: None)["synthesized"] == 1
    assert dc.check(tmp_path, "ep01")["ok"]


@needs_ffmpeg
def test_synth_refuses_to_overwrite_user_master_and_keeps_failures(tmp_path):
    (tmp_path / "refs" / "text").mkdir(parents=True)
    (tmp_path / "refs" / "text" / "transcript.txt").write_text("一句话。\n两句话。\n", encoding="utf-8")
    _tone(tmp_path / "refs" / "voices" / "me.mp3", 6)
    master = tmp_path / "assets" / "audio" / "master" / "ep01.mp3"
    _tone(master, 3, 550)
    r = dc.synth(tmp_path, "ep01", tts=_fake_tts([]), engine=ENGINE, log=lambda m: None)
    assert not r["ok"] and "--force" in r["issues"][0]

    def flaky(text, sample, out):
        if text.startswith("两"):
            raise RuntimeError("渠道 500")
        _tone(Path(out), 1.0)
    r = dc.synth(tmp_path, "ep01", tts=flaky, engine=ENGINE, force=True, log=lambda m: None)
    assert not r["ok"] and r["failed"] == 1
    assert not dc.check(tmp_path, "ep01")["ok"]


def test_clone_support_matrix():
    assert genmedia.clone_support({"provider": "agentics", "clone": "qwen3tts-clone"})[0]
    assert not genmedia.clone_support({"provider": "agentics", "clone": ""})[0]
    assert genmedia.clone_support({"provider": "comfyui", "mode": "local", "workflow": "comfy/x.json"})[0]
    assert not genmedia.clone_support({"provider": "comfyui", "mode": "rh_cn", "workflow": "comfy/x.json"})[0]
    assert genmedia.clone_support({"provider": "volcengine", "model": genmedia.SEEDAUDIO_MODEL})[0]
    assert not genmedia.clone_support({"provider": "volcengine", "model": "seed-tts-2.0"})[0]
    for prov in ("openrouter", "minimax", "elevenlabs"):
        assert not genmedia.clone_support({"provider": prov})[0]


def test_clone_from_dispatch(tmp_path, monkeypatch):
    sample = tmp_path / "voice.wav"
    sample.write_bytes(b"RIFF")
    out = tmp_path / "out.mp3"
    seen = {}

    monkeypatch.setattr(genmedia, "get_config", lambda kind, *a, **k: {"provider": "comfyui", "mode": "local",
                                                                       "workflow": "comfy/x.json", "voice_mode": "design"})

    def fake_comfy(cfg, text, output, voice, speed, instructions, *rest):
        seen["comfy"] = (cfg["voice_mode"], voice, rest)
        return output
    monkeypatch.setattr(genmedia, "_tts_comfyui", fake_comfy)
    genmedia.generate_tts("你好", str(out), voice="ignored", character="CHAR-0001", clone_from=str(sample))
    assert seen["comfy"] == ("library", str(sample.resolve()), ())

    monkeypatch.setattr(genmedia, "get_config", lambda kind, *a, **k: {"provider": "volcengine", "api_key": "k",
                                                                       "model": genmedia.SEEDAUDIO_MODEL})

    def fake_create(api_key, text_prompt, references, output, speed):
        seen["volc"] = (text_prompt, references)
        return {"audio": "AAAA"}
    monkeypatch.setattr(genmedia, "_seedaudio_create", fake_create)
    genmedia.generate_tts("你好", str(out), clone_from=str(sample))
    assert "@音频1" in seen["volc"][0] and "你好" in seen["volc"][0] and len(seen["volc"][1]) == 1

    monkeypatch.setattr(genmedia, "get_config", lambda kind, *a, **k: {"provider": "agentics", "clone": "voice-only",
                                                                       "model": "", "profile_code": ""})
    monkeypatch.setattr(genmedia, "_agentics_profile", lambda kind, code: {"token_schema": {"files": {}}})
    with pytest.raises(RuntimeError, match="不收参考音频"):
        genmedia.generate_tts("你好", str(out), clone_from=str(sample))
    monkeypatch.setattr(genmedia, "_agentics_profile",
                        lambda kind, code: {"token_schema": {"files": {"reference_audios": {"required": True}}}})
    monkeypatch.setattr(genmedia, "_agentics_generate",
                        lambda kind, cfg, params, files, profile=None: seen.setdefault("ag", (cfg["model"], files)) and b"ID3")
    genmedia.generate_tts("你好", str(out), clone_from=str(sample))
    assert seen["ag"] == ("voice-only", {"reference_audios": [str(sample.resolve())]})

    monkeypatch.setattr(genmedia, "get_config", lambda kind, *a, **k: {"provider": "minimax"})
    with pytest.raises(RuntimeError, match="不支持按上传样本克隆"):
        genmedia.generate_tts("你好", str(out), clone_from=str(sample))
    with pytest.raises(RuntimeError, match="克隆样本不存在"):
        genmedia.generate_tts("你好", str(out), clone_from=str(tmp_path / "missing.wav"))
