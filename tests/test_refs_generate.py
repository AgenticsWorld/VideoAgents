"""参考文件页「✨ 生成」(2026-10-26):按页面顶部图像模型(image_model_prefs.refs)出待选图 → 加入参考 / 重新生成。
genmedia 子进程一律打桩,不联网、不出真图。"""
import asyncio
import json
import re
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "apps/web/static"
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


class SyncThread:
    """后台出图线程改成 start() 时当场跑完,免得测试等线程。"""
    def __init__(self, target, args=(), **_):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    from services.runtime import core

    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")
    core.PROJECTS_DIR.mkdir()
    core.ensure_project("p")
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "zh")
    monkeypatch.setattr(core, "load_genconfig", lambda: {"image": {"provider": "fal", "fal": {"api_key": "k", "model": "flux-2"},
                                                                   "volcengine": {"api_key": "v", "model": "seedream-5"}}})
    monkeypatch.setattr(core, "threading", SimpleNamespace(Thread=SyncThread))
    monkeypatch.setitem(core.STATE, "image_model_prefs", {})
    monkeypatch.setattr(core, "REFGEN_JOBS", {})
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        out = Path(cmd[cmd.index("--output") + 1])
        if "坏" in cmd[cmd.index("--prompt") + 1]:
            return subprocess.CompletedProcess(cmd, 1, "", "[genmedia] refs 按预览页设定…\n生成失败: 渠道 Key 无效\n")
        out.write_bytes(JPEG)                     # 渠道回的是 JPEG 字节,文件名仍是契约名 .png
        return subprocess.CompletedProcess(cmd, 0, f"已生成: {out}\n", "")
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    return core, core.PROJECTS_DIR / "p", calls


def _start(core, **kw):
    return asyncio.run(core.api_refgen_start({"project": "p", "category": "characters", "prompt": "青铜鼎", **kw}))


def test_generate_preview_then_adopt(runtime):
    core, base, calls = runtime
    job = _start(core, aspect="3:4", style={"id": "x_style", "name": "水墨"})
    assert job["provider"] == "fal" and job["model"] == "flux-2"                                # 没选 = 跟随全局
    cmd = calls[0]
    assert cmd[cmd.index("--aspect") + 1] == "3:4" and "--provider" not in cmd                   # 渠道交给 genmedia 按目录套用
    assert cmd[cmd.index("--output") + 1] == str(base / "assets/refs_gen" / f"{job['id']}.png")
    st = asyncio.run(core.api_refgen_status("p", job["id"]))
    assert st["status"] == "done" and st["url"].startswith(f"/projects/p/assets/refs_gen/{job['id']}.png?v=")
    assert not any((base / "refs" / "characters").iterdir())                                   # 没点加入,refs 不动

    r = asyncio.run(core.api_refgen_adopt({"project": "p", "job": job["id"]}))
    assert r["path"] == f"characters/gen_{job['id']}.jpg" and not r["existed"]                 # 扩展名按文件头
    assert (base / "refs" / r["path"]).read_bytes() == JPEG
    ann = json.loads((base / "refs/annotations.json").read_text(encoding="utf-8"))[r["path"]]
    assert ann["note"] == "" and ann["gen"]["prompt"] == "青铜鼎" and ann["gen"]["style"] == "水墨"
    assert ann["gen"]["provider"] == "fal" and ann["gen"]["aspect"] == "3:4"
    again = asyncio.run(core.api_refgen_adopt({"project": "p", "job": job["id"]}))
    assert again["existed"] and again["path"] == r["path"]                                     # 连点不重复加
    other = asyncio.run(core.api_refgen_adopt({"project": "p", "job": job["id"], "category": "style"}))
    assert other["path"] == f"style/gen_{job['id']}.jpg"
    assert asyncio.run(core.api_refgen_status("p", job["id"]))["adopted"] == {"characters": r["path"], "style": other["path"]}

    files = {f["path"]: f for c in asyncio.run(core.api_refs_list("p"))["categories"] for f in c["files"]}
    assert files[r["path"]]["gen"]["prompt"] == "青铜鼎" and files[r["path"]]["source"] is None
    assert not any(k.startswith("assets") for k in files)                                      # 待选图不进参考文件列表
    asyncio.run(core.api_refs_note({"project": "p", "path": r["path"], "note": "主角兵器"}))
    assert f"- `{r['path']}`:主角兵器(AI 生成:fal flux-2)" in (base / "refs/NOTES.md").read_text(encoding="utf-8")
    asyncio.run(core.api_refs_note({"project": "p", "path": r["path"], "note": ""}))
    assert json.loads((base / "refs/annotations.json").read_text(encoding="utf-8"))[r["path"]]["gen"]   # 清空注释留着生成记录
    assert not (base / "refs/CREDITS.md").exists()                                              # 生成图不进图库署名表


