"""图像模型清单(apps/web/static/image-models.js)与 genmedia 的对应关系:Fal 清单里的模型都有内置请求体映射、
各模型的端点 / 字段 / 尺寸约束,OpenRouter 图像 API 的画幅 + 分辨率档,以及清单说明在各语言词典里都有译文。不联网。"""
import base64
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import modules.genmedia as g  # noqa: E402
from modules import scene_panos as sp  # noqa: E402

STATIC = ROOT / "apps" / "web" / "static"
U = lambda p: f"URL:{p}"  # noqa: E731


def model_lists() -> dict[str, list[tuple[str, str]]]:
    text = (STATIC / "image-models.js").read_text(encoding="utf-8")
    out = {}
    for name, block in re.findall(r"^(\w+):\[(.*?)^\],", text, re.S | re.M):
        out[name] = re.findall(r"\['([^']+)','([^']+)'\]", block)
    return out


def body(model, refs=(), size=(2560, 1440), seed=7):
    return g._fal_image_body({"model": model, "api_key": ""}, "p", "", list(refs), *size, seed, "o.png", U)


def test_lists_parse_and_carry_the_new_models():
    lists = model_lists()
    assert set(lists) == {"openrouter", "volcengine", "byteplus", "fal", "minimax"}
    openrouter, fal = dict(lists["openrouter"]), dict(lists["fal"])
    assert "google/gemini-nano-banana-2.1" in openrouter and "google/nano-banana-2.1" in fal
    for mid in ("openai/gpt-image-2.5-sunburst", "openai/gpt-image-2.5-flare"):
        assert "ChatGPT Images 2.5" in openrouter[mid]
    for mid in ("openai/gpt-image-2.5/sunburst", "openai/gpt-image-2.5/flare"):
        assert "ChatGPT Images 2.5" in fal[mid]
    for name, rows in lists.items():
        ids = [mid for mid, _ in rows]
        assert len(ids) == len(set(ids)), name


def test_fal_list_is_builtin_and_default_comes_first():
    from services.runtime import core
    fal = model_lists()["fal"]
    assert fal[0][0] == core.DEFAULT_GENCONFIG["image"]["fal"]["model"]
    for mid, _ in fal:
        assert g._fal_image_family(mid) != "generic", mid         # 清单里的模型不依赖联网查目录
        ep, _ = body(mid, size=(1920, 1080))
        assert ep.startswith(mid), (mid, ep)


def test_nano_banana_21_and_gpt_image_25_sunburst_requests():
    ep, b = body("google/nano-banana-2.1")
    assert ep == "google/nano-banana-2.1"
    assert b == {"prompt": "p", "aspect_ratio": "16:9", "resolution": "2K", "seed": 7, "output_format": "png"}
    ep, b = body("google/nano-banana-2.1", refs=["a.png", "b.png"], size=(1080, 1920))
    assert ep == "google/nano-banana-2.1/edit" and b["aspect_ratio"] == "9:16" and b["image_urls"] == ["URL:a.png", "URL:b.png"]
    with pytest.raises(RuntimeError):
        body("google/nano-banana-2.1", refs=[f"{i}.png" for i in range(15)])

    ep, b = body("openai/gpt-image-2.5/sunburst")
    assert ep == "openai/gpt-image-2.5/sunburst/text-to-image"
    assert b == {"prompt": "p", "image_size": {"width": 2560, "height": 1440}, "output_format": "png"}    # 无 seed
    ep, b = body("openai/gpt-image-2.5/sunburst", refs=["a.png"])
    assert ep == "openai/gpt-image-2.5/sunburst/edit" and b["image_urls"] == ["URL:a.png"]
    assert not sp.pano_support({"provider": "fal", "model": "google/nano-banana-2.1"})[0]


def gpt_ok(w, h):
    return (w % 16 == 0 and h % 16 == 0 and max(w, h) <= 3840 and max(w, h) / min(w, h) <= 3
            and 655_360 <= w * h <= 8_294_400)


