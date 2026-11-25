"""图生 3D(2026-11-25,modules/model3d.py + genmedia model3d):渠道解析、多视图按模型取舍、各渠道请求体、
Tripo / 本地 Hunyuan3D 走本机临时 HTTP 服务端到端出 GLB。不联网、不计费。"""
import json
import os
import struct
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules import model3d  # noqa: E402


def make_glb() -> bytes:
    """最小可渲染 GLB:一个立方体(双面材质),给接口与页面测试用。"""
    pos = [(-.5, -.5, -.5), (.5, -.5, -.5), (.5, .5, -.5), (-.5, .5, -.5), (-.5, -.5, .5), (.5, -.5, .5), (.5, .5, .5), (-.5, .5, .5)]
    idx = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 2, 3, 7, 2, 7, 6, 1, 2, 6, 1, 6, 5, 0, 4, 7, 0, 7, 3]
    pb = b"".join(struct.pack("<3f", *p) for p in pos)
    ib = b"".join(struct.pack("<H", i) for i in idx)
    bin_ = pb + ib
    gltf = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
            "materials": [{"doubleSided": True, "pbrMetallicRoughness": {"baseColorFactor": [.8, .4, .2, 1], "metallicFactor": 0}}],
            "buffers": [{"byteLength": len(bin_)}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pb)},
                            {"buffer": 0, "byteOffset": len(pb), "byteLength": len(ib)}],
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3", "min": [-.5] * 3, "max": [.5] * 3},
                          {"bufferView": 1, "componentType": 5123, "count": 36, "type": "SCALAR"}]}
    j = json.dumps(gltf).encode()
    j += b" " * (-len(j) % 4)
    total = 12 + 8 + len(j) + 8 + len(bin_)
    return (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(j), b"JSON") + j
            + struct.pack("<I4s", len(bin_), b"BIN\x00") + bin_)


GLB = make_glb()


def png(path: Path, size=(64, 64), color=(200, 30, 30)) -> Path:
    from PIL import Image
    Image.new("RGB", size, color).save(path)
    return path


@pytest.fixture(autouse=True)
def zh(monkeypatch):
    monkeypatch.setenv("VIDEOAGENTS_UI_LANG", "zh")
    for k in ("TRIPO_API_KEY", "FAL_KEY", "RUNNINGHUB_API_KEY"):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------- 渠道解析
def test_resolve_defaults_and_key_fallbacks():
    cfg = {"model3d": {"provider": "fal", "fal": {"api_key": "", "model": "meshy/v7.1"},
                       "rhapi": {"site": "cn", "api_key_cn": "", "i23d": "tripo-h/image-to-3d-v3.1"},
                       "tripo": {"site": "cn", "api_key_ai": "A", "api_key_cn": ""},
                       "hunyuan3d": {"base_url": "192.168.1.9:8080/", "model": "hunyuan3d-dit-v2-0", "texture": True}},
           "image": {"fal": {"api_key": "FK"}}, "video": {"rhapi": {"api_key_cn": "RK"}}}
    r = model3d.resolve(cfg)
    assert (r["provider"], r["api_key"], r["model"]) == ("fal", "FK", "meshy/v7.1")      # Fal Key 借图像段的
    r = model3d.resolve(cfg, "rhapi")
    assert (r["site"], r["api_key"], r["model"]) == ("cn", "RK", "tripo-h/image-to-3d-v3.1")
    with pytest.raises(model3d.Model3DError, match="tripo3d.com"):                    # 国内站没 Key;国际站的不串用
        model3d.resolve(cfg, "tripo")
    r = model3d.resolve(cfg, "hunyuan3d")
    assert r["base_url"] == "http://192.168.1.9:8080" and r["texture"] is True
    with pytest.raises(model3d.Model3DError, match="Unknown 3D provider"):
        model3d.resolve(cfg, "meshy", zh=False)
    # 自定义模型:model 与 custom_model 同值
    r = model3d.resolve({"model3d": {"tripo": {"site": "ai", "api_key_ai": "k", "model": "", "custom_model": "v9"}}}, "tripo")
    assert r["model"] == "v9"


