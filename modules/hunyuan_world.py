"""HunyuanWorld 世界模型(本地算力,2026-10-10):场景全景 → 远程 GPU 主机 worker(1.0 分层重建 +
多视图渲染 + 3DGS 训练)→ 与 Marble 完全相同的 world.json 契约落盘,viewer / 导演台 / 分镜截图链零改动。

架构:本模块是薄 HTTP 客户端;真正的 GPU 计算在 gpu_worker/(自包含,部署在 Windows/Linux GPU 主机,
见 docs/hunyuan_deploy.md)。调用形态对齐 Marble(modules/worldlabs.py):提交 → 轮询 → 下载 →
world/worlds/<key>/world.json + splats_<res>.spz + collider.glb + world_pano.jpg + thumbnail.jpg。

坐标与标定:worker 导出的 splats 与 Marble 同为 OpenCV 惯例(x 右、y 下、+z = 全景中心列,原点 = 全景
相机),直接套用 viewer 既有对齐变换;1.0 深度(MoGe)是相对尺度,标定口径与 Marble 相同——
scale_fix = 全景相机真实离地高(白模精确值)/ worker 拟合的 ground_plane_offset(原始单位),
残余误差用视窗 ⚙ 设置里的 yaw/尺度微调(同一条已打通的链路)。

配置:控制台「🎨 生成模型」→「🌍 世界模型」→ HunyuanWorld(远程)tab(genconfig.json#world):
provider 选 hunyuan;hunyuan.base_url(worker 地址)、hunyuan.token(X-Worker-Token)、
hunyuan.{fp8, views, gs_steps, max_gaussians, radius_m}(缺省见下)。环境变量 HUNYUAN_WORKER_URL /
HUNYUAN_WORKER_TOKEN 可覆盖。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from modules import worldlabs as wl
from modules.whitebox import read

PROVIDER = 'hunyuan'
MODEL_NAME = 'hunyuanworld-1.0+gsplat'
SCHEMA = wl.SCHEMA
POLL_INTERVAL_S = 10
POLL_TIMEOUT_S = 7200
RES_ORDER = ('full_res', '1000k', '500k', '150k', '100k')


class HunyuanWorldError(RuntimeError):
    pass


# ---------------------------------------------------------------- config / http
def hunyuan_config() -> dict:
    """生成模型配置世界模型段(world.hunyuan);环境变量优先。"""
    from modules.genmedia import CONFIG_PATH
    try:
        cfg = (json.loads(CONFIG_PATH.read_text(encoding='utf-8')).get('world') or {}).get(PROVIDER) or {}
    except Exception:  # noqa: BLE001
        cfg = {}
    if os.environ.get('HUNYUAN_WORKER_URL'):
        cfg['base_url'] = os.environ['HUNYUAN_WORKER_URL']
    if os.environ.get('HUNYUAN_WORKER_TOKEN'):
        cfg['token'] = os.environ['HUNYUAN_WORKER_TOKEN']
    return cfg


def base_url() -> str:
    return (hunyuan_config().get('base_url') or '').rstrip('/')


def _request(method: str, path: str, *, body: bytes | None = None, content_type: str | None = None,
             timeout: int = 120, base: str | None = None, token: str | None = None) -> dict:
    url = (base if base is not None else base_url()) + path
    if not url.startswith('http'):
        raise HunyuanWorldError('未配置 HunyuanWorld worker 地址:控制台「🎨 生成模型」→「🌍 世界模型」→ HunyuanWorld(远程)填 base_url')
    tok = token if token is not None else (hunyuan_config().get('token') or '')
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header('X-Worker-Token', tok)
    if content_type:
        req.add_header('Content-Type', content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode('utf-8')).get('detail') or ''
        except Exception:  # noqa: BLE001
            detail = ''
        raise HunyuanWorldError(f'{method} {path} → HTTP {e.code}{(": " + str(detail)) if detail else ""}') from e
    except urllib.error.URLError as e:
        raise HunyuanWorldError(f'{method} {url} 连接失败:{e.reason}') from e


def check_health(*, base: str | None = None, token: str | None = None, timeout: int = 15) -> dict:
    return _request('GET', '/health', timeout=timeout, base=base, token=token)


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = 'va' + uuid.uuid4().hex
    parts: list[bytes] = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (filename, data, ctype) in files.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{filename}"\r\n'
                     f'Content-Type: {ctype}\r\n\r\n'.encode() + data + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts), boundary


def download(job_id: str, name: str, target: Path, timeout: int = 600) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    tok = hunyuan_config().get('token') or ''
    req = urllib.request.Request(f'{base_url()}/worlds/{job_id}/artifacts/{name}')
    req.add_header('X-Worker-Token', tok)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(target, 'wb') as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    except urllib.error.URLError as e:
        raise HunyuanWorldError(f'下载 {name} 失败:{e}') from e
    return target


# ---------------------------------------------------------------- params
def gen_params(base: Path, sid: str, camera: dict, overrides: dict | None = None) -> dict:
    """任务参数:配置缺省 + 场景推断(indoor/outdoor)+ 锚点相机高(标定基准);overrides 优先(CLI 显式指定)。"""
    cfg = hunyuan_config()
    from modules import scene_panos as sp
    indoor = sp.is_indoor(base, sid)
    params = {
        'classes': str(cfg.get('classes') or ('indoor' if indoor else 'outdoor')),
        'fp8': bool(cfg.get('fp8', False)),
        'fp8_attention': bool(cfg.get('fp8_attention', False)),
        'views': int(cfg.get('views') or 32),
        'gs_steps': int(cfg.get('gs_steps') or 8000),
        'max_gaussians': int(cfg.get('max_gaussians') or 1000000),
        'radius_m': float(cfg.get('radius_m') or 0.35),
        'render_width': int(cfg.get('render_width') or 1600),   # 清晰度上限 = 训练视图分辨率
        'render_height': int(cfg.get('render_height') or 900),
        'camera_height_m': float(camera.get('height_m') or wl.CAMERA_HEIGHT_M),
    }
    params.update({k: v for k, v in (overrides or {}).items() if v is not None})
    return params


# ---------------------------------------------------------------- submit / poll / finish
def submit_world(base: Path, sid: str, *, anchor_id: str | None = None, scheme: str | None = None,
                 seed: int | None = None, smoke: bool = False, overrides: dict | None = None,
                 log=print, out: Path | None = None, key: str | None = None) -> dict:
    """准备全景(wl.prepare_pano 复用 scene_pano 链)→ 提交 worker → 轮询下载写 world.json。"""
    if out is None:
        key, out = wl.new_world_dir(base, sid)
    inp = wl.prepare_pano(base, sid, source='scene_pano', anchor_id=anchor_id, scheme=scheme, log=log, out=out)
    pano = out / 'pano.png'
    params = gen_params(base, sid, inp.get('camera') or {}, overrides)
    if smoke:
        params.update({'smoke': True, 'views': 4, 'gs_steps': 200})
    if seed is not None:
        params['seed'] = seed
    payload, boundary = _multipart({'params': json.dumps(params, ensure_ascii=False)},
                                   {'pano': ('pano.png', pano.read_bytes(), 'image/png')})
    log(f"提交 worker({base_url()}):全景 {pano.stat().st_size // 1024} KB,参数 {json.dumps(params, ensure_ascii=False)}…")
    resp = _request('POST', '/worlds', body=payload, content_type=f'multipart/form-data; boundary={boundary}', timeout=300)
    job_id = resp['job_id']
    wl._write_json(out / 'generate.json', {'job_id': job_id, 'submitted_at': wl._now(), 'params': params,
                                           'provider': PROVIDER})
    log(f'job {job_id},全景重建 + 3DGS 训练约 20–40 分钟,轮询中…')
    return finish_world(base, sid, job_id, log=log, out=out, key=key)


def poll_job(job_id: str, *, timeout_s: int = POLL_TIMEOUT_S, interval_s: int = POLL_INTERVAL_S, log=print) -> dict:
    """轮询任务到 done;log_tail 增量打印(对齐 wl.poll_operation 的推进日志),
    worker 的 progress(tqdm 进度条)变化时也实时打印当前阶段进度(2026-10-19)。"""
    t0 = time.time()
    seen = 0
    last_prog = None
    while True:
        st = _request('GET', f'/worlds/{job_id}', timeout=60)
        tail = st.get('log_tail') or []
        for line in tail[seen:]:
            log(f'  … {line}')
        seen = len(tail)
        prog = st.get('progress')
        if prog and prog != last_prog:
            log(f"  ⏳ [{st.get('stage') or '-'}] {prog}")
            last_prog = prog
        status = st.get('status')
        if status == 'done':
            return st
        if status in ('error', 'cancelled'):
            raise HunyuanWorldError(f"worker 任务 {status}:{st.get('error') or '见 worker 日志'}")
        if time.time() - t0 > timeout_s:
            raise HunyuanWorldError(f'worker 任务超时({timeout_s}s):job {job_id}(可 --resume {job_id} 续接)')
        time.sleep(interval_s)


def _files_from_artifacts(artifacts: dict) -> dict:
    def base(n):
        return Path(str(n)).name                   # worker 清单可能带子目录(gs/…),本地产物取文件名
    files: dict = {}
    splats = artifacts.get('splats') or {}
    if splats:
        files['splats'] = {res: base(name) for res, name in splats.items()}
    for k in ('collider', 'world_pano', 'thumbnail'):
        if artifacts.get(k):
            files[k] = base(artifacts[k])
    return files


def finish_world(base: Path, sid: str, job_id: str, *, log=print, out: Path | None = None,
                 key: str | None = None) -> dict:
    """轮询 job 结果并下载全部产物,写 <out>/world.json(Marble 契约)。--resume 续接走这里。"""
    if out is None:
        raise HunyuanWorldError('finish_world 须给 out(本次世界模型目录)')
    st = poll_job(job_id, log=log)
    artifacts = st.get('artifacts') or {}
    meta = st.get('pack_meta') or {}
    files = _files_from_artifacts(artifacts)
    if not (files.get('splats') or {}):
        raise HunyuanWorldError(f'任务完成但没有 splats 产物:{json.dumps(artifacts, ensure_ascii=False)[:500]}')
    for group in (st.get('artifacts') or {}).values():
        for name in (group.values() if isinstance(group, dict) else [group]):
            log(f'下载 {name} …')
            download(job_id, name, out / Path(str(name)).name)   # URL 用 worker 清单路径(可含 gs/ 子目录),落盘取文件名
    inp = read(out / 'input.json', None) or {}
    gen_rec = read(out / 'generate.json', None) or {}
    cam = inp.get('camera') or {}
    gpo = meta.get('gpo_raw')
    record = {
        'schema_version': SCHEMA, 'scene_id': sid, 'key': key, 'written_at': wl._now(),
        'provider': PROVIDER,
        'world_id': f'hy-{job_id}', 'display_name': ' '.join(x for x in (base.name, sid, key) if x),
        'model': MODEL_NAME, 'world_marble_url': None, 'caption': None,
        'semantics_metadata': {'metric_scale_factor': 1.0, 'ground_plane_offset': gpo},
        'files': files,
        'alignment': {
            'convention': 'hunyuan_raw_opencv(=marble raw:x 右,y 下,+z=全景中心列) → ×metric_scale_factor×scale_fix → rotX(180°) → rotY(yaw) + camera',
            'camera': cam, 'yaw_deg': cam.get('yaw_deg', 0.0),
            'metric_scale_factor': 1.0, 'ground_plane_offset': gpo,
            # 与 Marble 同口径:全景相机真实离地高 ÷ worker 拟合的原始单位相机高 = 米制修正
            'scale_fix': round(cam['height_m'] / gpo, 4) if cam.get('height_m') and gpo else 1.0,
            'scale_fix_basis': 'camera_height_m / ground_plane_offset', 'yaw_fix_deg': 0.0,
        },
        'input': inp,
        'steps': {'generate': {**gen_rec, 'job_id': job_id, 'pack_meta': meta, 'finished_at': wl._now()}},
    }
    wl._write_json(out / 'world.json', record)
    log(f'saved: {out / "world.json"}  world_id={record["world_id"]}')
    log(f'尺度自检:全景相机离地 {cam.get("height_m")} m,ground_plane_offset={gpo}(原始单位),scale_fix={record["alignment"]["scale_fix"]}')
    return record
