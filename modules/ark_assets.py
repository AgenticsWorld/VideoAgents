"""长镜头续接素材自动入方舟虚拟人像库(2026-10-09)。

尾帧/尾段里的人物是真人感画面,以预签名 URL / base64 原样提交会被方舟隐私过滤
(InputImageSensitiveContentDetected.PrivacyInformation)随机拒收;入库过审后以
asset://<id> 提交即可规避(与人物概念图同一机制,见 core.py 虚拟人像资产库)。

触发条件(genmedia 提交方舟视频任务前逐份判断):
  视频渠道火山方舟 + Seedance 2.x(调用方判)、「设置 → 高级 → 虚拟人像资产库」启用、
  项目「时长设置 → 长镜头」开启,且素材是续接素材——按连接方式:
    尾帧图片 assets/clips/<ep>/<前组>.last_frame.png        → AssetType=Image(在 refs)
    尾段视频 assets/continuity/<ep>/<前组>.continuation.mp4 → AssetType=Video(在 video_refs)
提交前确认已入库且审核 Active:台账有记录也经 GetAsset 复核(库可能被全自动管理或手动清空),
处理中则等待;入库失败、审核未过、不合方舟素材规格或等待超时只告警,回退原提交方式。

台账与人物图共用 data/.videoagents/avatar_assets.json(assets 按文件内容 sha256),条目带
kind=continuity 与 slot(项目|集|前组|类型):同一 slot 换了新素材(前组重出)先删旧资产,省素材额度。
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import time
import urllib.error
from pathlib import Path

from modules.continuity_refs import TAIL_IMAGE, TAIL_VIDEO
from modules.volc_openapi import signed_call

ARK_HOST = "ark.cn-beijing.volcengineapi.com"
API_VERSION = "2024-01-01"
WAIT_S = {"Image": 600, "Video": 900}   # 等审核 Active 的上限;官方注明视频素材处理更久、不承诺 SLA
POLL_S = 5
_THROTTLE_RE = re.compile(
    r"Quota\w*Exceeded|QPM|QPS|Throttl|RateLimit|TooManyRequests|HTTP 429", re.I)
_CALL_RETRIES = 6                       # 限流退避同 core._avatar_call:2s→4s→…≤30s(+抖动)
# 审核失败里属于处理环节偶发故障、可下次重提的错误码(GetAsset Error.Code);其余(内容审核、
# 格式/尺寸不符)同一份素材重提必然再败,台账记 Failed 不再重试,前组重出换了素材才会重新入库
_RETRYABLE_FAIL = {"DownloadFailed", "ModerationServiceErrorUploadFailed", "InternalError"}
_KIND_LABEL = {"Image": "尾帧图片", "Video": "尾段视频"}


class AssetsError(RuntimeError):
    pass


def tail_kind(path) -> str | None:
    """续接素材类型:前组尾帧图片 → Image;前组尾段视频 → Video;其余 None
    (目录口径同 modules/continuity_refs.plan)。"""
    p = Path(path)
    if len(p.parts) < 4 or p.parent.parent.parent.name != "assets":
        return None
    if p.name.endswith(TAIL_IMAGE) and p.parent.parent.name == "clips":
        return "Image"
    if p.name.endswith(TAIL_VIDEO) and p.parent.parent.name == "continuity":
        return "Video"
    return None


def _read_json(path) -> dict:
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _write_json(path: Path, obj: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2))
    os.replace(tmp, path)


def _long_take_on(base: Path) -> bool:
    return ((_read_json(base / "settings.json").get("duration") or {}).get("long_take") is True)


class _Ledger:
    """台账读改写事务(fcntl 独占锁防并发 genmedia 互相覆盖;非 POSIX 退化为无锁)。
    服务端 core.py 写同一台账不走此锁(await 前后整读整写),极少数情况会盖掉这里刚记的
    条目——下次提交时 GetAsset 复核 / 重新入库即自愈。"""

    def __init__(self, path: Path):
        self.path, self.fh, self.data = Path(path), None, {}

    def __enter__(self) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path.with_name(self.path.name + ".lock"), "a+")
        try:
            import fcntl
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        self.data = _read_json(self.path)
        self.data.setdefault("assets", {})
        self.data.setdefault("files", {})
        return self.data

    def __exit__(self, exc_type, *exc):
        try:
            if exc_type is None:
                _write_json(self.path, self.data)
        finally:
            try:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            self.fh.close()


# ---------------- 方舟素材资产 OpenAPI ----------------

def _keys(conf: dict) -> tuple[str, str]:
    """AK/SK:资产库页配置优先,留空回退文件托管 TOS 的 AK/SK 或环境变量(同 core._avatar_keys)。"""
    av = conf.get("avatar_assets") or {}
    ak = str(av.get("access_key") or "").strip()
    sk = str(av.get("secret_key") or "").strip()
    if not (ak and sk):
        tos = (conf.get("storage") or {}).get("tos") or {}
        ak = ak or str(tos.get("access_key") or os.environ.get("TOS_ACCESS_KEY", "")).strip()
        sk = sk or str(tos.get("secret_key") or os.environ.get("TOS_SECRET_KEY", "")).strip()
    if not (ak and sk):
        raise AssetsError("需先配置火山引擎 Access Key/Secret Key(⚙️ 设置 → 高级 → 虚拟人像资产库)")
    return ak, sk


def call(conf: dict, action: str, body: dict) -> dict:
    """方舟素材资产 OpenAPI(AK/SK 签名,Service=ark,cn-beijing),返回 Result;
    限流类错误指数退避重试,其余错误抛 AssetsError。"""
    ak, sk = _keys(conf)
    for attempt in range(_CALL_RETRIES + 1):
        try:
            return _call_once(ak, sk, action, body)
        except AssetsError as e:
            if attempt >= _CALL_RETRIES or not _THROTTLE_RE.search(str(e)):
                raise
            time.sleep(min(30.0, 2.0 ** (attempt + 1)) + random.random())
    raise AssertionError("unreachable")


def _call_once(ak: str, sk: str, action: str, body: dict) -> dict:
    try:
        r = signed_call(ak, sk, action, API_VERSION, body,
                        service="ark", region="cn-beijing", host=ARK_HOST)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        try:
            err = (json.loads(detail).get("ResponseMetadata") or {}).get("Error") or {}
            if err:
                detail = f"{err.get('Code')}: {err.get('Message')}"
        except Exception:  # noqa: BLE001 — 非 JSON 报文原样保留
            pass
        raise AssetsError(f"{action} HTTP {e.code}:{detail}") from None
    except Exception as e:  # noqa: BLE001
        raise AssetsError(f"{action} 调用失败:{e}") from None
    err = (r.get("ResponseMetadata") or {}).get("Error") or {}
    if err:
        raise AssetsError(f"{action} 失败:{err.get('Code')}: {err.get('Message')}")
    return r.get("Result") or {}


def _project_name(conf: dict) -> str:
    return (conf.get("avatar_assets") or {}).get("project_name") or "default"


def _status(conf: dict, aid: str) -> tuple[str | None, str]:
    """GetAsset → (Status, 失败原因);查询报错(资产已删 / 网络)返回 (None, 报错原文)。"""
    try:
        res = call(conf, "GetAsset", {"Id": aid, "ProjectName": _project_name(conf)})
    except AssetsError as e:
        return None, str(e)
    err = res.get("Error") or {}
    return (res.get("Status") or None,
            f"{err.get('Code')}: {err.get('Message')}" if err.get("Code") else "")


def _delete_quietly(conf: dict, aid: str):
    try:
        call(conf, "DeleteAsset", {"Id": aid, "ProjectName": _project_name(conf)})
    except AssetsError:
        pass


def _group_id(conf: dict, config_path: Path) -> str:
    """素材组 Id:已记录的直接用,否则 CreateAssetGroup 自动创建并写回 genconfig(同 core._avatar_group_id)。"""
    av = conf.setdefault("avatar_assets", {})
    gid = str(av.get("group_id") or "").strip()
    if gid:
        return gid
    res = call(conf, "CreateAssetGroup",
               {"Name": str(av.get("group_name") or "").strip() or "VideoAgents",
                "Description": "VideoAgents character images (auto-created)",
                "ProjectName": _project_name(conf)})
    gid = str(res.get("Id") or "").strip()
    if not gid:
        raise AssetsError(f"CreateAssetGroup 未返回素材组 Id:{res}")
    full = _read_json(config_path)
    full.setdefault("avatar_assets", {})["group_id"] = gid
    _write_json(Path(config_path), full)
    av["group_id"] = gid
    return gid


# ---------------- 素材规格(CreateAsset 官方限制,82379/2318271 2026-09-24 版) ----------------

def probe_media(path) -> dict:
    """ffprobe 取首条视频流宽高、平均帧率与时长(PNG 同样可读,帧率/时长为 0)。"""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height,avg_frame_rate:format=duration",
                        "-of", "json", str(path)],
                       capture_output=True, text=True, check=True, timeout=60)
    d = json.loads(r.stdout or "{}")
    st = (d.get("streams") or [{}])[0]
    num, _, den = str(st.get("avg_frame_rate") or "0/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 0.
    try:
        dur = float((d.get("format") or {}).get("duration") or 0)
    except ValueError:
        dur = 0.
    return {"width": int(st.get("width") or 0), "height": int(st.get("height") or 0),
            "fps": fps, "duration": dur}


def spec_problem(path, kind: str, info: dict) -> str | None:
    """不合方舟素材规格的原因;合规返回 None。不合规的素材上传了也是审核 Failed,直接回退。"""
    p = Path(path)
    size = p.stat().st_size
    w, h = info.get("width") or 0, info.get("height") or 0
    if not (w and h):
        return "读不出宽高"
    ratio = w / h
    if kind == "Image":
        if size >= 30 * 1024 * 1024:
            return f"图片 {size / 1048576:.1f} MB,须小于 30 MB"
        if not (300 < w < 6000 and 300 < h < 6000):
            return f"尺寸 {w}x{h},宽高须在 (300, 6000) px"
        if not 0.4 < ratio < 2.5:
            return f"宽高比 {ratio:.2f},须在 (0.4, 2.5)"
        return None
    dur, fps = info.get("duration") or 0., info.get("fps") or 0.
    if p.suffix.lower() not in (".mp4", ".mov"):
        return f"格式 {p.suffix},仅支持 mp4/mov"
    if size > 200 * 1024 * 1024:
        return f"视频 {size / 1048576:.1f} MB,须不超过 200 MB"
    if not 2. <= dur <= 30.:
        return f"时长 {dur:.2f}s,须在 [2, 30] s"
    if not 23.99 <= fps <= 60.01:
        return f"帧率 {fps:.2f},须在 [24, 60]"
    if not (300 <= w <= 6000 and 300 <= h <= 6000):
        return f"尺寸 {w}x{h},宽高须在 [300, 6000] px"
    if not 0.4 <= ratio <= 2.5:
        return f"宽高比 {ratio:.2f},须在 [0.4, 2.5]"
    if not 407_696 <= w * h <= 8_295_044:
        return f"总像素 {w * h},须在 [407696, 8295044]"
    return None


# ---------------- 入库 + 等待 Active ----------------

def _set_entry(ledger_path: Path, dig: str, **fields):
    with _Ledger(ledger_path) as led:
        ent = led["assets"].setdefault(dig, {})
        ent.update(fields)


def _drop_entry(ledger_path: Path, dig: str):
    with _Ledger(ledger_path) as led:
        led["assets"].pop(dig, None)


def _replace_slot(conf: dict, ledger_path: Path, slot: str, dig: str, log):
    """同一 slot(同一前组同一类型)的旧素材已被新内容取代(前组重出):删旧资产、清台账,省素材额度。"""
    with _Ledger(ledger_path) as led:
        stale = {k: v for k, v in led["assets"].items()
                 if k != dig and v.get("kind") == "continuity" and v.get("slot") == slot}
        for k in stale:
            led["assets"].pop(k)
    for v in stale.values():
        if v.get("asset_id"):
            log(f"虚拟人像库:前组已重出,删除旧续接素材 {v['asset_id']}")
            _delete_quietly(conf, v["asset_id"])


def _wait_active(conf: dict, aid: str, dig: str, kind: str, label: str,
                 ledger_path: Path, log) -> str | None:
    deadline = time.time() + WAIT_S[kind]
    t0 = last_note = time.time()
    while True:
        status, err = _status(conf, aid)
        if status == "Active":
            _set_entry(ledger_path, dig, status="Active", checked_at=int(time.time()))
            log(f"虚拟人像库:{label} 审核通过({int(time.time() - t0)}s),以资产 URI 提交:asset://{aid}")
            return f"asset://{aid}"
        if status == "Failed":
            _fail(conf, aid, dig, label, err, ledger_path, log)
            return None
        if time.time() >= deadline:
            log(f"虚拟人像库:{label} 等待审核超时({WAIT_S[kind]}s 仍未 Active,{aid}),本次按原方式提交;"
                "下次提交会继续复核")
            return None
        if time.time() - last_note >= 30:
            last_note = time.time()
            log(f"虚拟人像库:{label} 审核中({int(last_note - t0)}s)…")
        time.sleep(POLL_S)


def _fail(conf: dict, aid: str, dig: str, label: str, err: str, ledger_path: Path, log):
    """审核 Failed:删掉失败资产(不占额度);内容/规格类失败台账记 Failed 不再重提,偶发故障清台账下次重提。"""
    _delete_quietly(conf, aid)
    code = err.split(":", 1)[0].strip()
    if code in _RETRYABLE_FAIL:
        _drop_entry(ledger_path, dig)
        log(f"虚拟人像库:{label} 入库处理失败({err}),本次按原方式提交,下次提交重新入库")
    else:
        _set_entry(ledger_path, dig, status="Failed", asset_id=None, error=err or "Failed",
                   checked_at=int(time.time()))
        log(f"虚拟人像库:{label} 入库审核未通过({err or 'Failed'}),按原方式提交;同一素材不再重提")


def ensure_asset(path: str, kind: str, conf: dict, config_path: Path, ledger_path: Path,
                 upload, log) -> str | None:
    """确保一份续接素材已入库且 Active,返回 asset://<id>;回退原提交方式时返回 None(原因已 log)。
    upload(path) → 公网 URL(genmedia._storage_upload_url,经「文件托管」对象存储预签名)。"""
    p = Path(path)
    base = p.resolve().parents[3]
    prev = p.name[:-len(TAIL_VIDEO if kind == "Video" else TAIL_IMAGE)]
    slot = f"{base.name}|{p.parent.name}|{prev}|{kind}"
    label = f"{_KIND_LABEL[kind]} {p.parent.name}/{p.name}"
    dig = hashlib.sha256(p.read_bytes()).hexdigest()
    with _Ledger(ledger_path) as led:
        ent = dict(led["assets"].get(dig) or {})
    if ent.get("status") == "Failed" and not ent.get("asset_id"):
        log(f"虚拟人像库:{label} 此前入库审核未通过({ent.get('error') or 'Failed'}),按原方式提交")
        return None
    aid = ent.get("asset_id")
    if aid:
        status, err = _status(conf, aid)
        if status == "Active":
            _set_entry(ledger_path, dig, status="Active", checked_at=int(time.time()))
            log(f"虚拟人像库:{label} 已入库(Active),以资产 URI 提交:asset://{aid}")
            return f"asset://{aid}"
        if status == "Processing":
            log(f"虚拟人像库:{label} 入库审核中({aid}),等待 Active…")
            return _wait_active(conf, aid, dig, kind, label, ledger_path, log)
        if status == "Failed":
            _fail(conf, aid, dig, label, err, ledger_path, log)
            return None
        if err.startswith("GetAsset 调用失败") and ent.get("status") == "Active":
            # 网络层失败查不了:按台账 Active 提交(资产若已不在,方舟建任务即 400 不计费)
            log(f"虚拟人像库:{label} 复核查询失败({err[:200]}),按台账 Active 以 asset://{aid} 提交")
            return f"asset://{aid}"
        # 查不到(库被全自动管理/手动清空)或查询报错:删旧(若还在)、丢弃记录、重新入库
        log(f"虚拟人像库:{label} 台账记录的资产 {aid} 不可用({err[:200]}),重新入库")
        _delete_quietly(conf, aid)
        _drop_entry(ledger_path, dig)
    problem = spec_problem(p, kind, probe_media(p))
    if problem:
        log(f"虚拟人像库:{label} 不合方舟素材规格({problem}),不入库,按原方式提交")
        return None
    url = upload(str(p))
    gid = _group_id(conf, config_path)
    _replace_slot(conf, ledger_path, slot, dig, log)
    name = f"{base.name}-{p.parent.name}-{p.name}"[:64]
    res = call(conf, "CreateAsset", {"GroupId": gid, "URL": url, "AssetType": kind,
                                     "Name": name, "ProjectName": _project_name(conf)})
    aid = str(res.get("Id") or "").strip()
    if not aid:
        raise AssetsError(f"CreateAsset 未返回素材 Id:{res}")
    _set_entry(ledger_path, dig, asset_id=aid, status="Processing", group_id=gid, name=name,
               source=str(p.resolve()), uploaded_at=int(time.time()), kind="continuity",
               asset_type=kind, slot=slot)
    log(f"虚拟人像库:{label} 已提交入库 {aid},等待审核 Active"
        + ("(视频素材处理较慢)…" if kind == "Video" else "…"))
    return _wait_active(conf, aid, dig, kind, label, ledger_path, log)


def continuity_asset_uri(path, *, config_path, ledger_path, upload, log) -> str | None:
    """一份参考素材若是长镜头续接素材且满足入库条件,确保入库 Active 并返回 asset://<id>;
    否则(非续接素材、资产库未启用、长镜头关闭)或入库未成返回 None,调用方照原方式提交。
    渠道/模型门槛(火山方舟 + Seedance 2.x)由调用方判断。"""
    kind = tail_kind(path)
    if not kind or not Path(path).is_file():
        return None
    conf = _read_json(config_path)
    if not (conf.get("avatar_assets") or {}).get("enabled"):
        return None
    if not _long_take_on(Path(path).resolve().parents[3]):
        return None
    try:
        return ensure_asset(str(path), kind, conf, Path(config_path), Path(ledger_path), upload, log)
    except Exception as e:  # noqa: BLE001 — 入库失败不阻断生成,回退原提交方式
        log(f"虚拟人像库:{_KIND_LABEL[kind]} {Path(path).name} 入库失败,按原方式提交:{e}")
        return None