def test_options_lists_models_with_multiview_and_configured(monkeypatch, tmp_path):
    from modules import rh_models
    monkeypatch.setattr(rh_models, "CACHE_DIR", tmp_path)
    cat = [{"id": "tripo-h/image-to-3d-v3.1", "name": "tripo/image-to-3d-v3.1", "category": "Other", "task": "image-to-3d",
            "schema": {"properties": {"imageUrl": {"type": "string"}}}},
           {"id": "tripo-h/multiview-to-3d-v3.1", "name": "tripo/multiview-to-3d-v3.1", "category": "Other", "task": "multiview-to-3d",
            "schema": {"properties": {"frontImageUrl": {"type": "string"}}}},
           {"id": "meshy6/image-to-3d", "name": "meshy6/image-to-3d", "category": "3D", "task": "image-to-3d",
            "schema": {"properties": {"imageUrl": {"type": "string"}}}},
           {"id": "hunyuan3d-v3.1/image-to-3d", "name": "hunyuan3d-v3.1/image-to-3d", "category": "3D", "task": "image-to-3d",
            "schema": {"properties": {"imageUrl": {}, "leftImageUrl": {}}}},
           {"id": "x/text-to-3d", "name": "x", "category": "3D", "task": "text-to-3d", "schema": {"properties": {}}}]
    (tmp_path / "ai.json").write_text(json.dumps({"version": 2, "models": cat}), encoding="utf-8")
    cfg = {"model3d": {"provider": "rhapi", "rhapi": {"site": "ai", "api_key_ai": "k", "i23d": ""},
                       "fal": {"model": "fal-ai/custom/x"}, "hunyuan3d": {"base_url": ""}}}
    out = model3d.options(cfg)
    rows = {r["provider"]: r for r in out["providers"]}
    assert not rows["tripo"]["configured"] and "Tripo" in rows["tripo"]["hint"]
    assert all(m["multiview"] for m in rows["tripo"]["models"])
    fal = {m["id"]: m["multiview"] for m in rows["fal"]["models"]}
    assert fal["fal-ai/hunyuan-3d/v3.1/pro"] and fal["fal-ai/hunyuan-3d/v3.1/rapid"] is False and fal["fal-ai/custom/x"] is None
    rh = {m["id"]: m["multiview"] for m in rows["rhapi"]["models"]}
    assert rows["rhapi"]["configured"]                                                  # 没选 i23d 也能在窗口里挑
    assert rh == {"hunyuan3d-v3.1/image-to-3d": True, "meshy6/image-to-3d": False, "tripo-h/image-to-3d-v3.1": True}
    assert rows["hunyuan3d"]["configured"] and rows["hunyuan3d"]["models"][0]["multiview"] is False   # 空地址 = 默认本机
    assert out["default"]["provider"] == "rhapi" and out["views"] == ["front", "left", "back", "right"]


# ---------------------------------------------------------------- 各渠道请求体
def _fal_entry(eid, props, required=()):
    return {"endpoint_id": eid, "input": {"props": {k: {"types": t, "enum": e} for k, (t, e) in props.items()},
                                          "required": list(required)}}


