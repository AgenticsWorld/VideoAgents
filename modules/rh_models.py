"""RunningHub 标准模型 API(「生成模型」页图像/视频的「RH」渠道,provider id = rhapi)。

与 ComfyUI 渠道的 RunningHub 运行方式(工作流,mode=rh_cn/rh_ai)是两套东西:这里调用的是
RunningHub 托管的第三方标准模型端点(Seedance / Seedream / Kling / Vidu / Wan / H3 …),
POST {base}/openapi/v2/<endpoint> 建任务 → POST /openapi/v2/query 轮询 → results[].url 下载;
AI 应用与工作流节点不走这里。

- 两个站点账号/Key 不互通:runninghub.ai(国际)/ runninghub.cn(国内),Key 按站点分存。
  模型 API 只收「企业共享」类型的 API Key(官方文档口径)。
- 每个模型一个端点、各自一套参数表,官方没有模型列表接口:目录从该站文档站的 llms.txt
  (Apifox 生成)解析,逐个模型拉 OpenAPI 片段取端点路径与请求参数表,缓存到
  data/.videoagents/rh_models/<site>.json(24h 过期,设置页「刷新」强制重拉)。
- 模型 id = 端点路径去掉 /openapi/v2/ 前缀(如 rhart-video/sparkvideo-2.0/multimodal-video)。
- 请求体按模型参数表通用映射(build_body):prompt/负面/参考图/首尾帧/参考视频/参考音频/
  时长/分辨率/画幅/尺寸/seed/有声,枚举取最接近值;必填但无对应输入的参数取文档默认值,
  默认值是演示素材 URL 的一律不用(数组置空,单值报错),避免作者演示素材混进成片。
- 视频按输入在同系列端点间自动切换(pick_video_entry):选的是多模态端点但本次只有首帧 →
  同前缀下找支持首帧的 image-to-video 端点;纯文本且所选端点必须带图 → text-to-video。
"""
from __future__ import annotations

import concurrent.futures
import http.client
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
RUNTIME_DIR = Path(os.environ.get(
    "VIDEOAGENTS_RUNTIME_DIR", DATA_DIR / ".videoagents")).expanduser().resolve()
CACHE_DIR = RUNTIME_DIR / "rh_models"
CACHE_TTL = 24 * 3600

SITES = {
    "ai": {"base": "https://www.runninghub.ai", "doc": "runninghub-api-doc-en",
           "keys_url": "https://www.runninghub.ai/call-api/bill-task?tab=keys",
           "label": "runninghub.ai"},
    "cn": {"base": "https://www.runninghub.cn", "doc": "runninghub-api-doc-cn",
           "keys_url": "https://www.runninghub.cn/call-api/bill-task?tab=keys",
           "label": "runninghub.cn"},
}
DEFAULT_SITE = "ai"
POLL_INTERVAL = 8

# 目录里只收生成类任务;编辑/延长/超分/工具/对口型等不进下拉
IMAGE_TASKS = ("text-to-image", "image-to-image", "reference-to-image")
VIDEO_TASKS = ("text-to-video", "image-to-video", "reference-to-video")

_FIRST_KEYS = ("firstImageUrl", "firstFrameUrl")
_LAST_KEYS = ("lastImageUrl", "lastFrameUrl")
_AUDIO_FLAG_KEYS = ("generateAudio", "sound", "audio", "generateAudioSwitch")


def site_of(value) -> str:
    v = str(value or "").strip().lower()
    return "cn" if v in ("cn", "rh_cn") or "runninghub.cn" in v else "ai"


def api_key(pc: dict, site: str) -> str:
    return str(pc.get(f"api_key_{site}") or "").strip()


# ---------------- HTTP ----------------

def _http(url: str, data: bytes | None = None, headers: dict | None = None,
          timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:600]
        raise RuntimeError(f"RunningHub HTTP {e.code} {url}\n{body}") from e
    except (urllib.error.URLError, TimeoutError, ConnectionError,
            http.client.HTTPException, OSError) as e:
        raise RuntimeError(f"RunningHub 网络传输失败 {url}: {e}") from e


