"""成片发布页「设为默认」(2026-11-01):标题 / 封面各选一个作为发布时的默认素材。
标题写回 seo.json 本集该平台条目的 picked(SEO 规约里待人工填的字段,发布技能逐字取它),本集每个平台一张 SEO 卡;
封面每集一张,记 publish/defaults.json;派单工单写出默认封面与本渠道默认标题。"""
import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.runtime import core  # noqa: E402


def _w(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _r(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _png(p: Path, size=(1280, 720)):
    from PIL import Image
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (20, 30, 40)).save(p)
    return p


def _proj(tmp_path, monkeypatch, name="p"):
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path / "projects")

    async def no_op(*args, **kwargs):
        return {"ok": True}
    monkeypatch.setattr(core, "_notify_settings_change", no_op)
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "zh")
    base = core.PROJECTS_DIR / name
    base.mkdir(parents=True)
    (base / "settings.json").write_text("{}")
    return base


def _seo_project(base: Path):
    """sunworld / fengshen3 口径:项目级 publish/seo.json,items 按 集 × 平台,picked 写标题原文。"""
    _w(base / "publish" / "seo.json", {"meta": {"generated_at": "2026-10-30"}, "items": [
        {"ep": "ep01", "platform": "xiaohongshu", "part": 1,
         "titles": [{"id": "t1", "text": "小红书甲", "orientation": "悬念型"}, {"id": "t2", "text": "小红书乙"}],
         "tags": ["#a"], "description": "简介", "picked": None},
        {"ep": "ep01", "platform": "douyin", "part": 1,
         "titles": ["抖音甲", "抖音乙", "抖音丙"], "title_angles": ["悬念型", "冲突型", "关键词检索型"],
         "picked": "抖音甲", "picked_index": 0},
        {"ep": "ep02", "platform": "douyin", "part": 1, "titles": ["别集"], "picked": None}]})


def _set(project, ep, body):
    return asyncio.run(core.api_publish_default_set(project, ep, body))


def test_seo_items_per_platform(tmp_path, monkeypatch):
    base = _proj(tmp_path, monkeypatch)
    _seo_project(base)
    info = core._ep_publish_info(base, "ep01")
    items = info["seo_items"]
    assert [(s["idx"], s["platform"]) for s in items] == [(0, "xiaohongshu"), (1, "douyin")]
    assert info["seo"] == items[0] and info["seo_file"] == "publish/seo.json"
    assert items[0]["picked"] is None and items[1]["picked"] == "t1"           # 原文匹配到 id
    assert [t["orientation"] for t in items[1]["titles"]] == ["悬念型", "冲突型", "关键词检索型"]
    # 条目自己的 platform 优先于文件 meta(以前 meta 是对象时一律取 meta.platform,项目级多平台全变成 None)
    assert core._ep_publish_info(base, "ep02")["seo_items"][0]["platform"] == "douyin"


def test_pick_title_writes_seo_picked(tmp_path, monkeypatch):
    base = _proj(tmp_path, monkeypatch)
    _seo_project(base)
    r = _set("p", "ep01", {"kind": "title", "item": 1, "title_id": "t3", "platform": "douyin", "text": "抖音丙"})
    assert r["item"]["picked"] == "t3" and r["file"] == "publish/seo.json"
    doc = _r(base / "publish" / "seo.json")
    dy = doc["items"][1]
    assert dy["picked"] == "抖音丙" and dy["picked_index"] == 2 and dy["picked_by"] == "user" and dy["picked_at"]
    assert doc["items"][0]["picked"] is None and doc["items"][2]["picked"] is None   # 别的平台 / 别集不动
    assert "picked_index" not in doc["items"][0]                                    # 原来没有的键不凭空加
    r = _set("p", "ep01", {"kind": "title", "item": 0, "title_id": "t2", "platform": "xiaohongshu", "text": "小红书乙"})
    assert _r(base / "publish" / "seo.json")["items"][0]["picked"] == "小红书乙"
    assert "picked_index" not in _r(base / "publish" / "seo.json")["items"][0]
    # 取消默认:picked 清空,picked_index 同步,留痕字段去掉
    _set("p", "ep01", {"kind": "title", "item": 1, "title_id": None, "platform": "douyin"})
    dy = _r(base / "publish" / "seo.json")["items"][1]
    assert dy["picked"] is None and dy["picked_index"] is None and "picked_by" not in dy
    assert core._ep_publish_info(base, "ep01")["seo_items"][1]["picked"] is None
    assert core._ep_default_title(base, "ep01", "xiaohongshu") == "小红书乙"
    assert core._ep_default_title(base, "ep01", "douyin") is None and core._ep_default_title(base, "ep01", "bilibili") is None


