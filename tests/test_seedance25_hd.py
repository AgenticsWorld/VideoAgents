"""Seedance 2.5 放开 1080p / 4k(2026-11-26,用户拍板)。

火山 docs/ark/seedance-2-5 与 BytePlus docs/ModelArk/seedance-2-5 两家教程一致:Seedance 2.5 输出 480p / 720p(8bit)、
1080p / 4k(10bit,H.265/HEVC);2.5 Flash 仍只有 480p / 720p;基于 Draft 出正式视频支持 1080p 或 4K。
只对方舟直连放开(OpenRouter 的 Seedance 2.5 目录口径仍是 480p/720p)。10bit HEVC 产物下载后转成 H.264 8bit,
后期链路多处 -c copy 拼接与网页预览不受影响。
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import modules.genmedia as g  # noqa: E402

V25 = ("doubao-seedance-2-5-260628", "dreamina-seedance-2-5-260628")


def _res(model, resolution, allow_hd=True):
    return g._seedance_precheck(model, "a girl walks", None, None, 10, resolution, "16:9",
                                [], [], [], allow_hd=allow_hd)[0]


@pytest.mark.parametrize("model", V25)
def test_ark_seedance25_keeps_1080p_and_4k(model):
    for r in ("480p", "720p", "1080p", "4k"):
        assert _res(model, r) == r
    assert _res(model, "4K") == "4k"                                    # 方舟取小写
    assert _res(model, "2k") == "1080p"                                 # 不往 4k 取(费用)
    assert _res(model, "360p") == "480p"


def test_openrouter_and_flash_stay_at_720p(capsys):
    assert _res("bytedance/seedance-2.5", "1080p", allow_hd=False) == "720p"
    assert _res("bytedance/seedance-2.5", "360p", allow_hd=False) == "480p"
    assert _res("doubao-seedance-2-5-flash-261115", "4k") == "720p"
    assert "支持 480p/720p" in capsys.readouterr().err
    assert _res("doubao-seedance-2-0-260128", "1080p") == "1080p"       # 2.0 不经这条修正


@pytest.mark.parametrize("res", ["1080p", "4k"])
def test_ark_body_sends_hd_resolution(res):
    body = g._ark_video_body({"provider": "volcengine", "model": V25[0], "api_key": "k"}, "a girl", "", "", 5, res,
                             "16:9", None, [], [], None, False, to_url=lambda p: f"file://{p}")
    assert f"--resolution {res}" in body["content"][0]["text"]


@pytest.fixture
def draft_final(monkeypatch, tmp_path):
    sent = {}
    monkeypatch.setattr(g, "_volc_draft_task", lambda inp, tid: {"id": "cgt-draft", "provider": "volcengine",
                                                                  "model": V25[0]})
    monkeypatch.setattr(g, "_ark_key_for", lambda prov: {"provider": prov, "api_key": "k", "model": V25[0]})
    monkeypatch.setattr(g, "_post_json", lambda url, body, headers: sent.setdefault("body", body) and {"id": "cgt-final"})
    monkeypatch.setattr(g, "_ark_wait_and_download",
                        lambda cfg, tid, output, resolution, duration, lf: sent.update(wait=resolution) or output)
    return sent, str(tmp_path / "grp001.mp4")


@pytest.mark.parametrize("res", ["1080p", "4k", "4K"])
def test_draft_final_accepts_1080p_and_4k(draft_final, res):
    sent, out = draft_final
    saved, meta = g._upscale_volcengine("src.mp4", "", out, res)
    assert saved == out and meta["task_id"] == "cgt-final"
    assert sent["body"] == {"model": V25[0], "content": [{"type": "draft_task", "draft_task": {"id": "cgt-draft"}}],
                            "resolution": res.lower()}
    assert sent["wait"] == res.lower()


@pytest.mark.parametrize("res", ["720p", "2k", ""])
def test_draft_final_rejects_other_resolutions(draft_final, res):
    with pytest.raises(RuntimeError, match="仅支持 1080p / 4k"):
        g._upscale_volcengine("src.mp4", "", draft_final[1], res)
    assert "body" not in draft_final[0]                                 # 拒在提交前,不建任务


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,pix_fmt",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return {s["codec_type"]: s for s in json.loads(out.stdout)["streams"]}


def _has_encoder(name):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
    return f" {name} " in out.stdout


@pytest.mark.skipif(not _has_encoder("libx265"), reason="需要 ffmpeg + libx265")
def test_hevc_10bit_download_is_converted_to_h264_8bit(tmp_path):
    clip = tmp_path / "grp001.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
                    "-x265-params", "log-level=error", "-tag:v", "hvc1", "-c:a", "aac", "-shortest", str(clip)], check=True)
    assert _probe(clip)["video"]["codec_name"] == "hevc"
    assert g._ensure_h264_8bit(str(clip)) is True
    streams = _probe(clip)
    assert (streams["video"]["codec_name"], streams["video"]["pix_fmt"]) == ("h264", "yuv420p")
    assert streams["audio"]["codec_name"] == "aac"                      # 音轨流拷贝保留
    assert not list(tmp_path.glob(".*tmp*"))                            # 临时文件已清


@pytest.mark.skipif(not _has_encoder("libx264"), reason="需要 ffmpeg + libx264")
def test_h264_8bit_and_unreadable_files_are_left_alone(tmp_path):
    clip = tmp_path / "grp002.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], check=True)
    before = clip.read_bytes()
    assert g._ensure_h264_8bit(str(clip)) is False and clip.read_bytes() == before
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")
    assert g._ensure_h264_8bit(str(junk)) is False and junk.read_bytes() == b"not a video"