def test_fal_gpt_image_sends_exact_sizes_within_its_constraints(capsys):
    for mid in ("openai/gpt-image-2.5/sunburst", "openai/gpt-image-2.5/flare", "openai/gpt-image-2"):
        for size in ((2560, 1440), (1280, 720), (2880, 1440), (1440, 2560), (3840, 2160)):      # 本来就合规:原样发
            capsys.readouterr()
            assert body(mid, size=size)[1]["image_size"] == {"width": size[0], "height": size[1]}
            assert capsys.readouterr().err == ""
    for size, want in (((1920, 1080), (1920, 1088)), ((2858, 1608), (2864, 1600)), ((4096, 4096), (2880, 2880)),
                       ((5000, 1000), (3840, 1280)), ((640, 360), (1088, 608))):
        got = body("openai/gpt-image-2.5/sunburst", size=size)[1]["image_size"]
        assert (got["width"], got["height"]) == want and gpt_ok(*want)
        assert f"已调整为 {want[0]}x{want[1]}" in capsys.readouterr().err
    for w in range(300, 6000, 233):                                     # 任意请求尺寸收进约束后都合规、画幅基本不变
        for h in range(300, 6000, 377):
            fw, fh = g._fit_size(w, h, **g.FAL_GPT_SIZE)
            assert gpt_ok(fw, fh), (w, h, fw, fh)
            if max(w, h) / min(w, h) <= 3:
                assert abs(fw / fh - w / h) / (w / h) < 0.06, (w, h, fw, fh)
    assert body("openai/gpt-image-2", refs=["a.png"])[0] == "openai/gpt-image-2/edit"
    assert sp.pano_support({"provider": "fal", "model": "openai/gpt-image-2.5/sunburst"})[0]     # 2880x1440 现在发得出去
    assert g.image_max_pixels({"provider": "fal", "model": "openai/gpt-image-2.5/sunburst"}) is None


def test_resolution_tier_goes_by_pixel_area():
    tiers = ["1K", "2K", "4K"]
    assert g._resolution_tier(1280, 720, tiers) == "1K"
    assert g._resolution_tier(1920, 1080, tiers) == "2K"
    assert g._resolution_tier(2560, 1440, tiers) == "2K"
    assert g._resolution_tier(2858, 1608, tiers) == "2K"               # 母图:长边过 2560 也不跳 4K
    assert g._resolution_tier(3840, 2160, tiers) == "4K"
    assert g._resolution_tier(5386, 3106, tiers) == "4K"
    assert g._resolution_tier(1920, 1080, ["768", "1K", "1.5K", "2K", "4K"]) == "1.5K"
    assert g._resolution_tier(640, 360, ["512", "1K", "2K", "4K"]) == "512"
    assert g._resolution_tier(4096, 4096, ["1K", "2K"]) == "2K"        # 都够不上:取最大一档
    assert g._resolution_tier(2560, 1440, ["1K"]) == "1K"
    assert g._resolution_tier(2560, 1440, []) is None and g._resolution_tier(2560, 1440, ["auto"]) is None
    assert g._tier_side("1.5K") == 1536 and g._tier_side("768sq") == 768 and g._tier_side("auto") is None


def test_fal_flux3_requests():
    ep, b = body("blackforestlabs/flux-3")
    assert ep == "blackforestlabs/flux-3/text-to-image"
    assert b == {"prompt": "p", "aspect_ratio": "16:9", "resolution": "2k", "output_format": "png"}      # 无 seed
    ep, b = body("blackforestlabs/flux-3", refs=["a.png", "b.png"], size=(2880, 1440))
    assert ep == "blackforestlabs/flux-3/edit-image"
    assert b["aspect_ratio"] == "2:1" and b["resolution"] == "2k" and b["image_urls"] == ["URL:a.png", "URL:b.png"]
    assert body("blackforestlabs/flux-3", size=(1280, 720))[1]["resolution"] == "1k"
    assert body("blackforestlabs/flux-3", size=(4096, 4096))[1]["resolution"] == "4k"
    assert body("blackforestlabs/flux-3/edit-image", refs=["a.png"])[0] == "blackforestlabs/flux-3/edit-image"
    with pytest.raises(RuntimeError):
        body("blackforestlabs/flux-3", refs=[f"{i}.png" for i in range(11)])
    assert sp.pano_support({"provider": "fal", "model": "blackforestlabs/flux-3"})[0]
    assert g._fal_image_family("fal-ai/flux-2-pro") == "flux2" and g._fal_image_family("fal-ai/flux-pro/kontext/max") == "kontext"