def test_fal_body_named_slots_list_order_and_texture_off():
    to_url = lambda p: f"data:{p}"  # noqa: E731
    views = {"front": "F", "back": "B", "right": "R"}
    hy = _fal_entry("fal-ai/hunyuan-3d/v3.1/pro/image-to-3d", {
        "input_image_url": (["string"], None), "back_image_url": (["string", "null"], None),
        "left_image_url": (["string", "null"], None), "right_image_url": (["string", "null"], None),
        "enable_pbr": (["boolean"], None), "generate_type": (["string"], ["Normal", "Geometry"])})
    body, used = model3d.fal_body(hy, views, False, to_url, [])
    assert body == {"input_image_url": "data:F", "back_image_url": "data:B", "right_image_url": "data:R",
                    "enable_pbr": False, "generate_type": "Geometry"} and used == ["front", "back", "right"]
    tr = _fal_entry("tripo3d/h3.1/multiview-to-3d", {"image_urls": (["array"], None), "texture": (["boolean"], None),
                                                     "pbr": (["boolean"], None)})
    body, used = model3d.fal_body(tr, {"front": "F", "left": "L", "back": "B"}, True, to_url, [])
    assert body == {"image_urls": ["data:F", "data:L", "data:B"]} and used == ["front", "left", "back"]   # 按 [正, 左, 后, 右]
    one = _fal_entry("tripo3d/p2/image-to-3d", {"image_url": (["string"], None), "texture": (["boolean"], None)})
    body, used = model3d.fal_body(one, views, True, to_url, [])
    assert body == {"image_url": "data:F"} and used == ["front"]
    rodin = _fal_entry("fal-ai/hyper3d/rodin/v2.5", {"image_urls": (["array"], None),
                                                    "geometry_file_format": (["string"], ["glb", "usdz", "fbx"])})
    body, _ = model3d.fal_body(rodin, {"front": "F"}, True, to_url, [])
    assert body["geometry_file_format"] == "glb"
    assert model3d._fal_candidates("tripo3d/h3.1", 2)[0] == "tripo3d/h3.1/multiview-to-3d"
    assert model3d._fal_candidates("meshy/v7.1/image-to-3d", 3) == ["meshy/v7.1/image-to-3d"]   # 写全端点就只用它
    res = {"model_mesh": {"url": "https://x/a.glb"}, "rendered_image": {"url": "https://x/p.png"}}
    assert model3d._find_glb_url(res) == "https://x/a.glb"
    assert model3d._find_glb_url({"model_urls": {"glb": {"url": "https://x/m?sig=1", "content_type": "model/gltf-binary"}}}) == "https://x/m?sig=1"


def test_rh_body_views_required_defaults_and_demo_urls():
    tripo_mv = {"id": "tripo-h/multiview-to-3d-v3.1", "task": "multiview-to-3d", "schema": {"properties": {
        "inputMode": {"type": "string", "enum": ["views", "multiviewTaskId"], "default": "views"},
        "frontImageUrl": {"type": "string"}, "leftImageUrl": {"type": "string"}, "backImageUrl": {"type": "string"},
        "rightImageUrl": {"type": "string"}, "texture": {"type": "boolean", "default": True},
        "pbr": {"type": "boolean", "default": True}}, "required": []}}
    views = {"front": 1, "back": 1}
    assert model3d.rh_views_for(tripo_mv, views) == ["front", "back"]
    body = model3d.rh_body(tripo_mv, {"front": "uF", "back": "uB"}, False)
    assert body == {"frontImageUrl": "uF", "backImageUrl": "uB", "inputMode": "views", "texture": False, "pbr": False}
    hitem = {"id": "hitem3d-v3/image-to-3d", "task": "image-to-3d", "schema": {"properties": {
        "requestType": {"type": "string", "enum": ["mesh", "both"], "default": "mesh"},
        "imageUrl": {"type": "string", "default": "https://www.runninghub.cn/view?filename=demo.png"},
        "resolution": {"type": "string", "enum": ["2048quality", "2048master"], "default": "2048quality"},
        "face": {"type": "integer", "default": 2000000}}, "required": ["imageUrl", "requestType", "resolution"]}}
    body = model3d.rh_body(hitem, {"front": "uF"}, True)
    assert body == {"requestType": "both", "resolution": "2048quality", "imageUrl": "uF"}         # 演示图默认值不混进来
    hy = {"id": "hunyuan3d-v3.1/image-to-3d", "task": "image-to-3d", "schema": {"properties": {
        "imageUrl": {}, "leftImageUrl": {"default": "https://www.runninghub.cn/view?filename=l.png"},
        "backImageUrl": {}, "rightImageUrl": {}, "generateType": {"type": "string", "enum": ["Normal", "Geometry", "Sketch"]}}}}
    assert model3d.rh_views_for(hy, {"front": 1, "left": 1, "right": 1}) == ["front", "left", "right"]
    body = model3d.rh_body(hy, {"front": "uF", "right": "uR"}, False)
    assert body == {"imageUrl": "uF", "rightImageUrl": "uR", "generateType": "Geometry"}         # 没给左侧就不带演示图
    miora = {"id": "miora-3d/image-to-3d", "task": "image-to-3d", "schema": {"properties": {
        "imageUrl": {}, "multiViewImages": {"type": "array"}, "enablePbr": {"type": "boolean", "default": False},
        "generateType": {"type": "string", "enum": ["normal", "geometry"], "default": "normal"}},
        "required": ["enablePbr", "generateType", "imageUrl"]}}
    body = model3d.rh_body(miora, {"front": "uF", "back": "uB", "left": "uL"}, True)
    assert body == {"enablePbr": False, "generateType": "normal", "imageUrl": "uF", "multiViewImages": ["uL", "uB"]}


