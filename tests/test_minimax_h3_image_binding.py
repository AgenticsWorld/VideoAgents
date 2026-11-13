"""MiniMax-H3-Image 模板(latent 由 MiniMaxH3ImageConditioning 自产)的宿主绑定:
参考图槽位数、RunningHub 无占位符直绑的宽高(EmptyImage 画幅载体)。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules import genmedia  # noqa: E402

LITERALS = {'"{{WIDTH}}"': "2560", '"{{HEIGHT}}"': "1440", '"{{SEED}}"': "42",
            "{{PROMPT}}": "demo prompt", "{{FIRST_FRAME}}": "demo.png"}


def _cloud_text(kind: str) -> str:
    """本地模板去掉占位符,即 RunningHub 工作区导出件的样子。"""
    raw = (ROOT / "comfy" / f"image-minimax-h3-image-{kind}-api.json").read_text()
    for token, value in LITERALS.items():
        raw = raw.replace(token, value)
    assert "{{" not in raw
    return raw


def test_ref_slots_count_numbered_reference_inputs():
    template = json.loads((ROOT / "comfy" / "image-minimax-h3-image-i2i-api.json").read_text())
    assert genmedia._rh_image_ref_slots(template) == 10
    assert genmedia._rh_image_ref_slots(json.loads(_cloud_text("i2i"))) == 10


def test_ref_slots_zero_for_img2img_latent():
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
        "2": {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0], "vae": ["9", 0]}},
        "3": {"class_type": "KSampler", "inputs": {"latent_image": ["2", 0], "seed": 1}},
    }
    assert genmedia._rh_image_ref_slots(workflow) == 0


def test_rh_size_binds_empty_image_carrier():
    raw = _cloud_text("t2i")
    workflow = json.loads(raw)
    genmedia._apply_rh_image_bindings(raw, workflow, "a cat", "text", None, 7, 1440, 2560)
    assert (workflow["10"]["inputs"]["width"], workflow["10"]["inputs"]["height"]) == (1440, 2560)
    assert (workflow["90"]["inputs"]["width"], workflow["90"]["inputs"]["height"]) == (1440, 2560)
    assert workflow["11"]["inputs"]["megapixels"] == 4.0          # 生成像素数仍由模板决定
    assert workflow["4"]["inputs"]["prompt"] == "a cat\nExclude from the image: text"
    assert workflow["6"]["inputs"]["seed"] == 7


def test_rh_refs_bind_in_order_and_drop_unused_slots():
    raw = _cloud_text("i2i")
    workflow = json.loads(raw)
    genmedia._apply_rh_image_bindings(raw, workflow, "edit", "", ["a.png", "b.png"], 7, 2560, 1440)
    cond = workflow["4"]["inputs"]
    assert [k for k in cond if k.startswith("images.")] == ["images.image_1", "images.image_2"]
    assert workflow["101"]["inputs"]["image"] == "a.png" and workflow["102"]["inputs"]["image"] == "b.png"
    assert "103" not in workflow and "110" not in workflow
    assert (workflow["10"]["inputs"]["width"], workflow["10"]["inputs"]["height"]) == (2560, 1440)
