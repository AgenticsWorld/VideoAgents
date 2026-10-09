"""ComfyUI / RunningHub 图像工作流:多张 --ref 按序绑模板的多个 LoadImage(张数超过 LoadImage 个数提交前报错)、
无负面位的云端模板把 --negative 并入正面(故事板草图经 RunningHub Krea 模板出图时踩到的两处报错,2026-09-16;
2026-09-18 起多参考图改为逐张绑 LoadImage,不再拼联络图)。"""
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


def qwen_edit_i2i():
    """多参考图模板缩影:两个 LoadImage 经 TextEncodeQwenImageEditPlus 的 image1/image2 位,
    节点 id 序(30 < 31)与参考图位序相反。"""
    return {
        "30": {"class_type": "LoadImage", "inputs": {"image": "demo2.png"}},
        "31": {"class_type": "LoadImage", "inputs": {"image": "demo1.png"}},
        "6": {"class_type": "TextEncodeQwenImageEditPlus", "inputs": {"prompt": "demo", "image1": ["31", 0],
                                                                     "image2": ["30", 0], "clip": ["4", 1]}},
        "52": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {"seed": 1, "positive": ["6", 0], "negative": ["6", 0],
                                                   "latent_image": ["52", 0], "model": ["4", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}},
    }


def _png(path: Path, size, color="red"):
    from PIL import Image
    Image.new("RGB", size, color).save(path)
    return str(path)


def _rh_cfg():
    return {"provider": "comfyui", "mode": "rh_ai", "rh_api_key_ai": "k", "negative_mode": "conditioning",
            "rh_workflow_id": "t2i", "rh_ref_workflow_id": "i2i"}


def _stub_rh(monkeypatch, templates):
    uploaded, submitted = [], {}
    monkeypatch.setattr(genmedia, "_rh_workflow_text", lambda cfg: templates[cfg["rh_workflow_id"]])
    monkeypatch.setattr(genmedia, "_rh_upload", lambda cfg, path: uploaded.append(Path(path).name) or Path(path).name)
    monkeypatch.setattr(genmedia, "_rh_run", lambda cfg, wf, output, want_video: submitted.update(cfg=cfg, wf=wf) or output)
    return uploaded, submitted


def test_rh_ref_and_negative_fold(tmp_path, monkeypatch):
    """一张 --ref + 负面提示词经 Krea 图生图模板:走图生图工作流,提示词直绑并带 Exclude 段,不再报错。"""
    uploaded, submitted = _stub_rh(monkeypatch, {"t2i": json.dumps(krea_t2i()), "i2i": json.dumps(krea_i2i())})
    refs = [_png(tmp_path / "a.png", (64, 64))]
    genmedia._image_comfyui(_rh_cfg(), "P", "color, text", refs, 1280, 720, 7, str(tmp_path / "out.png"))
    assert uploaded == ["a.png"] and submitted["cfg"]["rh_workflow_id"] == "i2i"
    wf = submitted["wf"]
    assert wf["39"]["inputs"]["image"] == "a.png"
    assert wf["9"]["inputs"]["text"] == "P\nExclude from the image: color, text"
    assert wf["12"]["inputs"]["noise_seed"] == 7


def test_rh_multi_ref_binds_load_images_in_slot_order(tmp_path, monkeypatch):
    """多张 --ref 按 image1/image2 位序绑 LoadImage(不按节点 id 序)。"""
    uploaded, submitted = _stub_rh(monkeypatch, {"t2i": json.dumps(krea_t2i()), "i2i": json.dumps(qwen_edit_i2i())})
    refs = [_png(tmp_path / "a.png", (64, 64)), _png(tmp_path / "b.png", (64, 64), "blue")]
    genmedia._image_comfyui(_rh_cfg(), "P", "", refs, 1280, 720, 7, str(tmp_path / "out.png"))
    assert uploaded == ["a.png", "b.png"]
    wf = submitted["wf"]
    assert wf["31"]["inputs"]["image"] == "a.png" and wf["30"]["inputs"]["image"] == "b.png"


def test_rh_more_refs_than_load_images_rejected(tmp_path, monkeypatch):
    """参考图多于模板 LoadImage 个数:提交前报错,不发请求。"""
    _, submitted = _stub_rh(monkeypatch, {"t2i": json.dumps(krea_t2i()), "i2i": json.dumps(krea_i2i())})
    refs = [_png(tmp_path / "a.png", (64, 64)), _png(tmp_path / "b.png", (64, 64))]
    with pytest.raises(RuntimeError, match="只有 1 个 LoadImage"):
        genmedia._image_comfyui(_rh_cfg(), "P", "", refs, 1280, 720, 7, str(tmp_path / "out.png"))
    assert submitted == {}


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


def test_rh_negative_placeholder_not_folded(monkeypatch, tmp_path):
    """模板带 {{NEGATIVE}} 占位符:负面填进占位符,不并入正面。"""
    tpl = sd_t2i()
    tpl["6"]["inputs"]["text"], tpl["7"]["inputs"]["text"] = "{{PROMPT}}", "{{NEGATIVE}}"
    monkeypatch.setattr(genmedia, "_rh_workflow_text", lambda cfg: json.dumps(tpl))
    submitted = {}
    monkeypatch.setattr(genmedia, "_rh_run", lambda cfg, wf, output, want_video: submitted.update(wf=wf) or output)
    genmedia._image_comfyui(_rh_cfg(), "P", "N", None, 1280, 720, 3, str(tmp_path / "o.png"))
    assert submitted["wf"]["6"]["inputs"]["text"] == "P" and submitted["wf"]["7"]["inputs"]["text"] == "N"