def test_rh_pick_switches_to_sibling_endpoint(monkeypatch):
    from modules import rh_models
    img = {"id": "tripo-h/image-to-3d-v3.1", "task": "image-to-3d", "schema": {"properties": {"imageUrl": {}}}}
    mv = {"id": "tripo-h/multiview-to-3d-v3.1", "task": "multiview-to-3d", "schema": {"properties": {"frontImageUrl": {}}}}
    h_img = {"id": "hitem3d-v3/image-to-3d", "task": "image-to-3d", "schema": {"properties": {"imageUrl": {}}}}
    h_mv = {"id": "hitem3d-v3/multi-image-to-3d", "task": "multiview-to-3d", "schema": {"properties": {"frontImageUrl": {}}}}
    solo = {"id": "zz/multiview-to-3d", "task": "multiview-to-3d", "schema": {"properties": {}}}
    monkeypatch.setattr(rh_models, "catalog", lambda site, refresh=False: [img, mv, h_img, h_mv, solo])
    assert model3d._rh_pick("ai", img, 3)["id"] == mv["id"]
    assert model3d._rh_pick("ai", img, 1)["id"] == img["id"]
    assert model3d._rh_pick("ai", h_img, 2)["id"] == h_mv["id"]
    assert model3d._rh_pick("ai", mv, 1)["id"] == img["id"]
    with pytest.raises(model3d.Model3DError, match="至少要正面"):
        model3d._rh_pick("ai", solo, 1)


# ---------------------------------------------------------------- 参考图整理 / GLB 校验
def test_prep_image_and_glb_check(tmp_path):
    from PIL import Image
    small = png(tmp_path / "s.png")
    assert model3d._prep_image(small, tmp_path, "front") == small                          # 合规的原样用
    big = tmp_path / "b.webp"
    Image.new("RGBA", (3000, 1500), (0, 0, 0, 0)).save(big)
    out = model3d._prep_image(big, tmp_path, "back")
    im = Image.open(out)
    assert out.suffix == ".png" and max(im.size) == 2048 and im.mode == "RGBA"             # 透明底存 PNG、缩到 2048
    with pytest.raises(model3d.Model3DError, match="不存在"):
        model3d._prep_image(tmp_path / "nope.png", tmp_path, "x")
    assert model3d.check_glb(GLB) == GLB
    with pytest.raises(model3d.Model3DError, match="不是 GLB"):
        model3d.check_glb(b'{"error": "x"}', "Tripo")


# ---------------------------------------------------------------- 端到端:本机假服务
class _Fake(BaseHTTPRequestHandler):
    calls: list = []
    mode = "ok"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        _Fake.calls.append(("POST", self.path, self.headers.get("Authorization"), raw))
        if self.path == "/v3/files":
            return self._send(200, {"code": 0, "data": {"file_token": f"tok{len(_Fake.calls)}"}})
        if self.path.startswith("/v3/generation/"):
            return self._send(200, {"code": 0, "data": {"task_id": "t1"}})
        if self.path == "/generate":
            if _Fake.mode == "fail":
                return self._send(404, {"text": "NETWORK ERROR DUE TO HIGH TRAFFIC.", "error_code": 1})
            return self._send(200, GLB, "application/octet-stream")
        self._send(404, {})

    def do_GET(self):  # noqa: N802
        _Fake.calls.append(("GET", self.path, self.headers.get("Authorization"), b""))
        if self.path == "/v3/tasks/t1":
            port = self.server.server_address[1]
            return self._send(200, {"code": 0, "data": {"status": "success", "progress": 100,
                                                        "output": {"model_url": f"http://127.0.0.1:{port}/out/model.glb"}}})
        if self.path == "/out/model.glb":
            return self._send(200, GLB, "model/gltf-binary")
        self._send(404, {})


