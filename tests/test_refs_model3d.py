"""参考文件页「🧊 3D 模型」板块(2026-11-25):GLB 上传与列表、「✨ 生成」窗口的参考图上传 / 开始 / 状态 / 加入。
genmedia 子进程一律打桩,不联网、不出真模型。"""
import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_model3d_generate import GLB, png  # noqa: E402  (tests/ 在 sys.path 上,同 test_delete_guard 的写法)

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "apps/web/static"
PNG_HEAD = b"\x89PNG\r\n\x1a\n"


class SyncThread:
    def __init__(self, target, args=(), **_):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


GENCONFIG = {"model3d": {"provider": "tripo",
                         "tripo": {"site": "ai", "api_key_ai": "k", "model": "v3.1-20260211"},
                         "fal": {"api_key": "", "model": "fal-ai/hunyuan-3d/v3.1/pro"},
                         "rhapi": {"site": "ai", "i23d": ""},
                         "hunyuan3d": {"base_url": "http://127.0.0.1:9", "model": "hunyuan3d-dit-v2-mini-turbo", "texture": False}}}


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    from services.runtime import core

    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    core.PROJECTS_DIR.mkdir()
    core.ensure_project("p")
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "zh")
    monkeypatch.setattr(core, "load_genconfig", lambda: json.loads(json.dumps(GENCONFIG)))
    monkeypatch.setattr(core, "threading", SimpleNamespace(Thread=SyncThread))
    monkeypatch.setattr(core, "REFGEN3D_JOBS", {})
    for k in ("TRIPO_API_KEY", "FAL_KEY", "RUNNINGHUB_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        out = Path(cmd[cmd.index("--output") + 1])
        if "--back" in cmd and "bad" in cmd[cmd.index("--back") + 1]:
            return subprocess.CompletedProcess(cmd, 1, "", "[genmedia] …\n生成失败: Tripo 余额不足(code 2010)\n")
        out.write_bytes(GLB)
        meta = {"provider": cmd[cmd.index("--provider") + 1], "endpoint": "multiview-to-model",
                "views": ["front", "back"] if "--back" in cmd else ["front"], "notes": ["说明一条"]}
        return subprocess.CompletedProcess(cmd, 0, f"已生成: {out}\nMODEL3D_META {json.dumps(meta, ensure_ascii=False)}\n", "")
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    return core, core.PROJECTS_DIR / "p", calls


def test_models3d_category_upload_and_list(runtime):
    core, base, _ = runtime
    assert (base / "refs/models3d").is_dir() and "`models3d/`" in (base / "refs/README.md").read_text(encoding="utf-8")
    r = asyncio.run(core.api_refs_upload(GLB, "p", "models3d", "青铜鼎 v2.GLB"))
    assert r["path"] == "models3d/v2.glb"
    for name, data, why in (("a.obj", b"o 1", ".glb"), ("a.glb", b"PK\x03\x04zip", "GLB")):
        with pytest.raises(core.ServiceError, match=why):
            asyncio.run(core.api_refs_upload(data, "p", "models3d", name))
    cats = asyncio.run(core.api_refs_list("p"))["categories"]
    keys = [c["key"] for c in cats]
    assert keys[keys.index("props") + 1] == "models3d"
    f = next(c for c in cats if c["key"] == "models3d")["files"][0]
    assert f["is_model3d"] and not f["is_image"] and f["gen3d"] is None


def test_generate3d_flow_upload_start_adopt(runtime):
    core, base, calls = runtime
    (base / "refs/characters").mkdir(parents=True, exist_ok=True)
    png(base / "refs/characters/hero.png")
    up = asyncio.run(core.api_refgen3d_upload((base / "refs/characters/hero.png").read_bytes(), "p", "背面.png"))
    assert up["path"].startswith("assets/refs_gen3d/inputs/") and up["path"].endswith(".png")
    with pytest.raises(core.ServiceError, match="只收图片"):
        asyncio.run(core.api_refgen3d_upload(b"hello", "p", "x.txt"))

    job = asyncio.run(core.api_refgen3d_start({"project": "p", "images": {"front": "refs/characters/hero.png", "back": up["path"]},
                                               "names": {"back": "背面.png"}, "texture": False}))
    assert job["provider"] == "tripo" and job["model"] == "v3.1-20260211" and job["model_label"] == "Tripo H3.1"
    cmd, kw = calls[0]
    assert cmd[cmd.index("--front") + 1] == str(base / "refs/characters/hero.png")
    assert cmd[cmd.index("--back") + 1] == str(base / up["path"])
    assert "--no-texture" in cmd and cmd[cmd.index("--provider") + 1] == "tripo"
    assert kw["env"]["VIDEOAGENTS_UI_LANG"] == "zh" and kw["timeout"] == core.REFGEN3D_TIMEOUT
    st = asyncio.run(core.api_refgen3d_status("p", job["id"]))
    assert st["status"] == "done" and st["url"].startswith(f"/projects/p/assets/refs_gen3d/{job['id']}.glb?v=")
    assert st["endpoint"] == "multiview-to-model" and st["notes"] == ["说明一条"] and st["views_used"] == ["front", "back"]
    assert set(st["image_urls"]) == {"front", "back"}
    assert not any((base / "refs/models3d").iterdir())                                        # 没点加入,refs 不动

    r = asyncio.run(core.api_refgen3d_adopt({"project": "p", "job": job["id"]}))
    assert r["path"] == f"models3d/gen3d_{job['id']}.glb" and (base / "refs" / r["path"]).read_bytes() == GLB
    ann = json.loads((base / "refs/annotations.json").read_text(encoding="utf-8"))[r["path"]]
    g = ann["gen3d"]
    assert g["provider"] == "tripo" and g["model_label"] == "Tripo H3.1" and g["texture"] is False
    assert g["views"] == {"front": "refs/characters/hero.png", "back": "背面.png"}
    assert asyncio.run(core.api_refgen3d_adopt({"project": "p", "job": job["id"]}))["existed"]
    # 写注释再清空:生成记录留着;NOTES.md 尾注标 AI 生成 3D
    asyncio.run(core.api_refs_note({"project": "p", "path": r["path"], "note": "主角盔甲"}))
    assert "AI 生成 3D:Tripo v3.1-20260211" in (base / "refs/NOTES.md").read_text(encoding="utf-8")
    asyncio.run(core.api_refs_note({"project": "p", "path": r["path"], "note": ""}))
    files = {f["path"]: f for c in asyncio.run(core.api_refs_list("p"))["categories"] for f in c["files"]}
    assert files[r["path"]]["gen3d"]["model"] == "v3.1-20260211"

    # 重新生成:丢掉上一个待选模型,上传的参考图留着
    job2 = asyncio.run(core.api_refgen3d_start({"project": "p", "images": {"front": up["path"]}, "provider": "hunyuan3d",
                                                "discard": job["id"]}))
    assert not (base / f"assets/refs_gen3d/{job['id']}.glb").exists() and (base / up["path"]).is_file()
    assert job2["texture"] is False                                     # 本地渠道默认跟设置页「生成纹理」
    assert "--no-texture" in calls[-1][0] and calls[-1][0][calls[-1][0].index("--provider") + 1] == "hunyuan3d"


def test_generate3d_validation_and_failure(runtime, monkeypatch):
    core, base, _ = runtime
    png(base / "refs/style/a.png")
    for images, why in (({}, "正面"), ({"front": "../x.png"}, "invalid image path"), ({"front": "novel/a.png"}, "invalid image path"),
                        ({"front": "refs/style/none.png"}, "不存在"), ({"top": "refs/style/a.png"}, "unknown view")):
        with pytest.raises(core.ServiceError, match=why):
            asyncio.run(core.api_refgen3d_start({"project": "p", "images": images}))
    with pytest.raises(core.ServiceError, match="Fal 未填写 API Key"):                        # Key 不全当场报,不起任务
        asyncio.run(core.api_refgen3d_start({"project": "p", "images": {"front": "refs/style/a.png"}, "provider": "fal"}))
    png(base / "refs/style/bad.png")
    job = asyncio.run(core.api_refgen3d_start({"project": "p", "images": {"front": "refs/style/a.png", "back": "refs/style/bad.png"}}))
    st = asyncio.run(core.api_refgen3d_status("p", job["id"]))
    assert st["status"] == "failed" and st["error"] == "Tripo 余额不足(code 2010)"
    with pytest.raises(core.ServiceError, match="还没生成好"):
        asyncio.run(core.api_refgen3d_adopt({"project": "p", "job": job["id"]}))
    with pytest.raises(core.ServiceError, match="重新生成"):
        asyncio.run(core.api_refgen3d_status("p", "nope"))
    opts = asyncio.run(core.api_refgen3d_options("p"))
    rows = {r["provider"]: r for r in opts["providers"]}
    assert rows["tripo"]["configured"] and not rows["fal"]["configured"] and opts["default"]["provider"] == "tripo"


def test_page_wiring():
    page = (STATIC / "preview_refs.html").read_text(encoding="utf-8")
    assert '"three":"/static/vendor/three/three.module.js"' in page and "/static/glb-viewer.js" in page
    assert "models3d:['🧊','3D 模型'" in page and "models3d:'.glb'" in page and "RefGen3D.open(" in page
    js = (STATIC / "ref-gen3d.js").read_text(encoding="utf-8")
    assert "/references/generate3d" in js and "VIEWS=['front','left','back','right']" in js
    for f in ("loaders/GLTFLoader.js", "loaders/DRACOLoader.js", "utils/BufferGeometryUtils.js", "libs/meshopt_decoder.module.js",
              "libs/draco/gltf/draco_decoder.wasm", "libs/draco/gltf/draco_wasm_wrapper.js", "environments/RoomEnvironment.js"):
        assert (STATIC / "vendor/three/addons" / f).is_file(), f