def test_pick_title_object_style_and_episode_file(tmp_path, monkeypatch):
    """offer 口径:picked 是 {title_id, description_id, note};集级 publish/<ep>/seo.json 有条目时写它。"""
    base = _proj(tmp_path, monkeypatch)
    _w(base / "publish" / "seo.json", {"items": [{"ep": "ep01", "platform": "youtube", "titles": ["项目级"], "picked": None}]})
    _w(base / "publish" / "ep01" / "seo.json", {"meta": {"platform": "youtube"}, "items": [
        {"titles": [{"id": "T1", "angle": "悬念钩子型", "text": "甲"}, {"id": "T2", "text": "乙"}],
         "picked": {"title_id": "T1", "description_id": "D1", "note": "弹窗选定 T1+D1", "picked_by": "user"}}]})
    info = core._ep_publish_info(base, "ep01")
    assert info["seo_file"] == "publish/ep01/seo.json" and info["seo_items"][0]["platform"] == "youtube"
    assert info["seo_items"][0]["titles"][0]["orientation"] == "悬念钩子型"
    _set("p", "ep01", {"kind": "title", "item": 0, "title_id": "T2", "platform": "youtube", "text": "乙"})
    pk = _r(base / "publish" / "ep01" / "seo.json")["items"][0]["picked"]
    assert pk["title_id"] == "T2" and pk["description_id"] == "D1" and pk["note"] != "弹窗选定 T1+D1"
    assert _r(base / "publish" / "seo.json")["items"][0]["picked"] is None          # 项目级那份不碰
    assert core._ep_default_title(base, "ep01", "youtube") == "乙"
    _set("p", "ep01", {"kind": "title", "item": 0, "title_id": None, "platform": "youtube"})
    pk = _r(base / "publish" / "ep01" / "seo.json")["items"][0]["picked"]
    assert "title_id" not in pk and pk["description_id"] == "D1"                    # 只清标题,简介的选择保留
    assert core._ep_publish_info(base, "ep01")["seo_items"][0]["picked"] is None


def test_pick_title_guards(tmp_path, monkeypatch):
    base = _proj(tmp_path, monkeypatch)
    with pytest.raises(core.ServiceError) as ei:
        _set("p", "ep01", {"kind": "title", "item": 0, "title_id": "t1"})
    assert ei.value.status_code == 404
    _seo_project(base)
    before = (base / "publish" / "seo.json").read_text(encoding="utf-8")
    stale = [{"item": 5, "title_id": "t1"},                                          # 条目已不在
             {"item": 1, "title_id": "t1", "platform": "xiaohongshu"},               # 页面打开后条目顺序变了
             {"item": 1, "title_id": "t1", "platform": "douyin", "text": "旧标题"},    # 标题被 SEO 重写过
             {"item": 1, "title_id": "t9", "platform": "douyin"}]
    for body in stale:
        with pytest.raises(core.ServiceError) as ei:
            _set("p", "ep01", {"kind": "title", **body})
        assert ei.value.status_code == 409 and "请刷新页面" in str(ei.value.detail)
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "ja")                # 非中文界面出英文
    with pytest.raises(core.ServiceError) as ei:
        _set("p", "ep01", {"kind": "title", "item": 5, "title_id": "t1"})
    assert "refresh the page" in str(ei.value.detail)
    for body in ({"kind": "title", "item": "0", "title_id": "t1"}, {"kind": "poster"}):
        with pytest.raises(core.ServiceError) as ei:
            _set("p", "ep01", body)
        assert ei.value.status_code == 400
    assert (base / "publish" / "seo.json").read_text(encoding="utf-8") == before