def _post(site: str, key: str, path: str, payload: dict, timeout: int = 120) -> dict:
    url = SITES[site]["base"] + "/openapi/v2/" + path.lstrip("/")
    raw = _http(url, json.dumps(payload).encode(),
                {"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                timeout=timeout)
    try:
        return json.loads(raw)
    except ValueError:
        raise RuntimeError(f"RunningHub 返回非 JSON({url}):{raw[:300]!r}") from None


# ---------------- 模型目录 ----------------

_LINE_RE = re.compile(r"^- (?P<cat>.+?) \[(?P<name>.+?)\]\((?P<url>https?://\S+?/(?P<doc>api-\d+)\.md)\)")


def _task_of(category: str, path: str) -> str:
    """任务类型:优先取分类里的 text-to-image 等段,「其他」分类按端点末段推断。"""
    for seg in category.split(" > "):
        s = seg.strip().lower()
        if s in IMAGE_TASKS + VIDEO_TASKS:
            return s
    last = path.rsplit("/", 1)[-1].lower()
    for t in IMAGE_TASKS + VIDEO_TASKS:
        if last.startswith(t):
            return t
    if last in ("edit", "image-edit") or last.startswith(("edit-", "image-edit-")):
        return "image-to-image"
    if last.startswith(("multimodal-video", "multimodal-to-video")):
        return "reference-to-video"
    # RH 自家 H3 插件流端点(minimax-h3-rh-enhanced/…、minimax-h3-oss/…)用缩写命名,
    # 文档还可能挂在 audio-to-video 分类下
    if last in ("ref2va", "r2va") or last.startswith(("ref2va-", "r2va-")):
        return "reference-to-video"
    if last in ("i2va", "fl2va") or last.startswith(("i2va-", "fl2va-")):
        return "image-to-video"
    if last in ("t2va",) or last.startswith("t2va-"):
        return "text-to-video"
    return ""


# 部分端点(RH 自家插件流/工作流封装,如 rhart-video/minimax-h3-*、rhart-image/f-2-*)的参数名是
# 「节点号##字段」(84##value、204##image…),语义只写在 description 首行(prompt / imageUrl / 参考图1 /
# 时长(秒)…)。_normalize 按字段名+描述把它们归一成标准参数名,多个同类槽位(参考图1..9)合成虚拟数组,
# build_body 组完再按 keymap 展开回真实参数名。识别不了的(lora、输出格式、提示词增强模型…)保持文档默认。
_SLOT_RE = re.compile(r"(\d+)")


def _role(key: str, spec: dict) -> tuple[str, int] | None:
    """「节点号##字段」参数 → (标准名, 槽位序号);标准名以 [] 结尾表示多槽位数组。"""
    field = key.split("##", 1)[1].lower()
    desc = str((spec or {}).get("description") or "").strip().split("\n")[0].strip()
    d = desc.lower().replace(" ", "")
    m = _SLOT_RE.search(desc)
    n = int(m.group(1)) if m else 0
    if field in ("image", "file", "video", "audio"):
        if d in ("firstframeurl", "firstimageurl", "首帧"):
            return "firstFrameUrl", 0
        if d in ("lastframeurl", "lastimageurl", "尾帧"):
            return "lastFrameUrl", 0
        if field == "image":
            if d == "imageurl":
                return "imageUrl", 0
            if re.match(r"^(imageurl|image)\d+$", d) or d.startswith("参考图") or d == "uploadimage":
                return "imageUrls[]", n
            return None
        if re.match(r"^video\d+$", d) or re.match(r"^参考视频\d+$", d):
            return "videoUrls[]", n
        if field == "audio" and (re.match(r"^audio\d+$", d) or d.startswith("音色参考")):
            return "audioUrls[]", n
        return None   # 驱动口型音频、参考视频伴音等:不自动填
    if field in ("positive_prompt",) or d in ("prompt", "positivepromptwords", "正向提示词"):
        return "prompt", 0
    if field == "negative_prompt" or "negative" in d:
        return "negativePrompt", 0
    if field == "aspect_ratio" or d.startswith("aspectratio") or "画面比例" in d:
        return "aspectRatio", 0
    if field == "megapixels" or d == "resolution" or "输出分辨率" in d:
        return "resolution", 0
    if d.startswith("duration") or d.startswith("时长"):
        return "duration", 0
    if d == "customwidth":
        return "width", 0
    if d in ("customhight", "customheight"):
        return "height", 0
    if d.startswith("thesizeofthegeneratedmedia"):
        return "aspectRatio", 0   # 实为画幅选项(1:1/16:9/…/Custom),值是序号,标签在 x-option-metadata
    return None


def _normalize(schema: dict) -> tuple[dict, set, dict]:
    """→ (标准名参数表, 标准名必填集, keymap{标准名: [真实参数名…]})。无「##」参数的端点原样返回。"""
    props, req = schema["properties"], set(schema["required"])
    if not any("##" in k for k in props):
        return props, req, {}
    out, keymap, required, slots = {}, {}, set(), {}
    for k, spec in props.items():
        role = _role(k, spec) if "##" in k else (k, 0)
        if role is None:
            out[k], keymap[k] = spec, [k]
            if k in req:
                required.add(k)
            continue
        name, n = role
        if name.endswith("[]"):
            slots.setdefault(name[:-2], []).append((n, k))
            continue
        if name in out:      # 同一语义出现两次:只填第一个,其余保持默认
            out[k], keymap[k] = spec, [k]
            if k in req:
                required.add(k)
            continue
        out[name], keymap[name] = spec, [k]
        if k in req:
            required.add(name)
    for name, items in slots.items():
        if name in out:
            continue
        items.sort()
        keys = [k for _, k in items]
        out[name] = {"type": "array", "maxItems": len(keys)}
        keymap[name] = keys
    return out, required, keymap


def _caps(props: dict) -> dict:
    """从参数表归纳本端点能收的输入。"""
    def arr_max(k):
        v = props.get(k) or {}
        return int(v.get("maxItems") or 99) if v.get("type") == "array" else 0

    c = {"images": arr_max("imageUrls"), "videos": arr_max("videoUrls"),
         "audios": arr_max("audioUrls")}
    img = props.get("imageUrl") or {}
    if img.get("type") == "array":
        c["images"] = max(c["images"], int(img.get("maxItems") or 99))
    elif img:
        c["image_single"] = True
    c["first"] = next((k for k in _FIRST_KEYS if k in props), "")
    c["last"] = next((k for k in _LAST_KEYS if k in props), "")
    if "videoUrl" in props and not c["videos"]:
        c["videos"] = 1
    au = props.get("audioUrl") or {}
    if au and not c["audios"]:
        c["audios"] = 1
    return c


def _parse_doc(text: str) -> tuple[str, dict] | None:
    import yaml
    m = re.search(r"```yaml\n(.*?)```", text, re.S)
    if not m:
        return None
    try:
        spec = yaml.safe_load(m.group(1))
    except Exception:       # noqa: BLE001
        return None
    for path, ops in (spec.get("paths") or {}).items():
        op = (ops or {}).get("post") or {}
        sch = ((((op.get("requestBody") or {}).get("content") or {})
                .get("application/json") or {}).get("schema") or {})
        if not path.startswith("/openapi/v2/") or not sch.get("properties"):
            continue
        return path[len("/openapi/v2/"):], {"properties": sch["properties"],
                                            "required": sorted(set(sch.get("required") or []))}
    return None


def _fetch_catalog(site: str) -> list[dict]:
    s = SITES[site]
    doc_root = f"{s['base']}/{s['doc']}"
    index = _http(f"{doc_root}/llms.txt", timeout=60).decode("utf-8", "replace")
    rows = []
    for line in index.splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        cat = m.group("cat")
        top = cat.split(" > ")[0].strip()
        # 顶层分类名文档站会改(国内站 2026-09-30「模型API」→「标准模型API」):按关键词认
        if "模型API" not in top.replace(" ", "") and "model api" not in top.lower():
            continue
        rows.append({"doc": m.group("doc"), "name": m.group("name").strip(),
                     "category": " > ".join(x.strip() for x in cat.split(" > ")[1:])})

    def load(row):
        try:
            text = _http(f"{doc_root}/{row['doc']}.md", timeout=60).decode("utf-8", "replace")
        except RuntimeError:
            return None
        parsed = _parse_doc(text)
        if not parsed:
            return None
        path, schema = parsed
        task = _task_of(row["category"], path)
        if not task:
            return None
        kind = "image" if task in IMAGE_TASKS else "video"
        return {**row, "id": path, "task": task, "kind": kind, "schema": schema,
                "caps": _caps(_normalize(schema)[0])}

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        out = [r for r in ex.map(load, rows) if r]
    if not out:
        raise RuntimeError(f"RunningHub 模型目录解析为空({doc_root}/llms.txt)")
    # 同一端点在文档里可能挂在多个分类下:按 id 去重
    seen, uniq = set(), []
    for r in sorted(out, key=lambda r: (r["kind"], r["task"], r["id"])):
        if r["id"] not in seen:
            seen.add(r["id"])
            uniq.append(r)
    return uniq


def catalog(site: str, refresh: bool = False) -> list[dict]:
    """站点模型目录(带缓存);拉取失败时有旧缓存就用旧缓存。"""
    site = site_of(site)
    cache = CACHE_DIR / f"{site}.json"
    cached = None
    if cache.is_file():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            cached = None
    if cached and not refresh and time.time() - float(cached.get("fetched_at") or 0) < CACHE_TTL:
        return _with_caps(cached["models"])
    try:
        models = _fetch_catalog(site)
    except RuntimeError:
        if cached:
            return _with_caps(cached["models"])
        raise
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps({"fetched_at": time.time(), "models": models},
                              ensure_ascii=False), encoding="utf-8")
    tmp.replace(cache)
    return models


def _with_caps(models: list[dict]) -> list[dict]:
    for m in models:
        m["caps"] = _caps(_normalize(m["schema"])[0])
    return models


def list_models(site: str, kind: str, refresh: bool = False) -> list[dict]:
    """设置页下拉用的精简清单:[{id, name, task, category}]。"""
    return [{"id": m["id"], "name": m["name"], "task": m["task"], "category": m["category"]}
            for m in catalog(site, refresh) if m["kind"] == kind]


def find_entry(site: str, model_id: str) -> dict:
    mid = str(model_id or "").strip().strip("/")
    if mid.startswith("openapi/v2/"):
        mid = mid[len("openapi/v2/"):]
    for refresh in (False, True):
        for m in catalog(site, refresh):
            if m["id"] == mid:
                return m
    raise RuntimeError(f"RunningHub({SITES[site]['label']})模型目录里找不到 {mid}"
                       "(「🎨 生成模型」页 RH 标签页刷新模型列表后重选)")


def _need(entry: dict, first: bool, last: bool, refs: int, videos: int, audios: int) -> str:
    """该端点收不下本次输入时返回原因,收得下返回空串。"""
    c = entry["caps"]
    props, req, _ = _normalize(entry["schema"])
    if first and not (c.get("first") or c.get("image_single") or (c.get("images") and not refs)):
        return "不支持首帧图"
    if last and not c.get("last"):
        return "不支持尾帧图"
    if refs and refs > (c.get("images") or 0):
        return f"参考图至多 {c.get('images') or 0} 张(本次 {refs})"
    if videos and videos > (c.get("videos") or 0):
        return f"参考视频至多 {c.get('videos') or 0} 段(本次 {videos})"
    if audios and audios > (c.get("audios") or 0):
        return f"参考音频至多 {c.get('audios') or 0} 段(本次 {audios})"
    # 必填的图/视频位本次没给素材
    if not (first or refs) and any(k in req for k in ("imageUrl", "firstImageUrl", "firstFrameUrl")):
        return "必须带首帧/参考图"
    if not refs and "imageUrls" in req and int((props["imageUrls"]).get("minItems") or 0) > 0:
        return "必须带参考图"
    if not videos and "videoUrl" in req:
        return "必须带参考视频"
    return ""


def pick_video_entry(site: str, model_id: str, first: bool, last: bool,
                     refs: int, videos: int, audios: int) -> dict:
    """按本次输入选端点:所选端点收得下就用它;否则在同系列(端点路径同前缀)里找收得下的,
    按任务类型贴合度排序(首尾帧→image-to-video,参考素材→reference-to-video,纯文本→text-to-video)。"""
    entry = find_entry(site, model_id)
    why = _need(entry, first, last, refs, videos, audios)
    prefix = entry["id"].rsplit("/", 1)[0]
    c = entry["caps"]
    if not why and first and not refs and not (c.get("first") or c.get("image_single")):
        # 所选端点只能把首帧当参考图收(多模态端点):同系列有真正的首帧端点就改用它,语义是「从这帧起播」
        strict = [m for m in catalog(site) if m["kind"] == "video" and m["id"] != entry["id"]
                  and m["id"].rsplit("/", 1)[0] == prefix
                  and (m["caps"].get("first") or m["caps"].get("image_single"))
                  and not _need(m, first, last, refs, videos, audios)]
        if strict:
            pick = min(strict, key=lambda m: (m["task"] != "image-to-video", len(m["id"])))
            print(f"[genmedia] RH 视频模型 {entry['id']} 无首帧参数,本次按首帧起播改用同系列端点 {pick['id']}",
                  file=sys.stderr, flush=True)
            return pick
    if not why:
        return entry
    want = ("reference-to-video" if (refs or videos or audios)
            else "image-to-video" if (first or last) else "text-to-video")
    siblings = [m for m in catalog(site) if m["kind"] == "video" and m["id"] != entry["id"]
                and m["id"].rsplit("/", 1)[0] == prefix
                and not _need(m, first, last, refs, videos, audios)]
    siblings.sort(key=lambda m: (m["task"] != want, len(m["id"])))
    if siblings:
        pick = siblings[0]
        print(f"[genmedia] RH 视频模型 {entry['id']} {why},按本次输入改用同系列端点 {pick['id']}",
              file=sys.stderr, flush=True)
        return pick
    raise RuntimeError(f"RunningHub 视频模型 {entry['id']} {why},同系列也没有收得下本次输入的端点;"
                       "请在「🎨 生成模型」页 RH 标签页换模型")


# ---------------- 请求体映射 ----------------

def _num(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _ratio(s) -> float | None:
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*[:x*×]\s*(\d+(?:\.\d+)?)(?:\s*\(.*\))?\s*$", str(s))
    if not m or float(m.group(2)) == 0:
        return None
    return float(m.group(1)) / float(m.group(2))


def _short_side(v) -> float | None:
    """分辨率档位值 → 短边像素:480p / native1080p / 768P / 2k / 1280*720 / 1K。"""
    s = str(v).strip().lower()
    m = re.search(r"(\d+)\s*p$", s)
    if m:
        return float(m.group(1))
    m = re.match(r"^(\d+(?:\.\d+)?)\s*k$", s)
    if m:
        return {1: 1024, 2: 1440, 3: 1728, 4: 2160, 8: 4320}.get(int(float(m.group(1))),
                                                                  float(m.group(1)) * 540)
    m = re.match(r"^(\d+)\s*[x*×]\s*(\d+)$", s)
    if m:
        return float(min(int(m.group(1)), int(m.group(2))))
    return None


def _wh(v) -> tuple[int, int] | None:
    m = re.match(r"^\s*(\d+)\s*[x*×]\s*(\d+)\s*$", str(v))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _pick_resolution(enum: list, target_short: float, target_ratio: float | None):
    """取短边 ≥ 目标的最小档(没有就取最大档);同短边优先 native 原生档;W*H 档再比画幅方向。"""
    cands = []
    for v in enum:
        ss = _short_side(v)
        if ss is None:
            continue
        wh = _wh(v)
        ratio_pen = 0.0
        if wh and target_ratio:
            ratio_pen = abs(wh[0] / wh[1] - target_ratio)
        native = 0 if "native" in str(v).lower() else 1
        cands.append((ratio_pen, ss, native, v))
    if not cands:
        return None
    best_pen = min(c[0] for c in cands)
    cands = [c for c in cands if c[0] - best_pen < 0.05]
    up = [c for c in cands if c[1] >= target_short - 1]
    if up:
        return min(up, key=lambda c: (c[1], c[2]))[3]
    return max(cands, key=lambda c: (c[1], -c[2]))[3]


def _fit_wh(ws: dict, hs: dict, width: int, height: int) -> tuple[int, int]:
    """宽高按参数表上下限等比缩放(保画幅),取 8 的倍数。"""
    lo = max(_num(ws.get("minimum")) or 0, _num(hs.get("minimum")) or 0)
    hi = min(_num(ws.get("maximum")) or 1e9, _num(hs.get("maximum")) or 1e9)
    k = 1.0
    if lo and min(width, height) < lo:
        k = lo / min(width, height)
    if max(width, height) * k > hi:
        k = hi / max(width, height)
    w, h = int(round(width * k / 8) * 8), int(round(height * k / 8) * 8)
    return max(w, int(lo or 0)), max(h, int(lo or 0))


def _pick_ratio(enum: list, aspect: str, adaptive_ok: bool):
    if aspect in [str(x) for x in enum]:
        return aspect
    target = _ratio(aspect)
    if adaptive_ok:
        for fallback in ("adaptive", "auto"):
            if fallback in enum:
                return fallback
    if target is None:
        return None
    scored = [(abs((_ratio(v) or 99) - target), v) for v in enum if _ratio(v)]
    return min(scored)[1] if scored else None


def _pick_duration(spec: dict, duration: float):
    enum = spec.get("enum")
    if enum:
        nums = [(n, v) for v in enum if (n := _num(v)) is not None and n > 0]
        if not nums:
            return None
        up = [x for x in nums if x[0] >= duration - 1e-6]
        n, v = min(up) if up else max(nums)
        return v
    lo, hi = _num(spec.get("minimum")), _num(spec.get("maximum"))
    d = duration
    if lo is not None:
        d = max(d, lo)
    if hi is not None:
        d = min(d, hi)
    if spec.get("type") == "integer":
        d = int(round(d))
    return str(int(round(d))) if spec.get("type") == "string" else d


def _coerce(spec: dict, value):
    t = spec.get("type")
    enum = spec.get("enum")
    if isinstance(value, bool):
        if t == "string" or (enum and all(isinstance(x, str) for x in enum)):
            return "true" if value else "false"
        return value
    if t == "string" and not isinstance(value, str):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return str(value)
    if t == "integer" and isinstance(value, (int, float, str)) and _num(value) is not None:
        return int(round(_num(value)))
    if t == "number" and isinstance(value, str) and _num(value) is not None:
        return _num(value)
    return value


def _is_bool_flag(spec: dict) -> bool:
    return spec.get("type") == "boolean" or sorted(map(str, spec.get("enum") or [])) == ["false", "true"]


def _looks_like_url_default(v) -> bool:
    if isinstance(v, str):
        return v.startswith(("http://", "https://")) or "/view?filename=" in v
    if isinstance(v, list):
        return any(_looks_like_url_default(x) for x in v)
    return False


def build_body(entry: dict, *, prompt: str, negative: str = "", image_urls=None,
               first_url: str = "", last_url: str = "", video_urls=None, audio_urls=None,
               duration: float | None = None, resolution: str = "", aspect: str = "",
               width: int = 0, height: int = 0, seed: int | None = None,
               gen_audio: bool | None = None) -> tuple[dict, list[str]]:
    """按端点参数表组请求体;返回 (body, notes)。notes = 未能生效的输入,供 stderr 如实记录。"""
    props, required, keymap = _normalize(entry["schema"])
    image_urls = list(image_urls or [])
    video_urls = list(video_urls or [])
    audio_urls = list(audio_urls or [])
    body: dict = {}
    notes: list[str] = []
    used = {"prompt": False, "negative": not negative, "images": not image_urls,
            "first": not first_url, "last": not last_url, "videos": not video_urls,
            "audios": not audio_urls}
    target_ratio = _ratio(aspect) or ((width / height) if width and height else None)
    if resolution:
        target_short = _short_side(resolution) or 720
    else:
        target_short = float(min(width, height)) if width and height else 1024

    wh_keys = next(((w, h) for w, h in (("width", "height"), ("outputWidth", "outputHeight"))
                    if w in props and h in props), None)
    fit = _fit_wh(props[wh_keys[0]], props[wh_keys[1]], width, height) if wh_keys and width and height else None

    for k, spec in props.items():
        spec = spec or {}
        enum = spec.get("enum")
        val = None
        if k == "prompt":
            val, used["prompt"] = prompt, True
        elif k == "negativePrompt":
            if negative:
                val, used["negative"] = negative, True
        elif k == "imageUrls":
            if image_urls:
                val, used["images"] = image_urls, True
            elif first_url and not any(x in props for x in _FIRST_KEYS + ("imageUrl",)):
                val, used["first"] = [first_url], True   # 只有参考图位的图生端点:首帧当唯一参考图
            else:
                val = []
        elif k == "imageUrl":
            if spec.get("type") == "array":
                if image_urls:
                    val, used["images"] = image_urls, True
            elif first_url:
                val, used["first"] = first_url, True
            elif image_urls and len(image_urls) == 1 and not props.get("imageUrls"):
                val, used["images"] = image_urls[0], True
        elif k in _FIRST_KEYS:
            if first_url:
                val, used["first"] = first_url, True
        elif k in _LAST_KEYS:
            if last_url:
                val, used["last"] = last_url, True
        elif k == "videoUrls":
            val = video_urls
            used["videos"] = used["videos"] or bool(video_urls)
        elif k == "videoUrl":
            if video_urls:
                val, used["videos"] = video_urls[0], len(video_urls) == 1
        elif k == "audioUrls":
            val = audio_urls
            used["audios"] = used["audios"] or bool(audio_urls)
        elif k == "audioUrl":
            if audio_urls:
                val, used["audios"] = audio_urls[0], len(audio_urls) == 1
        elif k == "duration":
            if duration:
                val = _pick_duration(spec, float(duration))
        elif k == "resolution":
            # 有宽高位时 resolution 会覆盖宽高(Seedream 文档口径):非必填就不填,按宽高出图
            if enum and not (wh_keys and k not in required):
                val = _pick_resolution(enum, target_short, target_ratio)
                if val is None:
                    # 枚举值本身读不出档位(如 megapixels 0.346…):按 x-option-metadata 的 480p/768p 标签选
                    labels = {str(o.get("description")): o.get("value")
                              for o in (spec.get("x-option-metadata") or []) if isinstance(o, dict)}
                    lab = _pick_resolution(list(labels), target_short, target_ratio) if labels else None
                    val = labels.get(lab) if lab is not None else None
        elif k in ("aspectRatio", "ratio"):
            labels = {str(o.get("description")).strip(): o.get("value")
                      for o in (spec.get("x-option-metadata") or []) if isinstance(o, dict)}
            if enum and labels and not any(_ratio(v) for v in enum):
                # 枚举值是序号(1..9),画幅写在选项标签里;给了宽高且有自定义宽高位时选 Custom
                custom = next((v for lab, v in labels.items() if lab.lower() == "custom"), None)
                if custom is not None and wh_keys and fit:
                    val = custom
                elif aspect:
                    lab = _pick_ratio(list(labels), aspect, adaptive_ok=False)
                    val = labels.get(lab) if lab is not None else None
            elif aspect and enum:
                # 首帧图生视频画幅随首帧图:能 adaptive 就 adaptive,免得裁切
                val = _pick_ratio(enum, aspect, adaptive_ok=bool(first_url) and not image_urls)
            elif aspect:
                val = aspect
        elif k == "size":
            if enum:
                sized = [(v, _wh(v)) for v in enum if _wh(v)]
                if sized:
                    tw, th = (width, height) if width and height else (None, None)
                    tr = target_ratio or 16 / 9
                    area = (tw * th) if tw else None

                    def score(x):
                        w, h = x[1]
                        return (abs(w / h - tr) > 0.05, abs(w / h - tr),
                                abs(w * h - area) if area else abs(min(w, h) - target_short))
                    val = min(sized, key=score)[0]
            elif width and height:
                sep = "x" if "x" in str(spec.get("default") or "") else "*"
                val = f"{width}{sep}{height}"
        elif k in ("width", "outputWidth"):
            if fit:
                val = fit[0]
        elif k in ("height", "outputHeight"):
            if fit:
                val = fit[1]
        elif k == "seed":
            if seed is not None:
                hi = _num(spec.get("maximum"))
                val = int(seed) % int(hi) if hi and seed > hi else int(seed)
        elif k in _AUDIO_FLAG_KEYS and _is_bool_flag(spec):
            if gen_audio is not None:
                val = bool(gen_audio)
        if val is None or (isinstance(val, list) and not val and k not in required):
            if k in required:
                default = spec.get("default")
                if default is None:
                    raise RuntimeError(f"RunningHub 模型 {entry['id']} 必填参数 {k} 无对应输入,"
                                       "也没有文档默认值,无法自动填写")
                if _looks_like_url_default(default):
                    if spec.get("type") == "array":
                        body[k] = []
                        continue
                    raise RuntimeError(f"RunningHub 模型 {entry['id']} 必填素材参数 {k} 本次没有对应输入"
                                       "(文档默认值是演示素材,不代填)")
                body[k] = default
            continue
        if enum and not isinstance(val, list):
            sval = _coerce(spec, val)
            if sval not in enum and str(sval) not in [str(x) for x in enum]:
                raise RuntimeError(f"RunningHub 模型 {entry['id']} 参数 {k}={val!r} 不在可选值 {enum} 内")
            val = sval
        else:
            val = _coerce(spec, val)
        if isinstance(val, list):
            mx = spec.get("maxItems")
            if mx is not None and len(val) > int(mx):
                raise RuntimeError(f"RunningHub 模型 {entry['id']} 参数 {k} 至多 {mx} 项,本次 {len(val)} 项")
        body[k] = val

    if not used["prompt"]:
        notes.append("该模型无 prompt 参数,提示词未生效")
    if negative and not used["negative"]:
        # 无独立负面位:并进正面提示词,免得 --negative 被静默丢弃(与 Agentics/RH 工作流同一写法)
        if "prompt" in body:
            body["prompt"] = f"{body['prompt']}\nExclude from the image: {negative}"
            notes.append("该模型无 negativePrompt 参数,负面提示词已并入正面提示词")
    for key, label in (("images", "参考图"), ("first", "首帧图"), ("last", "尾帧图"),
                       ("videos", "参考视频"), ("audios", "参考音频")):
        if not used[key]:
            raise RuntimeError(f"RunningHub 模型 {entry['id']} 没有可接{label}的参数,"
                               "请换模型或去掉该输入")
    if keymap:
        # 标准名 → 真实「节点号##字段」参数名;多槽位数组按序拆到各槽(参考图1..N)
        real = {}
        for k, v in body.items():
            keys = keymap.get(k, [k])
            if isinstance(v, list) and len(keys) > 1 or (isinstance(v, list) and props.get(k, {}).get("maxItems")
                                                        and keys != [k]):
                for rk, item in zip(keys, v):
                    real[rk] = item
            else:
                real[keys[0]] = v
        body = real
    return body, notes


# ---------------- 上传 / 提交 / 轮询 ----------------

def upload(site: str, key: str, path: str) -> str:
    """上传本地文件,返回标准模型 API 用的 download_url(官方口径:有效期一天)。"""
    p = Path(path)
    if not p.is_file():
        raise RuntimeError(f"输入文件不存在: {path}")
    if p.stat().st_size > 30 * 1024 * 1024:
        raise RuntimeError(f"RunningHub 上传限制单文件 30MB,超限: {path}")
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    body = b"".join([
        (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
         f"filename=\"{p.name}\"\r\nContent-Type: {mime}\r\n\r\n").encode(),
        p.read_bytes(), f"\r\n--{boundary}--\r\n".encode()])
    raw = _http(SITES[site]["base"] + "/openapi/v2/media/upload/binary", body,
                {"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Authorization": f"Bearer {key}"}, timeout=300)
    try:
        resp = json.loads(raw)
    except ValueError:
        raise RuntimeError(f"RunningHub 上传返回非 JSON:{raw[:300]!r}") from None
    data = resp.get("data") or {}
    url = data.get("download_url") or data.get("downloadUrl") or data.get("url")
    if resp.get("code") not in (0, 200) or not url:
        raise RuntimeError(f"RunningHub 上传失败(code={resp.get('code')}):"
                           f"{str(resp.get('message') or resp.get('msg'))[:300]}")
    return url


def _err_text(resp: dict) -> str:
    parts = [str(resp.get(k)) for k in ("errorCode", "errorMessage", "msg", "message") if resp.get(k)]
    fr = resp.get("failedReason")
    if fr:
        parts.append(json.dumps(fr, ensure_ascii=False)[:600])
    return ";".join(parts) or json.dumps(resp, ensure_ascii=False)[:600]


def submit(site: str, key: str, model_id: str, body: dict) -> str:
    resp = _post(site, key, model_id, body, timeout=120)
    task_id = str(resp.get("taskId") or (resp.get("data") or {}).get("taskId") or "")
    if not task_id or resp.get("errorCode") or str(resp.get("status") or "").upper() == "FAILED":
        raise RuntimeError(f"RunningHub 建任务失败({model_id}):{_err_text(resp)}")
    return task_id


def wait(site: str, key: str, task_id: str, timeout: int, label: str = "") -> dict:
    """轮询到终态;SUCCESS 返回完整响应(含 results/usage),FAILED/超时报错。
    网络瞬断连续 3 次内容忍(任务已计费,别因一次掐流量丢产物)。"""
    t0 = time.time()
    net_fail = 0
    last_status = ""
    while True:
        try:
            resp = _post(site, key, "query", {"taskId": task_id}, timeout=60)
            net_fail = 0
        except RuntimeError as e:
            net_fail += 1
            if net_fail > 3:
                raise RuntimeError(f"RunningHub 查询任务 {task_id} 连续失败:{e}") from e
            time.sleep(POLL_INTERVAL)
            continue
        status = str(resp.get("status") or "").upper()
        if status == "SUCCESS":
            return resp
        if status == "FAILED" or resp.get("errorCode"):
            raise RuntimeError(f"RunningHub 任务 {task_id} 失败:{_err_text(resp)}")
        if status != last_status:
            print(f"[genmedia] RH {label} 任务 {task_id} 状态 {status or '?'}"
                  f"({int(time.time() - t0)}s)", file=sys.stderr, flush=True)
            last_status = status
        if time.time() - t0 > timeout:
            raise RuntimeError(f"RunningHub 任务 {task_id} 等待超时({timeout}s),"
                               "任务可能仍在云端运行(已计费),可凭 taskId 到 RunningHub 控制台取回")
        time.sleep(POLL_INTERVAL)


def result_url(resp: dict, want: str) -> str:
    """want = image | video:从 results 里挑对应类型的产物 URL。"""
    exts = {"image": (".png", ".jpg", ".jpeg", ".webp"),
            "video": (".mp4", ".mov", ".webm")}[want]
    items = [r for r in (resp.get("results") or []) if isinstance(r, dict) and r.get("url")]

    def rank(r):
        name = str(r["url"]).split("?", 1)[0].lower()
        ot = str(r.get("outputType") or "").lower().lstrip(".")
        return 0 if name.endswith(exts) or ("." + ot) in exts or ot == want else 1
    if not items:
        raise RuntimeError(f"RunningHub 任务成功但无产物 URL:{json.dumps(resp, ensure_ascii=False)[:400]}")
    return sorted(items, key=rank)[0]["url"]


def download(url: str, attempts: int = 4) -> bytes:
    last = None
    for i in range(attempts):
        try:
            return _http(url, timeout=600)
        except RuntimeError as e:
            last = e
            print(f"[genmedia] RH 产物下载中断,第 {i + 1}/{attempts} 次重试…", file=sys.stderr)
            time.sleep(POLL_INTERVAL)
    raise RuntimeError(f"RunningHub 产物下载失败:{last}")


def usage_note(resp: dict) -> str:
    u = resp.get("usage") or {}
    bits = [f"{k}={u[k]}" for k in ("consumeMoney", "thirdPartyConsumeMoney", "consumeCoins", "taskCostTime")
            if u.get(k) not in (None, "")]
    return " ".join(bits)