def test_fal_ideogram_v45_requests():
    ep, b = body("ideogram/v4.5")
    assert ep == "ideogram/v4.5" and b == {"prompt": "p", "image_size": {"width": 2560, "height": 1440}, "seed": 7}
    for size, want in (((1920, 1080), (2560, 1440)), ((1280, 720), (1280, 720)), ((2880, 1440), (2880, 1440)),
                       ((1080, 1920), (1440, 2560)), ((4096, 4096), (2048, 2048)), ((2858, 1608), (2560, 1440))):
        got = body("ideogram/v4.5", size=size)[1]["image_size"]
        assert (got["width"], got["height"]) == want and want in g.FAL_IDEOGRAM45_SIZES
    assert len(set(g.FAL_IDEOGRAM45_SIZES)) == len(g.FAL_IDEOGRAM45_SIZES)
    ep, b = body("ideogram/v4.5", refs=["a.png", "b.png", "c.png"], size=(1920, 1080))
    assert ep == "ideogram/v4.5/edit"
    assert b == {"prompt": "p", "image_size": {"width": 1920, "height": 1088}, "seed": 7,
                 "image_url": "URL:a.png", "reference_image_urls": ["URL:b.png", "URL:c.png"]}
    b = body("ideogram/v4.5", refs=["a.png"])[1]
    assert b["image_url"] == "URL:a.png" and "reference_image_urls" not in b and "image_urls" not in b
    for size in ((640, 360), (4096, 4096), (5000, 1000), (300, 3000)):
        got = body("ideogram/v4.5", refs=["a.png"], size=size)[1]["image_size"]
        w, h = got["width"], got["height"]
        assert w % 32 == 0 and h % 32 == 0 and min(w, h) >= 256 and w * h <= 2048 * 2048 and max(w, h) / min(w, h) <= 6
    with pytest.raises(RuntimeError):
        body("ideogram/v4.5", refs=[f"{i}.png" for i in range(6)])
    assert g._fal_image_family("fal-ai/ideogram/v3") == "generic"       # 其它 Ideogram 端点字段不同,不套这套映射