def test_page_model_choice_and_regenerate_discards_previous(runtime):
    core, base, calls = runtime
    asyncio.run(core.api_image_model_set("refs", {"provider": "volcengine", "model": "seedream-5"}))
    first = _start(core)
    assert (first["provider"], first["model"]) == ("volcengine", "seedream-5")
    f1 = base / "assets/refs_gen" / f"{first['id']}.png"
    assert f1.is_file()
    second = _start(core, discard=first["id"], prompt="青铜鼎,侧面")
    assert not f1.exists() and first["id"] not in core.REFGEN_JOBS and second["id"] in core.REFGEN_JOBS
    old = base / "assets/refs_gen/stale.png"
    old.write_bytes(JPEG)
    stamp = time.time() - core.REFGEN_KEEP_S - 60
    import os
    os.utime(old, (stamp, stamp))
    _start(core)
    assert not old.exists()                                                                    # 隔天的待选图清掉

    from modules import genmedia
    assert genmedia.image_kind_of_output(f"{base}/assets/refs_gen/x.png") == "refs"
    assert genmedia.image_kind_of_output("p/refs/characters/x.png") == ""
    assert "refs" in genmedia.IMAGE_PREF_KINDS and genmedia.IMAGE_PREF_KINDS == core.IMAGE_PREF_KINDS


def test_failures_and_validation(runtime):
    core, base, _ = runtime
    bad = _start(core, prompt="坏图")
    st = asyncio.run(core.api_refgen_status("p", bad["id"]))
    assert st["status"] == "failed" and st["error"] == "渠道 Key 无效" and "url" not in st
    with pytest.raises(core.ServiceError) as e:
        asyncio.run(core.api_refgen_adopt({"project": "p", "job": bad["id"]}))
    assert e.value.status_code == 409
    for kw, code in (({"prompt": "  "}, 400), ({"category": "music"}, 400), ({"aspect": "2:1"}, 400),
                     ({"prompt": "x" * (core.REFGEN_MAX_PROMPT + 1)}, 400)):
        with pytest.raises(core.ServiceError) as e:
            _start(core, **kw)
        assert e.value.status_code == code, kw
    with pytest.raises(core.ServiceError) as e:
        asyncio.run(core.api_refgen_status("p", "nope"))
    assert e.value.status_code == 404
    core.ensure_project("q")
    ok = _start(core)
    with pytest.raises(core.ServiceError) as e:
        asyncio.run(core.api_refgen_status("q", ok["id"]))                                      # 别的项目看不到
    assert e.value.status_code == 404


def test_page_component_and_dictionaries():
    page = (STATIC / "preview_refs.html").read_text(encoding="utf-8")
    assert "mountImageModelPicker({kind:'refs'" in page and "/static/ref-generate.js" in page
    assert page.index('class="gencat"') < page.index('class="stockcat"')                       # 「生成」在「图库」左边
    comp = (STATIC / "ref-generate.js").read_text(encoding="utf-8")
    assert "/references/generate" in comp and "/adopt" in comp and "style_library.json" in comp
    keys = set(re.findall(r"T\('([^']+)'\)", comp)) | set(re.findall(r"F\('([^']+)'", comp))
    keys |= {"AI 生成", "用 AI 生成一张图加入本板块(图像模型在页面顶部选)",
             "本页「✨ 生成」出图用的图像渠道/模型,与全局「生成模型」设置分开;空=跟随全局"}
    for path in sorted((STATIC / "i18n").glob("??.js")):
        text = path.read_text(encoding="utf-8")
        missing = [k for k in keys if f"\n{json.dumps(k, ensure_ascii=False)}: " not in text]
        assert not missing, (path.name, missing)
        assert '\n"生成": ".",' not in text, path.name