def test_default_cover(tmp_path, monkeypatch):
    base = _proj(tmp_path, monkeypatch)
    edir = base / "edit" / "ep01"
    _png(edir / "thumbnail_A.png")
    _png(edir / "sub" / "thumbnail_B.png", (1080, 1920))
    (edir / "notes.txt").write_text("x")
    (base / "secret.png").write_bytes(b"0")
    assert core._ep_default_cover(base, "ep01") is None
    r = _set("p", "ep01", {"kind": "cover", "name": "sub/thumbnail_B.png"})
    c = r["cover"]
    assert c["file"] == "edit/ep01/sub/thumbnail_B.png" and c["name"] == "sub/thumbnail_B.png" and c["exists"]
    assert (c["width"], c["height"]) == (1080, 1920)
    doc = _r(base / core.PUBLISH_DEFAULTS_REL)
    assert doc["episodes"]["ep01"]["cover"]["file"] == "edit/ep01/sub/thumbnail_B.png"
    assert doc["episodes"]["ep01"]["cover"]["picked_by"] == "user"
    for bad in ("../../secret.png", "notes.txt", "thumbnail_C.png", "/etc/hosts"):
        with pytest.raises(core.ServiceError) as ei:
            _set("p", "ep01", {"kind": "cover", "name": bad})
        assert ei.value.status_code == 404
    assert core._ep_default_cover(base, "ep01")["name"] == "sub/thumbnail_B.png"    # 失败不改原值
    _set("p", "ep02", {"kind": "cover", "name": None})                              # 别集取消:没有也不报错
    _set("p", "ep01", {"kind": "cover", "name": "thumbnail_A.png"})
    # 页面数据:默认封面与封面卡同名对得上
    d = core._preview_videos("p", "ep01")
    assert d["default_cover"]["name"] == "thumbnail_A.png"
    assert d["default_cover"]["name"] in [t["name"] for t in d["thumbnails"]]
    (edir / "thumbnail_A.png").unlink()
    assert core._ep_default_cover(base, "ep01")["exists"] is False
    _set("p", "ep01", {"kind": "cover", "name": ""})
    assert core._ep_default_cover(base, "ep01") is None
    assert "ep01" not in _r(base / core.PUBLISH_DEFAULTS_REL)["episodes"]
    # 只有 defaults.json 时「发布相关」仍按没有物料处理
    assert core._ep_publish_info(base, "ep01") is None


def test_dispatch_carries_defaults(tmp_path, monkeypatch):
    base = _proj(tmp_path, monkeypatch)
    sent = []

    async def fake_chat(body):
        sent.append(body["message"])
        return {"run_id": "r1"}
    monkeypatch.setattr(core, "api_chat", fake_chat)
    (base / "edit" / "ep01").mkdir(parents=True)
    (base / "edit" / "ep01" / "final.mp4").write_bytes(b"0")
    _seo_project(base)
    asyncio.run(core.api_publish_dispatch("p", "ep01", {"platform": "douyin"}))
    assert "默认封面" not in sent[-1] and "douyin 条目已选定默认标题「抖音甲」" in sent[-1]   # 只有标题选定:只加标题行
    _png(base / "edit" / "ep01" / "thumbnail_B.png", (1920, 1080))
    _set("p", "ep01", {"kind": "cover", "name": "thumbnail_B.png"})
    asyncio.run(core.api_publish_dispatch("p", "ep01", {"platform": "xiaohongshu"}))
    msg = sent[-1]
    assert "默认封面 edit/ep01/thumbnail_B.png(1920×1080)" in msg and "优先于发布包里的封面" in msg
    assert "仅当技能文档写明本渠道不接受这种画幅的封面时" in msg
    assert "已选定默认标题" not in msg                                               # 小红书条目还没选定
    assert msg.index("默认封面") < msg.index("- 标题 / 简介 / 标签 / 元数据")
    (base / "edit" / "ep01" / "thumbnail_B.png").unlink()                            # 文件没了就不写
    asyncio.run(core.api_publish_dispatch("p", "ep01", {"platform": "douyin"}))
    assert "默认封面" not in sent[-1]
    plain = core.publish_dispatch_message("p", "ep01", "youtube", False, ["final.mp4"])
    assert "默认封面" not in plain and "默认标题" not in plain