NANO_BANANA_21 = {"id": "google/gemini-nano-banana-2.1",
                  "architecture": {"input_modalities": ["image", "text"], "output_modalities": ["image", "text"]},
                  "supported_parameters": {
                      "resolution": {"type": "enum", "values": ["1K", "2K", "4K"]},
                      "aspect_ratio": {"type": "enum", "values": ["1:1", "1:4", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"]},
                      "n": {"type": "range", "min": 1, "max": 1},
                      "input_references": {"type": "range", "min": 0, "max": 14}}}
GPT_IMAGE_25 = {"id": "openai/gpt-image-2.5-sunburst",
                "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["image"]},
                "supported_parameters": {
                    "aspect_ratio": {"type": "enum", "values": ["1:1", "3:2", "2:3", "16:9", "9:16", "auto"]},
                    "quality": {"type": "enum", "values": ["auto", "low", "high"]}}}
SEEDREAM_FLASH = {"id": "bytedance-seed/seedream-5-0-flash",
                  "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["image"]},
                  "supported_parameters": {
                      "resolution": {"type": "enum", "values": ["1K", "2K"]},
                      "aspect_ratio": {"type": "enum", "values": ["1:1", "16:9", "9:19.5", "19.5:9", "auto"]},
                      "seed": {"type": "boolean"}}}


def test_openrouter_images_body_follows_the_catalog():
    def make(info, size=(2560, 1440), refs=(), seed=5, negative=""):
        return g._openrouter_images_body({"model": info["id"]}, "p", negative, list(refs), *size, seed, info, U)
    assert make(NANO_BANANA_21) == {"model": "google/gemini-nano-banana-2.1", "prompt": "p",
                                    "aspect_ratio": "16:9", "resolution": "2K"}            # 目录没列 seed:不发
    assert make(NANO_BANANA_21, size=(1280, 720))["resolution"] == "1K"
    assert make(NANO_BANANA_21, size=(1080, 1920))["aspect_ratio"] == "9:16"
    assert make(NANO_BANANA_21, size=(5386, 3106))["resolution"] == "4K"
    b = make(NANO_BANANA_21, refs=["a.png"], negative="blur")
    assert b["prompt"] == "p\nNegative (avoid): blur"
    assert b["input_references"] == [{"type": "image_url", "image_url": {"url": "URL:a.png"}}]
    assert make(GPT_IMAGE_25) == {"model": "openai/gpt-image-2.5-sunburst", "prompt": "p", "aspect_ratio": "16:9"}   # 没有分辨率档
    b = make(SEEDREAM_FLASH, size=(4096, 4096))
    assert b["resolution"] == "2K" and b["aspect_ratio"] == "1:1" and b["seed"] == 5
    assert make({"id": "x/unknown"})["aspect_ratio"] == "16:9" and "resolution" not in make({"id": "x/unknown"})


def test_openrouter_routes_cataloged_models_through_the_images_api(monkeypatch, capsys):
    png = base64.b64encode(b"img").decode()
    catalog = {m["id"]: m for m in (NANO_BANANA_21, GPT_IMAGE_25)}
    monkeypatch.setattr(g, "_openrouter_image_model_info", lambda model: catalog.get(model))
    calls = []
    images_api_status = {"code": 200}

    def fake_post(url, payload, headers=None, timeout=300):
        calls.append((url.rsplit("/api/v1", 1)[-1], payload))
        if url.endswith("/images"):
            if images_api_status["code"] != 200:
                raise g._HTTPStatusError(images_api_status["code"], url, "nope")
            return {"data": [{"b64_json": png}]}
        return {"choices": [{"message": {"images": [{"image_url": {"url": f"data:image/png;base64,{png}"}}]}}]}
    monkeypatch.setattr(g, "_post_json", fake_post)
    cfg = {"model": "google/gemini-nano-banana-2.1", "api_key": "k"}

    assert g._image_openrouter(cfg, "p", "", [], 2560, 1440, None) == b"img"
    assert [c[0] for c in calls] == ["/images"]                         # 图文双出模型也先走图像 API
    assert calls[0][1]["aspect_ratio"] == "16:9" and calls[0][1]["resolution"] == "2K"
    assert "分辨率档 2K" in capsys.readouterr().err

    calls.clear()
    images_api_status["code"] = 404                                     # 图像 API 没有这个模型:退回 chat
    assert g._image_openrouter(cfg, "p", "", [], 2560, 1440, None) == b"img"
    assert [c[0] for c in calls] == ["/images", "/chat/completions"]
    assert "Image size: 2560x1440" in calls[1][1]["messages"][0]["content"][0]["text"]

    calls.clear()
    images_api_status["code"] = 402                                     # 其它错误照常抛,不悄悄换接口
    with pytest.raises(g._HTTPStatusError):
        g._image_openrouter(cfg, "p", "", [], 2560, 1440, None)
    assert [c[0] for c in calls] == ["/images"]

    calls.clear()
    images_api_status["code"] = 200
    assert g._image_openrouter({"model": "openai/gpt-image-2.5-sunburst", "api_key": "k"}, "p", "", [], 2560, 1440, None) == b"img"
    assert [c[0] for c in calls] == ["/images"] and "resolution" not in calls[0][1]
    calls.clear()
    assert g._image_openrouter({"model": "x/not-in-catalog", "api_key": "k"}, "p", "", [], 2560, 1440, None) == b"img"
    assert [c[0] for c in calls] == ["/chat/completions"]               # 目录没收录:照旧走 chat


def test_fit_area_keeps_ratio_and_lands_inside_the_range():
    lo, hi = g.FAL_SEEDREAM_2K_AREA
    assert g._fit_area(2560, 1440, lo, hi) == (2560, 1440)
    assert g._fit_area(4096, 4096, lo, hi) == (2048, 2048)
    for w, h in ((2858, 1608), (5386, 3106), (1280, 720), (640, 360), (720, 1280), (3840, 1634)):
        fw, fh = g._fit_area(w, h, lo, hi)
        assert lo <= fw * fh <= hi and fw % 2 == 0 and fh % 2 == 0
        assert abs(fw / fh - w / h) < 0.01, (w, h, fw, fh)


def test_fal_seedream_5_pro_and_flash_stay_inside_their_area_range(capsys):
    for mid in ("bytedance/seedream/v5/pro", "bytedance/seedream/v5/flash"):
        capsys.readouterr()
        ep, b = body(mid)
        assert ep == f"{mid}/text-to-image" and b["image_size"] == {"width": 2560, "height": 1440}
        assert "output_format" not in b
        assert capsys.readouterr().err == ""
        ep, b = body(mid, refs=["a.png"], size=(2858, 1608))                  # 母图规格超过 2048x2048
        assert ep == f"{mid}/edit" and b["image_size"] == {"width": 2730, "height": 1536}
        assert "已调整为 2730x1536" in capsys.readouterr().err
        assert body(mid, size=(1280, 720))[1]["image_size"] == {"width": 1366, "height": 768}
        cfg = {"provider": "fal", "model": mid}
        assert g.image_max_pixels(cfg) == 2048 * 2048
        assert sp.pano_support(cfg)[0]                                         # 2880x1440 在范围内
        assert body(mid, size=(2880, 1440))[1]["image_size"] == {"width": 2880, "height": 1440}
    # 5.0 Lite / 4.5 仍按原请求尺寸发,越界交给 Fal 缩放
    assert body("fal-ai/bytedance/seedream/v5/lite", size=(4096, 4096))[1]["image_size"] == {"width": 4096, "height": 4096}
    assert g.image_max_pixels({"provider": "fal", "model": "fal-ai/bytedance/seedream/v5/lite"}) == 4096 * 4096
    assert g.image_max_pixels({"provider": "fal", "model": "google/nano-banana-2.1"}) == 4096 * 4096


def test_model_descriptions_are_translated_in_every_dictionary():
    labels = sorted({label for rows in model_lists().values() for _, label in rows
                     if re.search(r"[一-鿿]", label)})
    assert labels
    dicts = sorted((STATIC / "i18n").glob("??.js"))
    assert len(dicts) >= 11
    for path in dicts:
        text = path.read_text(encoding="utf-8")
        missing = [label for label in labels if f"\n{json.dumps(label, ensure_ascii=False)}: " not in text]
        assert not missing, (path.name, missing)
