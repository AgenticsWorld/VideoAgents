"""Fal 模型发现(2026-10-06):「生成模型」页图像 / 视频的 Fal 标签页「搜索 Fal 模型」,以及 genmedia 里
没有内置请求体映射的 Fal 模型(家族 generic)按端点参数表整形请求体。

目录来自 Fal 平台模型搜索接口(公开,不需要 Key):
    GET https://api.fal.ai/v1/models?category=<类别>&status=active&limit=≤100[&q=<关键词>][&cursor=…]
    GET https://api.fal.ai/v1/models?endpoint_id=<端点>&expand=openapi-3.0      单个端点 + 参数表

- 设置页里一条 = 一个「模型前缀」:同一模型的 text-to-video / image-to-video / reference-to-video
  (图像:text-to-image / edit / image-to-image / multi)几个端点并成一条,模型 ID 存前缀——与内置清单同一约定,
  genmedia 生成时按本次输入挑端点。有的模型文生端点就是前缀本身(如 fal-ai/veo3.1),resolve_* 按目录核对。
- 各端点参数名、取值写法都不一样(时长有整数、"5"、"8s" 三种写法,尾帧有 end_image_url / tail_image_url …),
  shape_video / shape_image 读该端点的参数表:只发它收的字段,时长 / 分辨率 / 画幅取它枚举里最接近的值;
  端点收不下本次输入(如不支持尾帧、不收参考图)就报错,不悄悄丢掉。
- genmedia 内置了映射的家族(Seedance / MiniMax H3 / Kling / Wan 3.0 / Seedream / Nano Banana / GPT Image /
  FLUX.2 / Kontext / Qwen / Hunyuan)不走这里。目录查不到的端点(自己部署的私有应用等)genmedia 回落旧的通用映射。

缓存:搜索结果进程内 10 分钟;单个端点(含「不存在」)落盘 data/.videoagents/fal_models/ 24 小时。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
RUNTIME_DIR = Path(os.environ.get("VIDEOAGENTS_RUNTIME_DIR", DATA_DIR / ".videoagents")).expanduser().resolve()
CACHE_DIR = RUNTIME_DIR / "fal_models"
API_URL = "https://api.fal.ai/v1/models"
SEARCH_TTL = 600
ENDPOINT_TTL = 24 * 3600
PAGE_LIMIT = 100           # 接口单页上限
RETRY_WAITS = (2.0, 5.0, 10.0, 15.0)

KINDS = ("image", "video")
CATEGORIES = {"image": ("text-to-image", "image-to-image"),
              "video": ("text-to-video", "image-to-video")}      # reference-to-video 端点在目录里归 image-to-video
IMAGE_TASK_SUFFIXES = ("text-to-image", "image-to-image", "edit", "multi")

FIRST_FRAME_KEYS = ("image_url", "start_image_url", "first_frame_url", "first_image_url", "start_frame_url")
LAST_FRAME_KEYS = ("end_image_url", "tail_image_url", "last_frame_url", "last_image_url", "end_frame_url")
REF_IMAGE_LIST_KEYS = ("reference_image_urls", "image_urls", "input_image_urls", "reference_images")
REF_VIDEO_LIST_KEYS = ("reference_video_urls", "video_urls")
REF_VIDEO_KEYS = ("video_url", "reference_video_url")
REF_AUDIO_LIST_KEYS = ("reference_audio_urls", "audio_urls")
REF_AUDIO_KEYS = ("audio_url", "reference_audio_url")
AUDIO_FLAG_KEYS = ("generate_audio", "enable_audio", "with_audio", "audio")
IMAGE_SIZE_RATIOS = {"square_hd": 1.0, "square": 1.0, "portrait_4_3": 3 / 4, "portrait_16_9": 9 / 16,
                     "landscape_4_3": 4 / 3, "landscape_16_9": 16 / 9}
RESOLUTION_HEIGHTS = {"4k": 2160, "2k": 1440, "1k": 1024, "hd": 720, "fhd": 1080, "uhd": 2160}

_SEARCH_CACHE: dict[tuple, tuple[float, list]] = {}


class CatalogUnavailable(RuntimeError):
    """目录接口连不上 / 返回异常(与「目录里没有这个端点」区分:后者 lookup 返回 None)。"""


# ---------------------------------------------------------------- HTTP / 缓存
def _get(params: dict, api_key: str = "", timeout: int = 30) -> dict:
    """查目录接口。匿名可用但限流较紧(实测连续十来次即 429),有 Fal Key 就带上。
    服务端有回应的 429 / 5xx 按退避重试;连不上(断网、DNS、TLS)只快速重试一次就报错,不让设置页干等。"""
    url = API_URL + "?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Key {api_key}"
    last: Exception | str | None = None
    transport_failures = 0
    for attempt in range(len(RETRY_WAITS)):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            if not isinstance(data, dict) or data.get("error"):
                raise CatalogUnavailable(f"Fal 模型目录返回错误:{json.dumps(data, ensure_ascii=False)[:300]}")
            return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"models": []}          # 按 endpoint_id 查不存在的端点:404 not_found
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code != 429 and e.code < 500:
                raise CatalogUnavailable(f"Fal 模型目录 HTTP {e.code}:{detail}") from e
            last = f"HTTP {e.code}:{detail}"
            try:
                wait = float(e.headers.get("Retry-After") or 0)
            except (TypeError, ValueError):
                wait = 0.0
            time.sleep(min(20.0, wait or RETRY_WAITS[attempt]))
        except CatalogUnavailable:
            raise
        except Exception as e:  # noqa: BLE001  DNS / TCP / TLS / 超时
            last = e
            transport_failures += 1
            if transport_failures > 1:
                break
            time.sleep(0.5)
    raise CatalogUnavailable(f"Fal 模型目录({API_URL})暂时不可用:{last}")


def _cache_path(endpoint_id: str) -> Path:
    return CACHE_DIR / "endpoints" / (hashlib.sha1(endpoint_id.encode("utf-8")).hexdigest()[:20] + ".json")


def _cache_read(endpoint_id: str) -> dict | None:
    path = _cache_path(endpoint_id)
    try:
        if time.time() - path.stat().st_mtime > ENDPOINT_TTL:
            return None
        rec = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return rec if isinstance(rec, dict) and rec.get("endpoint_id") == endpoint_id else None


def _cache_write(endpoint_id: str, rec: dict) -> None:
    path = _cache_path(endpoint_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------- 参数表归一
def _flatten(spec, schemas: dict, depth: int = 0) -> dict:
    """把一个属性的 JSON Schema(可能套 $ref / anyOf / allOf)摊平成
    {types, enum, min, max, items, object_keys};取各分支的并集。"""
    out = {"types": [], "enum": None, "min": None, "max": None, "items": None, "object_keys": None}
    if not isinstance(spec, dict) or depth > 6:
        return out
    if "$ref" in spec:
        return _flatten(schemas.get(str(spec["$ref"]).rsplit("/", 1)[-1]) or {}, schemas, depth + 1)
    branches = [b for key in ("anyOf", "oneOf", "allOf") for b in (spec.get(key) or [])]
    for b in branches:
        sub = _flatten(b, schemas, depth + 1)
        out["types"] += [t for t in sub["types"] if t not in out["types"]]
        for key in ("enum", "min", "max", "items", "object_keys"):
            if out[key] is None and sub[key] is not None:
                out[key] = sub[key]
    t = spec.get("type")
    for one in (t if isinstance(t, list) else [t] if t else []):
        if one not in out["types"]:
            out["types"].append(one)
    if isinstance(spec.get("enum"), list):
        out["enum"] = list(spec["enum"])
    for src, dst in (("minimum", "min"), ("maximum", "max")):
        if isinstance(spec.get(src), (int, float)):
            out[dst] = spec[src]
    if isinstance(spec.get("items"), dict):
        out["items"] = _flatten(spec["items"], schemas, depth + 1)
        if "array" not in out["types"]:
            out["types"].append("array")
    if isinstance(spec.get("properties"), dict):
        out["object_keys"] = sorted(spec["properties"])
        if "object" not in out["types"]:
            out["types"].append("object")
    return out


def _input_schema(endpoint_id: str, openapi: dict) -> dict:
    """从端点的 OpenAPI 取请求体参数表:{props: {名: 摊平后的属性}, required: [...]}。"""
    schemas = ((openapi.get("components") or {}).get("schemas") or {}) if isinstance(openapi, dict) else {}
    post = (((openapi.get("paths") or {}).get("/" + endpoint_id) or {}).get("post") or {}) if isinstance(openapi, dict) else {}
    body = (((post.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema") or {}
    if "$ref" in body:
        body = schemas.get(str(body["$ref"]).rsplit("/", 1)[-1]) or {}
    if not body.get("properties"):
        body = next((v for k, v in schemas.items() if k.endswith("Input") and isinstance(v, dict)), {})
    props = {name: _flatten(spec, schemas) for name, spec in (body.get("properties") or {}).items()}
    return {"props": props, "required": [r for r in (body.get("required") or []) if r in props]}


# ---------------------------------------------------------------- 目录
def lookup(endpoint_id: str, refresh: bool = False, api_key: str = "") -> dict | None:
    """单个端点:{endpoint_id, name, category, input: {props, required}};目录里没有返回 None;连不上抛 CatalogUnavailable。"""
    endpoint_id = str(endpoint_id or "").strip().strip("/")
    if not endpoint_id:
        return None
    cached = None if refresh else _cache_read(endpoint_id)
    if cached is not None:
        return None if cached.get("missing") else cached
    models = _get({"endpoint_id": endpoint_id, "expand": "openapi-3.0"}, api_key).get("models") or []
    # 不存在的端点带 expand 查询时会回一条没有 metadata 的空壳,不算命中
    hit = next((m for m in models if m.get("endpoint_id") == endpoint_id and m.get("metadata")), None)
    if not hit:
        _cache_write(endpoint_id, {"endpoint_id": endpoint_id, "missing": True})
        return None
    meta = hit.get("metadata") or {}
    rec = {"endpoint_id": endpoint_id, "name": meta.get("display_name") or endpoint_id,
           "category": meta.get("category") or "", "input": _input_schema(endpoint_id, hit.get("openapi") or {})}
    _cache_write(endpoint_id, rec)
    return rec


def split_endpoint(endpoint_id: str, kind: str) -> tuple[str, str]:
    """端点 → (模型前缀, 任务段);末段不是任务段时任务段为空串、前缀就是端点本身。"""
    eid = str(endpoint_id or "").strip().strip("/")
    head, _, tail = eid.rpartition("/")
    is_task = tail.endswith("-to-video") if kind == "video" else tail in IMAGE_TASK_SUFFIXES
    return (head, tail) if head and is_task else (eid, "")


_TASK_PHRASE_RE = re.compile(
    r"[(\[]?\b(?:text|image|reference|video|audio|first[\s-]last[\s-]frame)[\s-]to[\s-](?:video|image)\b[)\]]?", re.I)
_TASK_TAIL_RE = re.compile(r"[\s\-–|:·(\[]*\b(?:edit|multi)\b[\])]*\s*$", re.I)


def _family_name(display_name: str) -> str:
    """目录里的展示名是按端点起的(「… Text to Video」「… Edit」),并成一条时去掉任务词。"""
    name = str(display_name or "").strip()
    stripped = _TASK_TAIL_RE.sub("", _TASK_PHRASE_RE.sub(" ", name))
    stripped = re.sub(r"\s{2,}", " ", stripped).strip(" -–|:·")
    return stripped or name


def search(kind: str, query: str = "", limit: int = 40, refresh: bool = False, api_key: str = "") -> list[dict]:
    """按类别(可带关键词)搜目录并按模型前缀归并,保持目录返回的顺序。
    每条 {id, name, tasks, endpoints, description, updated_at}。"""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    query = str(query or "").strip()
    key = (kind, query.lower())
    hit = _SEARCH_CACHE.get(key)
    if hit and not refresh and time.time() - hit[0] < SEARCH_TTL:
        return hit[1][:limit]
    families: dict[str, dict] = {}
    for rank, category in enumerate(CATEGORIES[kind]):
        params = {"category": category, "status": "active", "limit": PAGE_LIMIT}
        if query:
            params["q"] = query
        for pos, m in enumerate(_get(params, api_key).get("models") or []):
            eid = str(m.get("endpoint_id") or "")
            meta = m.get("metadata") or {}
            if not eid or (meta.get("kind") and meta.get("kind") != "inference"):
                continue
            prefix, task = split_endpoint(eid, kind)
            fam = families.setdefault(prefix, {"id": prefix, "name": _family_name(meta.get("display_name") or prefix),
                                               "tasks": [], "endpoints": [], "description": "", "updated_at": "",
                                               "_order": (pos, rank)})
            fam["_order"] = min(fam["_order"], (pos, rank))
            task = task or str(meta.get("category") or category)
            if task not in fam["tasks"]:
                fam["tasks"].append(task)
            if eid not in fam["endpoints"]:
                fam["endpoints"].append(eid)
            if not fam["description"] or task.startswith("text-to-"):
                fam["description"] = str(meta.get("description") or fam["description"])[:300]
            fam["updated_at"] = max(fam["updated_at"], str(meta.get("updated_at") or ""))
    rows = sorted(families.values(), key=lambda f: f.pop("_order"))
    for fam in rows:
        fam["tasks"].sort()
    _SEARCH_CACHE[key] = (time.time(), rows)
    return rows[:limit]


def resolve_video(model: str, task: str, needs_last: bool = False, api_key: str = "") -> dict | None:
    """模型 ID(前缀或完整端点)+ 任务段 → 目录里实际的端点记录;目录里没有返回 None。
    needs_last:本次带尾帧——图生端点不收尾帧而同模型另有 first-last-frame-to-video 端点时改用后者。"""
    mid = str(model or "").strip().strip("/")
    if split_endpoint(mid, "video")[1]:
        return lookup(mid, api_key=api_key)                       # 已是完整端点:原样用,不按输入切换
    rec = lookup(f"{mid}/{task}", api_key=api_key)
    if needs_last and task == "image-to-video" and not (
            rec and _first_key(rec["input"]["props"], LAST_FRAME_KEYS, want_array=False)):
        rec = lookup(f"{mid}/first-last-frame-to-video", api_key=api_key) or rec
    if rec:
        return rec
    bare = lookup(mid, api_key=api_key)                           # 文生 / 图生端点就是前缀本身的模型
    if bare and (bare.get("category") == task or (task == "reference-to-video" and _takes_refs(bare))):
        return bare
    return None


def resolve_image(model: str, has_refs: bool, api_key: str = "") -> dict | None:
    mid = str(model or "").strip().strip("/")
    if split_endpoint(mid, "image")[1]:
        return lookup(mid, api_key=api_key)
    for suffix in (("edit", "image-to-image", "multi") if has_refs else ("text-to-image",)):
        rec = lookup(f"{mid}/{suffix}", api_key=api_key)
        if rec:
            return rec
    bare = lookup(mid, api_key=api_key)
    if bare and (bare.get("category") == ("image-to-image" if has_refs else "text-to-image")
                 or (has_refs and _takes_refs(bare))):
        return bare
    return None


def _takes_refs(rec: dict) -> bool:
    props = (rec.get("input") or {}).get("props") or {}
    return any(k in props for k in REF_IMAGE_LIST_KEYS + REF_VIDEO_LIST_KEYS)


# ---------------------------------------------------------------- 取值贴合
def _numbers(value) -> list[float]:
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(value))]


def _fit_number(spec: dict, value: float, label: str, notes: list[str]):
    """按参数表把数值写成端点要的样子:枚举取最接近的一项(保留原写法,如 "8s"),否则按类型取整 / 夹到上下限。"""
    enum = [e for e in (spec.get("enum") or []) if _numbers(e)]
    if enum:
        picked = min(enum, key=lambda e: abs(_numbers(e)[0] - float(value)))
        if _numbers(picked)[0] != float(value):
            notes.append(f"{label} {value:g} 不在该端点可选值 {enum} 里,已取最接近的 {picked}")
        return picked
    out: float = float(value)
    if spec.get("min") is not None:
        out = max(out, float(spec["min"]))
    if spec.get("max") is not None:
        out = min(out, float(spec["max"]))
    types = spec.get("types") or []
    if "integer" in types or ("number" not in types and "string" in types):
        out = int(round(out))
    if out != float(value):
        notes.append(f"{label} {value:g} 已按该端点范围调整为 {out:g}")
    return str(out) if ("string" in types and "integer" not in types and "number" not in types) else out


def _height(value) -> float | None:
    v = str(value).strip().lower()
    if v in RESOLUTION_HEIGHTS:
        return float(RESOLUTION_HEIGHTS[v])
    nums = _numbers(v)
    if not nums:
        return None
    return nums[-1] if re.search(r"\d\s*[x×*]\s*\d", v) else nums[0]


def _fit_resolution(spec: dict, resolution: str, notes: list[str]):
    enum = spec.get("enum") or []
    if not enum:
        return resolution
    exact = next((e for e in enum if str(e).lower() == str(resolution).lower()), None)
    if exact is not None:
        return exact
    want = _height(resolution)
    sized = [(e, _height(e)) for e in enum]
    sized = [(e, h) for e, h in sized if h is not None]
    if want is None or not sized:
        return None
    picked = min(sized, key=lambda eh: abs(eh[1] - want))[0]
    notes.append(f"分辨率 {resolution} 不在该端点可选值 {enum} 里,已取最接近的 {picked}")
    return picked


def _ratio(value) -> float | None:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[:/x]\s*(\d+(?:\.\d+)?)\s*", str(value))
    return float(m.group(1)) / float(m.group(2)) if m and float(m.group(2)) else None


def _fit_ratio(spec: dict, aspect: str, notes: list[str]):
    enum = spec.get("enum") or []
    if not enum:
        return aspect
    if aspect in enum:
        return aspect
    want = _ratio(aspect)
    rated = [(e, _ratio(e)) for e in enum]
    rated = [(e, r) for e, r in rated if r is not None]
    if want is None or not rated:
        return None
    picked = min(rated, key=lambda er: abs(er[1] - want))[0]
    notes.append(f"画幅 {aspect} 不在该端点可选值 {enum} 里,已取最接近的 {picked}")
    return picked


def _first_key(props: dict, names: tuple, want_array: bool | None = None) -> str:
    for name in names:
        spec = props.get(name)
        if spec is None:
            continue
        is_array = "array" in (spec.get("types") or [])
        if want_array is None or want_array == is_array:
            return name
    return ""


def _put_refs(body: dict, props: dict, values: list, list_keys: tuple, single_keys: tuple,
              label: str, endpoint: str, to_url) -> None:
    if not values:
        return
    key = _first_key(props, list_keys, want_array=True)
    if key:
        body[key] = [to_url(v) for v in values]
        return
    key = _first_key(props, single_keys, want_array=False)
    if key and len(values) == 1 and key not in body:
        body[key] = to_url(values[0])
        return
    raise RuntimeError(f"Fal 端点 {endpoint} 收不下本次的{label}({len(values)} 个):"
                       + (f"只有单个 {key} 位" if key else "参数表里没有对应字段")
                       + f";它的参数有 {', '.join(sorted(props))}")


def _check_required(body: dict, entry: dict) -> None:
    missing = [r for r in entry["input"]["required"] if r not in body]
    if missing:
        raise RuntimeError(f"Fal 端点 {entry['endpoint_id']} 的必填参数 {', '.join(missing)} 本次没有对应输入"
                           "(换同模型的其它任务端点,或补上相应素材)")


# ---------------------------------------------------------------- 请求体整形
def shape_video(entry: dict, *, prompt: str, duration=None, resolution: str = "", aspect: str = "", seed=None,
                gen_audio=None, first: str = "", last: str = "", refs=(), video_refs=(), audio_refs=(),
                to_url=str, video_to_url=str) -> tuple[dict, list[str]]:
    """按端点参数表组视频请求体,返回 (body, notes);notes 是给用户看的取值调整说明。"""
    props, endpoint, notes = entry["input"]["props"], entry["endpoint_id"], []
    body: dict = {}
    if "prompt" in props:
        body["prompt"] = prompt
    if duration and "duration" in props:
        body["duration"] = _fit_number(props["duration"], float(duration), "时长", notes)
    if resolution and "resolution" in props:
        res = _fit_resolution(props["resolution"], resolution, notes)
        if res is not None:
            body["resolution"] = res
    if aspect and "aspect_ratio" in props:
        ratio = _fit_ratio(props["aspect_ratio"], aspect, notes)
        if ratio is not None:
            body["aspect_ratio"] = ratio
    if seed is not None and "seed" in props:
        body["seed"] = seed
    if gen_audio is not None:
        key = next((k for k in AUDIO_FLAG_KEYS if "boolean" in ((props.get(k) or {}).get("types") or [])), "")
        if key:
            body[key] = bool(gen_audio)
        else:
            notes.append("该端点没有有声 / 无声开关,--generate-audio 已忽略")
    if first:
        key = _first_key(props, FIRST_FRAME_KEYS, want_array=False)
        if not key:
            raise RuntimeError(f"Fal 端点 {endpoint} 不收首帧图;它的参数有 {', '.join(sorted(props))}")
        body[key] = to_url(first)
    if last:
        key = _first_key(props, LAST_FRAME_KEYS, want_array=False)
        if not key:
            raise RuntimeError(f"Fal 端点 {endpoint} 不支持尾帧图;它的参数有 {', '.join(sorted(props))}")
        body[key] = to_url(last)
    _put_refs(body, props, list(refs or []), REF_IMAGE_LIST_KEYS, FIRST_FRAME_KEYS[:1], "参考图", endpoint, to_url)
    _put_refs(body, props, list(video_refs or []), REF_VIDEO_LIST_KEYS, REF_VIDEO_KEYS, "参考视频", endpoint, video_to_url)
    _put_refs(body, props, list(audio_refs or []), REF_AUDIO_LIST_KEYS, REF_AUDIO_KEYS, "参考音频", endpoint, to_url)
    _check_required(body, entry)
    return body, notes


def shape_image(entry: dict, *, prompt: str, negative: str = "", width: int, height: int, seed=None,
                fmt: str = "", refs=(), to_url=str) -> tuple[dict, list[str]]:
    """按端点参数表组图像请求体,返回 (body, notes)。"""
    props, endpoint, notes = entry["input"]["props"], entry["endpoint_id"], []
    body: dict = {"prompt": prompt} if "prompt" in props else {}
    if negative:
        if "negative_prompt" in props:
            body["negative_prompt"] = negative
        elif "prompt" in body:
            body["prompt"] = f"{prompt}\nAvoid: {negative}"
    aspect = f"{int(width)}:{int(height)}"
    size = props.get("image_size")
    if size is not None:
        if "object" in (size.get("types") or []):
            body["image_size"] = {"width": int(width), "height": int(height)}
        else:
            named = [e for e in (size.get("enum") or []) if e in IMAGE_SIZE_RATIOS]
            if named:
                body["image_size"] = min(named, key=lambda e: abs(IMAGE_SIZE_RATIOS[e] - width / height))
    elif "width" in props and "height" in props:
        body["width"] = _fit_number(props["width"], int(width), "宽", notes)
        body["height"] = _fit_number(props["height"], int(height), "高", notes)
    else:
        if "aspect_ratio" in props:
            ratio = _fit_ratio(props["aspect_ratio"], aspect, [])       # 像素宽高换算成画幅,不逐次提示
            if ratio is not None:
                body["aspect_ratio"] = ratio
        if "resolution" in props:
            long_side = max(int(width), int(height))
            res = _fit_resolution(props["resolution"], "1K" if long_side <= 1280 else "2K" if long_side <= 2560 else "4K", [])
            if res is not None:
                body["resolution"] = res
    if seed is not None and "seed" in props:
        body["seed"] = seed
    fmt_enum = (props.get("output_format") or {}).get("enum") or []
    if fmt and fmt in fmt_enum:
        body["output_format"] = fmt
    _put_refs(body, props, list(refs or []), REF_IMAGE_LIST_KEYS, FIRST_FRAME_KEYS[:1], "参考图", endpoint, to_url)
    _check_required(body, entry)
    return body, notes


if __name__ == "__main__":      # python3 modules/fal_models.py video kling
    import sys
    rows = search(sys.argv[1] if len(sys.argv) > 1 else "video", " ".join(sys.argv[2:]))
    for row in rows:
        print(f"{row['id']:55s} {row['name']}  [{', '.join(row['tasks'])}]")