def test_page_render_and_clicks(tmp_path, monkeypatch):
    """node 跑页面原函数:render 出每平台一张 SEO 卡、标题 / 封面的「设为默认」「取消默认」,点击发出的请求体。"""
    if not shutil.which("node"):
        pytest.skip("Node unavailable")
    base = _proj(tmp_path, monkeypatch)
    _seo_project(base)
    _png(base / "edit" / "ep01" / "thumbnail_A.png")
    _png(base / "edit" / "ep01" / "thumbnail_B.png")
    (base / "edit" / "ep01" / "final.mp4").write_bytes(b"0")
    _set("p", "ep01", {"kind": "cover", "name": "thumbnail_B.png"})
    d = core._preview_videos("p", "ep01")
    src = (ROOT / "apps" / "web" / "static" / "preview_videos.html").read_text(encoding="utf-8")
    fns = src[src.index("function pubHtml("):src.index("async function load(){")]
    script = fns + f"""
let LAST=null;const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
const t=s=>s, I18N={{f:(s,p)=>String(s).replace(/\\{{(\\w+)\\}}/g,(m,k)=>k in p?p[k]:m)}};
const CanvasEntry={{btn:()=>'<a>canvas</a>'}};
function renderEps(){{}};function restoreScroll(){{}};let loads=0;function load(){{loads++}}
globalThis.sessionStorage={{getItem:()=>null,setItem(){{}},removeItem(){{}}}};globalThis.localStorage=globalThis.sessionStorage;
const content={{innerHTML:'',dataset:{{}}}},proj={{value:'p'}};
const $=s=>s==='#project'?proj:content;
const reqs=[];globalThis.fetch=async(u,o)=>{{reqs.push([u,JSON.parse(o.body)]);return {{ok:true,json:async()=>({{ok:true}})}}}};
const alerts=[];globalThis.alert=m=>alerts.push(m);
const btns=[];
globalThis.document={{querySelectorAll:sel=>{{
  const m=sel.match(/data-(tpick|tclear|cpick|cclear)/);
  if(sel.endsWith('button.mini'))return btns;
  if(!m)return [];
  const out=[];const re=new RegExp('data-'+m[1]+'="([^"]*)"(?: data-tid="([^"]*)")?','g');let x;
  while((x=re.exec(content.innerHTML))){{const b={{dataset:{{[m[1]]:x[1],...(x[2]!=null?{{tid:x[2]}}:{{}})}}}};btns.push(b);out.push(b)}}
  return out;}}}};
(async()=>{{
  render({json.dumps(d, ensure_ascii=False)});
  const html=content.innerHTML;
  for(const kind of ['tpick','tclear','cpick','cclear']){{
    const b=btns.find(x=>kind in x.dataset);await b.onclick();
  }}
  console.log(JSON.stringify({{html,reqs,loads,alerts,disabled:btns.every(b=>b.disabled)}}));
}})();"""
    res = json.loads(subprocess.check_output(["node", "-e", script], text=True))
    html = res["html"]
    assert html.count("🔍 SEO 上刊物料") == 2 and "publish/seo.json · xiaohongshu" in html and "publish/seo.json · douyin" in html
    assert html.count('data-tpick="0"') == 2 and html.count('data-tpick="1"') == 2 and html.count('data-tclear="1"') == 1
    assert html.count("⚠ 上刊标题尚未选定:点一条标题右侧的「设为默认」,发布时逐字使用") == 1     # 只有小红书没选
    assert 'data-cpick="thumbnail_A.png"' in html and 'data-cpick="thumbnail_B.png"' not in html
    assert html.count('data-cclear="1"') == 1 and "默认封面:thumbnail_B.png" in html
    assert html.count("✓ 默认") == 2 and html.count('class="t on"') == 1
    url = "/api/v1/projects/p/publish/ep01/defaults"
    assert [r[0] for r in res["reqs"]] == [url] * 4
    assert [r[1] for r in res["reqs"]] == [
        {"kind": "title", "item": 0, "title_id": "t1", "platform": "xiaohongshu", "text": "小红书甲"},
        {"kind": "title", "item": 1, "title_id": None, "platform": "douyin"},
        {"kind": "cover", "name": "thumbnail_A.png"},
        {"kind": "cover", "name": None}]
    assert res["loads"] == 4 and res["alerts"] == [] and res["disabled"]
    # 没选默认封面时的提示;元数据封面与默认封面不同时提示发布按默认封面
    d2 = {**d, "default_cover": None}
    _w(base / "publish" / "metadata.json", {"episodes": [{"ep": "ep01", "thumbnail": "edit/ep01/thumbnail_A.png"}]})
    d3 = core._preview_videos("p", "ep01")
    script2 = fns + script[script.index("\nlet LAST=null;"):script.index("(async()=>{")] + f"""
render({json.dumps(d2, ensure_ascii=False)});const a=content.innerHTML;
render({json.dumps(d3, ensure_ascii=False)});console.log(JSON.stringify([a,content.innerHTML]));"""
    a, b = json.loads(subprocess.check_output(["node", "-e", script2], text=True))
    assert "未设默认封面:发布时按发布包封面与技能文档的查找顺序" in a and "data-cclear" not in a
    assert "发布时用本页选定的默认封面 thumbnail_B.png,不按元数据里的封面" in b


