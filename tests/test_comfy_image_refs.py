"""ComfyUI / RunningHub 图像工作流:多张 --ref 拼成一张联络图、无负面位的云端模板把 --negative 并入正面
(故事板草图经 RunningHub Krea 模板出图时踩到的两处报错,2026-09-16)。"""
import json
from pathlib import Path

import pytest

from modules import genmedia

PIL = pytest.importorskip("PIL")


def krea_t2i():
    """Krea2 文生图云端模板缩影:负面走 ConditioningZeroOut,无 {{TOKEN}} 占位符。"""
    return {
        "93": {"class_type": "Text", "inputs": {"text": "demo prompt"}},
        "51": {"class_type": "CLIPTextEncode", "inputs": {"text": ["93", 0], "clip": ["56", 0]}},
        "77": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["51", 0]}},
        "52": {"class_type": "EmptyLatentImage", "inputs": {"width": 1920, "height": 1080, "batch_size": 1}},
        "53": {"class_type": "KSampler", "inputs": {"seed": 1, "positive": ["51", 0], "negative": ["77", 0],
                                                    "latent_image": ["52", 0], "model": ["55", 0]}},
        "54": {"class_type": "VAEDecode", "inputs": {"samples": ["53", 0], "vae": ["57", 0]}},
        "29": {"class_type": "SaveImage", "inputs": {"images": ["54", 0]}},
    }


def krea_i2i():
    """Krea2 图生图云端模板缩影:SamplerCustomAdvanced + BasicGuider(只有正面),提示词来自图像反推节点。"""
    return {
        "39": {"class_type": "LoadImage", "inputs": {"image": "demo.png"}},
        "47": {"class_type": "AILab_QwenVL", "inputs": {"image": ["39", 0], "custom_prompt": "describe"}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"text": ["47", 0], "clip": ["29", 0]}},
        "13": {"class_type": "BasicGuider", "inputs": {"conditioning": ["9", 0], "model": ["28", 0]}},
        "12": {"class_type": "RandomNoise", "inputs": {"noise_seed": 5}},
        "40": {"class_type": "VAEEncode", "inputs": {"pixels": ["39", 0], "vae": ["38", 0]}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {"guider": ["13", 0], "noise": ["12", 0],
                                                                  "latent_image": ["40", 0]}},
        "18": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["38", 0]}},
        "20": {"class_type": "SaveImage", "inputs": {"images": ["18", 0]}},
    }


def sd_t2i():
    """标准 SD 模板:正负各一个 CLIPTextEncode。"""
    return {
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "pos", "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "neg", "clip": ["4", 1]}},
        "3": {"class_type": "KSampler", "inputs": {"seed": 1, "positive": ["6", 0], "negative": ["7", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}},
    }


def test_negative_slot_detection():
    assert genmedia._rh_image_negative_supported(json.dumps(sd_t2i()))
    assert genmedia._rh_image_negative_supported('{"x": "{{NEGATIVE}}"}')
    assert not genmedia._rh_image_negative_supported(json.dumps(krea_t2i()))
    assert not genmedia._rh_image_negative_supported(json.dumps(krea_i2i()))


def _png(path: Path, size, color="red"):
    from PIL import Image
    Image.new("RGB", size, color).save(path)
    return str(path)


def test_ref_sheet_equal_height_side_by_side(tmp_path):
    from PIL import Image
    a = _png(tmp_path / "a.png", (400, 200))
    b = _png(tmp_path / "b.png", (100, 300), "blue")
    sheet = genmedia._compose_ref_sheet([a, b], height=100, gutter=10)
    try:
        im = Image.open(sheet)
        # a 缩到 200×100,b 缩到 33×100,三条 10px 留白
        assert im.size == (200 + 33 + 30, 120)
        assert im.getpixel((15, 15)) == (255, 0, 0)
        assert im.getpixel((200 + 20 + 5, 60)) == (0, 0, 255)
    finally:
        sheet.unlink(missing_ok=True)


def _rh_cfg():
    return {"provider": "comfyui", "mode": "rh_ai", "rh_api_key_ai": "k", "negative_mode": "conditioning",
            "rh_workflow_id": "t2i", "rh_ref_workflow_id": "i2i"}


def test_rh_multi_ref_and_negative_fold(tmp_path, monkeypatch):
    """两张 --ref + 负面提示词经 Krea 图生图模板:上传的是一张联络图,提示词直绑并带 Exclude 段,不再报错。"""
    templates = {"t2i": json.dumps(krea_t2i()), "i2i": json.dumps(krea_i2i())}
    uploaded, submitted = [], {}
    monkeypatch.setattr(genmedia, "_rh_workflow_text", lambda cfg: templates[cfg["rh_workflow_id"]])
    monkeypatch.setattr(genmedia, "_rh_upload", lambda cfg, path: uploaded.append(Path(path).is_file()) or "up.png")
    monkeypatch.setattr(genmedia, "_rh_run", lambda cfg, wf, output, want_video: submitted.update(cfg=cfg, wf=wf) or output)
    refs = [_png(tmp_path / "a.png", (64, 64)), _png(tmp_path / "b.png", (64, 64))]
    genmedia._image_comfyui(_rh_cfg(), "P", "color, text", refs, 1280, 720, 7, str(tmp_path / "out.png"))
    assert uploaded == [True] and submitted["cfg"]["rh_workflow_id"] == "i2i"
    wf = submitted["wf"]
    assert wf["39"]["inputs"]["image"] == "up.png"
    assert wf["9"]["inputs"]["text"] == "P\nExclude from the image: color, text"
    assert wf["12"]["inputs"]["noise_seed"] == 7


def test_rh_t2i_negative_fold_without_refs(monkeypatch, tmp_path):
    monkeypatch.setattr(genmedia, "_rh_workflow_text", lambda cfg: json.dumps(krea_t2i()))
    submitted = {}
    monkeypatch.setattr(genmedia, "_rh_run", lambda cfg, wf, output, want_video: submitted.update(wf=wf) or output)
    genmedia._image_comfyui(_rh_cfg(), "P", "color", None, 1280, 720, 3, str(tmp_path / "o.png"))
    assert submitted["wf"]["93"]["inputs"]["text"] == "P\nExclude from the image: color"
    assert submitted["wf"]["53"]["inputs"]["seed"] == 3


def test_rh_template_with_negative_slot_keeps_conditioning(monkeypatch, tmp_path):
    monkeypatch.setattr(genmedia, "_rh_workflow_text", lambda cfg: json.dumps(sd_t2i()))
    submitted = {}
    monkeypatch.setattr(genmedia, "_rh_run", lambda cfg, wf, output, want_video: submitted.update(wf=wf) or output)
    genmedia._image_comfyui(_rh_cfg(), "P", "N", None, 1280, 720, 3, str(tmp_path / "o.png"))
    assert submitted["wf"]["6"]["inputs"]["text"] == "P" and submitted["wf"]["7"]["inputs"]["text"] == "N"
