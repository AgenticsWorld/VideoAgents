"""ComfyUI / RunningHub MiniMax-H3 Ref2VA 参考素材接线:官方 MiniMaxH3ReferenceToVideo 四组动态输入
(ref_images ≤9 / ref_videos ≤3 / ref_video_audios ≤3 按编号配对 / ref_audios ≤3)。"""
import shutil
import subprocess

import pytest

from modules import genmedia


def h3_workflow(**inputs):
    return {"5": {"class_type": genmedia.H3_REFERENCE_NODE,
                  "inputs": {"prompt": "x", "length": 124, **inputs}},
            "14": {"class_type": "SaveVideo", "inputs": {"video": ["5", 1]}}}


def test_video_refs_wire_frames_and_paired_soundtrack():
    wf = h3_workflow()
    genmedia._add_h3_references(wf, ["a.png"], ["voice.wav"], [("clip.mp4", True), ("mute.mp4", False)])
    inputs = wf["5"]["inputs"]
    assert wf[inputs["ref_images.ref_image_0"][0]] == {"class_type": "LoadImage", "inputs": {"image": "a.png"}}
    parts0 = inputs["ref_videos.ref_video_0"]
    assert wf[parts0[0]]["class_type"] == "GetVideoComponents" and parts0[1] == 0
    load0 = wf[parts0[0]]["inputs"]["video"]
    assert wf[load0[0]] == {"class_type": "LoadVideo", "inputs": {"file": "clip.mp4"}}
    # 音轨按编号与同号视频配对,取同一 GetVideoComponents 的 audio 输出
    assert inputs["ref_video_audios.ref_video_audio_0"] == [parts0[0], 1]
    parts1 = inputs["ref_videos.ref_video_1"]
    assert wf[wf[parts1[0]]["inputs"]["video"][0]]["inputs"]["file"] == "mute.mp4"
    assert "ref_video_audios.ref_video_audio_1" not in inputs
    assert wf[inputs["ref_audios.ref_audio_0"][0]] == {"class_type": "LoadAudio", "inputs": {"audio": "voice.wav"}}


def test_official_limits_enforced():
    with pytest.raises(RuntimeError, match="参考视频最多 3"):
        genmedia._add_h3_references(h3_workflow(), [], [], [("v.mp4", False)] * 4)
    with pytest.raises(RuntimeError, match="参考图最多 9"):
        genmedia._add_h3_references(h3_workflow(), ["i.png"] * 10, [], [])
    with pytest.raises(RuntimeError, match="参考音频最多 3"):
        genmedia._add_h3_references(h3_workflow(), ["i.png"], ["a.wav"] * 4, [])
    # 独立音频须有图或视频陪同;只带视频也算
    with pytest.raises(RuntimeError, match="参考音频必须"):
        genmedia._add_h3_references(h3_workflow(), [], ["a.wav"], [])
    genmedia._add_h3_references(h3_workflow(), [], ["a.wav"], [("v.mp4", True)])
    assert (genmedia.COMFY_H3_MAX_IMAGE_REFS, genmedia.COMFY_H3_MAX_VIDEO_REFS,
            genmedia.COMFY_H3_MAX_VIDEO_AUDIO_REFS, genmedia.COMFY_H3_MAX_AUDIO_REFS) == (9, 3, 3, 3)


def test_stale_template_video_chain_removed():
    # RunningHub 导出件常带作者的演示参考视频链 LoadVideo→GetVideoComponents→节点;拆线后整链回收,
    # 但被其他节点仍引用的上游(如共享 LoadVideo)保留
    wf = h3_workflow(**{"ref_videos.ref_video_0": ["21", 0], "ref_video_audios.ref_video_audio_0": ["21", 1],
                        "ref_images.ref_image_0": ["30", 0]})
    wf["20"] = {"class_type": "LoadVideo", "inputs": {"file": "author-demo.mp4"}}
    wf["21"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["20", 0]}}
    wf["30"] = {"class_type": "LoadImage", "inputs": {"image": "author.png"}}
    wf["40"] = {"class_type": "LoadVideo", "inputs": {"file": "shared.mp4"}}
    wf["41"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["40", 0]}}
    wf["42"] = {"class_type": "PreviewImage", "inputs": {"images": ["41", 0]}}
    wf["5"]["inputs"]["ref_videos.ref_video_1"] = ["41", 0]
    genmedia._add_h3_references(wf, ["mine.png"], [], [])
    assert "20" not in wf and "21" not in wf and "30" not in wf
    assert "40" in wf and "41" in wf and "42" in wf
    keys = [k for k in wf["5"]["inputs"] if k.startswith(("ref_videos.", "ref_video_audios."))]
    assert keys == []
    assert wf[wf["5"]["inputs"]["ref_images.ref_image_0"][0]]["inputs"]["image"] == "mine.png"


def test_ref_video_caps_follow_workflow(monkeypatch):
    monkeypatch.setattr(genmedia, "_is_h3_ref2va_workflow", lambda cfg: True)
    assert genmedia.comfy_h3_ref_video_caps({"provider": "comfyui"}) == (3, 15.0)
    monkeypatch.setattr(genmedia, "_is_h3_ref2va_workflow", lambda cfg: False)
    assert genmedia.comfy_h3_ref_video_caps({"provider": "comfyui"}) is None
    def boom(cfg):
        raise RuntimeError("no workflow")
    monkeypatch.setattr(genmedia, "_is_h3_ref2va_workflow", boom)
    assert genmedia.comfy_h3_ref_video_caps({}) is None


@pytest.fixture
def ffmpeg():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")


def clip(path, fps, seconds, audio):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=red:s=160x90:r={fps}:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return path


def test_prepare_ref_video_transcodes_to_24fps(ffmpeg, tmp_path, monkeypatch):
    monkeypatch.setattr(genmedia, "H3_REF_VIDEO_CACHE_DIR", tmp_path / "cache")
    src = clip(tmp_path / "thirty.mp4", 30, 3, audio=True)
    out, has_audio = genmedia._h3_prepare_ref_video(str(src))
    meta = genmedia._probe_video_meta(out)
    assert has_audio and meta["has_audio"] and round(meta["fps"]) == 24 and out != str(src)
    assert genmedia._h3_prepare_ref_video(str(src))[0] == out   # 缓存命中,不重转
    ok = clip(tmp_path / "ok.mp4", 24, 3, audio=False)
    assert genmedia._h3_prepare_ref_video(str(ok)) == (str(ok), False)   # 已合规原样上传
    with pytest.raises(RuntimeError, match="至少 2s"):
        genmedia._h3_prepare_ref_video(str(clip(tmp_path / "short.mp4", 24, 1, audio=False)))
    long = clip(tmp_path / "long.mp4", 24, 17, audio=False)
    out, _ = genmedia._h3_prepare_ref_video(str(long))
    assert genmedia._audio_duration_s(out) == pytest.approx(15, abs=.2)