@pytest.fixture
def fake_server():
    _Fake.calls, _Fake.mode = [], "ok"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_tripo_multiview_end_to_end(fake_server, tmp_path, monkeypatch):
    monkeypatch.setitem(model3d.TRIPO_SITES, "ai", {**model3d.TRIPO_SITES["ai"], "base": fake_server + "/v3"})
    monkeypatch.setattr(model3d, "POLL_S", 0)
    cfg = {"model3d": {"provider": "tripo", "tripo": {"site": "ai", "api_key_ai": "KEY", "model": "P2-20260801"}}}
    out = tmp_path / "o" / "m.glb"
    meta = model3d.generate(cfg, {"front": str(png(tmp_path / "f.png")), "right": str(png(tmp_path / "r.png"))},
                            str(out), texture=False)
    assert out.read_bytes() == GLB and not (tmp_path / "o" / "m.glb.part").exists()
    assert meta["endpoint"] == "multiview-to-model" and meta["views"] == ["front", "right"] and meta["texture"] is False
    gen = next(json.loads(c[3]) for c in _Fake.calls if c[1] == "/v3/generation/multiview-to-model")
    assert gen == {"inputs": [{"front": "tok1"}, {"right": "tok2"}], "model": "P2-20260801", "texture": False, "pbr": False}
    assert all(c[2] == "Bearer KEY" for c in _Fake.calls if c[1].startswith("/v3/"))
    # 单张走 image-to-model
    _Fake.calls.clear()
    model3d.generate(cfg, {"front": str(tmp_path / "f.png")}, str(out))
    gen = next(json.loads(c[3]) for c in _Fake.calls if c[1].startswith("/v3/generation/"))
    assert gen["input"].startswith("tok") and gen["texture"] is True and gen["model"] == "P2-20260801"


def test_hunyuan3d_local_single_view_and_errors(fake_server, tmp_path):
    cfg = {"model3d": {"provider": "hunyuan3d", "hunyuan3d": {"base_url": fake_server, "model": "hunyuan3d-dit-v2-0", "texture": False}}}
    out = tmp_path / "m.glb"
    meta = model3d.generate(cfg, {"front": str(png(tmp_path / "f.png")), "back": str(png(tmp_path / "b.png"))}, str(out))
    body = json.loads(_Fake.calls[-1][3])
    assert body["texture"] is False and body["num_inference_steps"] == 30 and body["type"] == "glb"   # 非 Turbo 30 步
    assert meta["views"] == ["front"] and any("背面" in n for n in meta["notes"])                  # 只收一张,如实说明
    _Fake.mode = "fail"
    with pytest.raises(model3d.Model3DError, match="--enable_tex"):
        model3d.generate(cfg, {"front": str(tmp_path / "f.png")}, str(out), texture=True)


def test_genmedia_cli_model3d(fake_server, tmp_path):
    conf = tmp_path / "genconfig.json"
    conf.write_text(json.dumps({"model3d": {"provider": "hunyuan3d", "hunyuan3d": {"base_url": fake_server}}}), encoding="utf-8")
    out = tmp_path / "x.glb"
    env = {**os.environ, "VIDEOAGENTS_CONFIG_PATH": str(conf), "VIDEOAGENTS_UI_LANG": "en"}
    r = subprocess.run([sys.executable, str(ROOT / "modules/genmedia.py"), "model3d", "--front", str(png(tmp_path / "f.png")),
                        "--output", str(out), "--no-texture"], capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=60)
    assert r.returncode == 0, r.stderr
    meta = json.loads(next(ln for ln in r.stdout.splitlines() if ln.startswith(model3d.META_PREFIX))[len(model3d.META_PREFIX):])
    assert meta["provider"] == "hunyuan3d" and meta["texture"] is False and out.read_bytes() == GLB
    r = subprocess.run([sys.executable, str(ROOT / "modules/genmedia.py"), "model3d", "--front", str(tmp_path / "f.png"),
                        "--output", str(tmp_path / "x.obj")], capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=60)
    assert r.returncode == 1 and "生成失败" in r.stderr and ".glb" in r.stderr