def test_dictionaries_and_specs():
    page = (ROOT / "apps" / "web" / "static" / "preview_videos.html").read_text(encoding="utf-8")
    keys = ["设为默认", "✓ 默认", "取消默认",
            "发布时用这条标题:写入 seo.json 本条目的 picked,发布执行 Agent 逐字使用",
            "取消这条默认标题(清空 seo.json 本条目的 picked)",
            "发布时用这张封面:记在 publish/defaults.json,派单时优先于发布包里的封面",
            "取消默认封面,发布时按发布包封面与技能文档的查找顺序",
            "⚠ 上刊标题尚未选定:点一条标题右侧的「设为默认」,发布时逐字使用",
            "默认封面:{name}",
            "默认封面 {name} 已不存在,发布时按发布包封面与技能文档的查找顺序",
            "未设默认封面:发布时按发布包封面与技能文档的查找顺序",
            "发布时用本页选定的默认封面 {name},不按元数据里的封面"]
    for k in keys:
        assert f"'{k}'" in page, k
    dicts = sorted((ROOT / "apps" / "web" / "static" / "i18n").glob("??.js"))
    assert len(dicts) == 11
    for js in dicts:
        text = js.read_text(encoding="utf-8")
        for k in keys:
            assert f'"{k}":' in text, (js.name, k)
        assert "items[0].picked=null" not in text                                     # 旧提示已删
    soul = (ROOT / "agents" / "12-publishing" / "publisher" / "SOUL.md").read_text(encoding="utf-8")
    assert "**仅当该文件记有本集默认封面且文件存在时**" in soul
    for rel in ("metadata", "platform-adapter"):
        assert "仅当 `publish/defaults.json` 的 `episodes.<ep>.cover.file` 记有本集默认封面" in \
            (ROOT / "agents" / "12-publishing" / rel / "SOUL.md").read_text(encoding="utf-8")
