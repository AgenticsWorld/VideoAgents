"""画板(2026-10-10,docs/image_canvas.md):对一张已有图片做圈选编辑 / 放大 / 裁剪翻转旋转缩放,保留全部历史版本,选一张作最终版。

入口:场景预览(场景图、分镜背景图)、人物预览(人物图、服装图)、生物 / 道具预览、故事板草图、成片发布页封面,各自「✏️ 修改」后的
「🖌 画板」按钮 → /preview/canvas?project=&file=<项目内相对路径>。

存放:assets/canvas/<原图相对路径压平>.<sha8>/
    history.json            版本台账(schema image_canvas/1.0)+ 参考图托盘 + 最近任务号
    v000.png …              各版本(扩展名随原图;内容按文件头,本库 .png 多为 JPEG 字节,见 code/image_edit.py)
    v003.raw.png            圈外锁定前模型返回的整图(「改用整图」时取它)
    v003.thumb.jpg          历史列表缩略图
    refs/                   用户上传的参考图
    jobs/<任务号>/          job.json + source / marked.jpg(带圈标注图)/ mask.png / region_N.jpg(各区域裁切,给 Agent 看)
不放在资产目录里:预览页递归列出 assets/concepts/<类>/<id>/ 下所有图片,历史版本放进去会混进页面与参考图检查。

要点:
- 编辑、放大的结果只进历史;「设为最终版」(adopt)才动生产链路——固定路径的图(人物 / 服装 / 生物 / 道具 / 场景图 / 草图 / 封面)
  把该版本写回原路径;分镜背景图(下游按库 key 引用)登记为 <key>_revN 并把引用原图的分镜改指过去(modules/shot_plates.adopt_canvas_plate)。
- 第一次打开把当前图存为 v000;之后原路径上的图被别处改过(Agent 重出、页面裁剪翻转),打开时自动收为一个「外部更新」版本。
- 圈选区域由页面传几何(归一化坐标),蒙版与带圈标注图在这里用 PIL 画,不信任页面像素。
- 圈外锁定:出图后只取圈内(边缘羽化)贴回原图,圈外像素保持不变;圈外漂移过大(模型把整张图挪了位置)时记 drift,页面提示可改用整图。
- 同一张图同时只跑一个任务(active_job);写盘统一按原图编码(JPEG 字节配 .png 名),不落 4 MB 的真 PNG。
"""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import time
from pathlib import Path

SCHEMA = 'image_canvas/1.0'
ROOT_REL = 'assets/canvas'
IMG_EXTS = ('.png', '.jpg', '.jpeg', '.webp')
KINDS = ('scene', 'plate', 'character', 'costume', 'creature', 'prop', 'sketch', 'cover', 'image')
ACTIVE = ('queued', 'drafting', 'awaiting_confirm', 'running')
STALE_S = {'queued': 15 * 60, 'drafting': 45 * 60, 'running': 30 * 60, 'awaiting_confirm': 24 * 3600}
MAX_REGIONS = 12
MAX_REFS = 8                      # 一次编辑带的用户参考图上限(原图与标注图另算)
MAX_TEXT = 2000
MAX_EDGE = 8192                   # 手动缩放 / 放大目标的单边上限
JPEG_QUALITY = 95
PNG_MAX_BYTES = 2 * 1024 * 1024   # 无透明的 PNG 超过这个体积改存 JPEG 字节(方舟内联参考图上限,见 genmedia REF_INLINE_MAX_BYTES)
THUMB_EDGE = 360
MARKED_EDGE = 2048
DRIFT_WARN = 0.06                 # 圈外平均差(0..1)超过它提示「模型改动了圈外」;启发式,见 docs
REGION_COLORS = ('#ff3b30', '#34c759', '#0a84ff', '#ffd60a', '#bf5af2', '#ff9f0a', '#64d2ff', '#ff375f')
MENTION_RE = re.compile(r'@\s*(?:图|ref|img|image)\s*(\d+)', re.I)
UPSCALE_PROMPT = ('Reproduce [Image 1] exactly at a higher resolution: the same composition, framing, subjects, faces, text, '
                  'colours and lighting. Add only fine detail and sharpness that is consistent with the original; do not add, '
                  'remove, move or restyle anything.')
# 设定卡归属:设为最终版时可勾选「把这次改动发给修改师更新设定」的类别 → 修改单 kind(core.REVISION_KIND_AGENTS)
SETTING_KINDS = {'character': 'character', 'costume': 'costume', 'scene': 'scene', 'plate': 'scene',
                 'creature': 'creature', 'prop': 'prop'}
# 各类别默认沿用哪一栏的图像模型选择(预览页顶栏,state.json image_model_prefs);封面等没有对应栏的跟随全局
PREF_KINDS = {'scene': 'scenes', 'plate': 'scenes', 'character': 'characters', 'costume': 'characters',
              'creature': 'creatures', 'prop': 'props', 'sketch': 'sketch'}


class CanvasError(Exception):
    """用户可见的错误:message 中文,en 英文(宿主按界面语言取:中文界面出中文,其余一律英文)。"""

    def __init__(self, message: str, status: int = 400, en: str = ''):
        super().__init__(message)
        self.status = status
        self.en = en

    def text(self, zh: bool = True) -> str:
        return str(self) if zh or not self.en else self.en


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec='seconds')


# ---------------------------------------------------------------- 图片读写(编码约定同 code/image_edit.py)
def encoding_of(data: bytes) -> str:
    return 'JPEG' if data[:3] == b'\xff\xd8\xff' else 'PNG' if data[:8] == b'\x89PNG\r\n\x1a\n' else 'WEBP' if data[:4] == b'RIFF' else 'OTHER'


def _pil():
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        raise CanvasError('缺少 Pillow,无法处理图片:pip install Pillow', 501, en='Pillow is missing; cannot process images: pip install Pillow') from None
    return Image


def has_alpha(im) -> bool:
    return im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info)


def load_image(data: bytes):
    Image = _pil()
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def encode_image(im, like: str = 'JPEG') -> bytes:
    """按原图编码写:带透明通道的保持 PNG;原图是 PNG 且结果不大的仍存 PNG;其余存 JPEG 字节(扩展名由调用方定,不随编码变)。"""
    from PIL import ImageFile
    buf = io.BytesIO()
    if has_alpha(im):
        im.save(buf, 'PNG')
        return buf.getvalue()
    if like == 'PNG':
        im.save(buf, 'PNG')
        if buf.tell() <= PNG_MAX_BYTES:
            return buf.getvalue()
        buf = io.BytesIO()
    if like == 'WEBP':
        im.save(buf, 'WEBP', quality=JPEG_QUALITY)
        return buf.getvalue()
    ImageFile.MAXBLOCK = max(ImageFile.MAXBLOCK, im.size[0] * im.size[1] * 4)   # optimize=True 对高熵大图会撑爆默认缓冲
    im.convert('RGB').save(buf, 'JPEG', quality=JPEG_QUALITY, optimize=True, subsampling=0)
    return buf.getvalue()


def file_info(path: Path) -> dict:
    """分辨率 / 体积 / 实际编码 / 创建时间 / 修改时间(创建时间取文件系统 birthtime,没有的平台退回 ctime 与 mtime 里较早的)。"""
    data = path.read_bytes()
    st = path.stat()
    im = load_image(data)
    born = getattr(st, 'st_birthtime', None) or min(st.st_ctime, st.st_mtime)
    return {'width': im.size[0], 'height': im.size[1], 'bytes': len(data), 'encoding': encoding_of(data),
            'alpha': has_alpha(im), 'created': int(born), 'modified': int(st.st_mtime),
            'sha256': hashlib.sha256(data).hexdigest()}


# ---------------------------------------------------------------- 定位
def safe_rel(base: Path, rel: str) -> str:
    """项目内相对路径 → 规范化;越出项目目录、不是图片、指向画板自己的目录都拒收。"""
    rel = str(rel or '').replace('\\', '/').strip().lstrip('/')
    if not rel or '\0' in rel:
        raise CanvasError('缺少图片路径(file)', en='Missing image path (file)')
    base_r = base.resolve()
    p = (base_r / rel).resolve()
    try:
        norm = p.relative_to(base_r).as_posix()
    except ValueError:
        raise CanvasError('图片路径越出项目目录', en='The image path is outside the project directory') from None
    if norm.startswith(ROOT_REL + '/'):
        raise CanvasError('不能对画板历史目录里的文件开画板', en='Files inside the canvas history folder cannot be opened in the canvas')
    if p.suffix.lower() not in IMG_EXTS:
        raise CanvasError(f'不支持的图片格式:{p.suffix or "(无扩展名)"}', en=f'Unsupported image format: {p.suffix or "(no extension)"}')
    if not p.is_file():
        raise CanvasError(f'图片不存在:{norm}', 404, en=f'Image not found: {norm}')
    return norm


_PLATE_RE = re.compile(r'^assets/concepts/scenes/([^/]+)/plates/([^/]+)\.(?:png|jpe?g|webp)$', re.I)
_CONCEPT_RE = re.compile(r'^assets/concepts/(scenes|characters|creatures|props)/([^/]+)/')
_SKETCH_RE = re.compile(r'^assets/storyboard/([^/]+)/([^/_][^/]*)\.(?:png|jpe?g|webp)$', re.I)
_COVER_RE = re.compile(r'^edit/([^/]+)/(?:[^/]+/)*[^/]*thumb[^/]*\.(?:png|jpe?g|webp)$', re.I)   # 成片发布页的封面:edit/<ep>/ 下文件名带 thumb 的图
_CONCEPT_KINDS = {'scenes': 'scene', 'characters': 'character', 'creatures': 'creature', 'props': 'prop'}


def locate(rel: str, kind: str = '') -> dict:
    """路径 → {storage: plate|sketch|fixed, kind, sid?, key?, ep?, id?}。
    storage 只按路径判(决定最终版怎么落);kind 是给页面显示与设定归属用的类别,页面可传 costume / cover 细化。"""
    m = _PLATE_RE.match(rel)
    if m and not m.group(2).endswith(('.pano', '.whitebox', '.plan', '.template', '.orig')):
        return {'storage': 'plate', 'kind': 'plate', 'sid': m.group(1), 'key': m.group(2), 'id': m.group(1)}
    m = _SKETCH_RE.match(rel)
    if m:
        return {'storage': 'sketch', 'kind': 'sketch', 'ep': m.group(1), 'key': m.group(2)}
    m = _COVER_RE.match(rel)
    if m:
        return {'storage': 'fixed', 'kind': 'cover', 'ep': m.group(1)}
    m = _CONCEPT_RE.match(rel)
    if m:
        k = _CONCEPT_KINDS[m.group(1)]
        if k == 'character' and kind == 'costume':
            k = 'costume'
        return {'storage': 'fixed', 'kind': k, 'id': m.group(2)}
    return {'storage': 'fixed', 'kind': kind if kind in KINDS and kind not in ('plate', 'sketch') else 'image'}


def canvas_root(base: Path) -> Path:
    return base / ROOT_REL


def doc_name(rel: str) -> str:
    flat = re.sub(r'[^\w\-.]+', '_', rel.replace('/', '__'))[-110:]
    return f"{flat}.{hashlib.sha1(rel.encode('utf-8')).hexdigest()[:8]}"


def _aliases(base: Path) -> dict:
    try:
        d = json.loads((canvas_root(base) / 'index.json').read_text(encoding='utf-8'))
        return d.get('aliases') or {}
    except Exception:  # noqa: BLE001
        return {}


def _set_alias(base: Path, rel: str, root_rel: str) -> None:
    """分镜背景图设为最终版后新登记的 <key>_revN 文件 → 记到原图的画板,之后从 _revN 打开看到的是同一份历史。"""
    root = canvas_root(base)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / 'index.lock', 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        al = _aliases(base)
        al[rel] = root_rel
        tmp = root / 'index.json.tmp'
        tmp.write_text(json.dumps({'schema': SCHEMA, 'aliases': al}, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(tmp, root / 'index.json')


def doc_dir_of(base: Path, rel: str) -> Path:
    return canvas_root(base) / doc_name(rel)


# ---------------------------------------------------------------- 台账
@contextlib.contextmanager
def _locked(doc: Path):
    doc.mkdir(parents=True, exist_ok=True)
    with open(doc / 'history.lock', 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def load_history(doc: Path) -> dict:
    try:
        h = json.loads((doc / 'history.json').read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        h = {}
    if not isinstance(h, dict) or h.get('schema') != SCHEMA:
        h = {'schema': SCHEMA, 'versions': [], 'refs': [], 'jobs': [], 'ref_seq': 0, 'final_log': []}
    for k, v in (('versions', []), ('refs', []), ('jobs', []), ('final_log', [])):
        if not isinstance(h.get(k), list):
            h[k] = v
    return h


def _save_history(doc: Path, h: dict) -> None:
    h['updated_at'] = now_iso()
    tmp = doc / 'history.json.tmp'
    tmp.write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, doc / 'history.json')


def mutate(doc: Path, fn):
    """加锁读-改-写台账(宿主线程与 Agent 起的 CLI 进程可并发);fn(hist) 的返回值原样返回。"""
    with _locked(doc):
        h = load_history(doc)
        before = json.dumps(h, ensure_ascii=False, sort_keys=True)
        out = fn(h)
        if json.dumps(h, ensure_ascii=False, sort_keys=True) != before:   # 页面轮询每次都会走到这里:没变就不重写文件
            _save_history(doc, h)
        return out


def version_of(h: dict, vid: str, deleted: bool = False) -> dict:
    v = next((x for x in h['versions'] if x.get('id') == vid and (deleted or not x.get('deleted'))), None)
    if not v:
        raise CanvasError(f'没有这个版本:{vid or "(空)"}', 404, en=f'No such version: {vid or "(empty)"}')
    return v


def _next_vid(h: dict) -> str:
    n = max([int(v['id'][1:]) for v in h['versions'] if re.fullmatch(r'v\d+', str(v.get('id') or ''))] + [-1])
    return f'v{n + 1:03d}'


def _write_thumb(doc: Path, vid: str, im) -> str:
    Image = _pil()
    t = im.convert('RGBA' if has_alpha(im) else 'RGB')
    t.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.LANCZOS)
    if t.mode == 'RGBA':
        bg = Image.new('RGB', t.size, (255, 255, 255))
        bg.paste(t, mask=t.split()[-1])
        t = bg
    name = f'{vid}.thumb.jpg'
    t.save(doc / name, 'JPEG', quality=82)
    return name


def _add_version(doc: Path, h: dict, data: bytes, *, op: str, parent: str | None, **meta) -> dict:
    """写一个新版本文件并登记(调用方须在 mutate 里)。data 是最终要落盘的字节。"""
    im = load_image(data)
    vid = _next_vid(h)
    suffix = Path(h.get('source') or 'x.png').suffix.lower() or '.png'
    name = f'{vid}{suffix}'
    (doc / name).write_bytes(data)
    rec = {'id': vid, 'file': name, 'thumb': _write_thumb(doc, vid, im), 'op': op, 'parent': parent, 'created_at': now_iso(),
           'width': im.size[0], 'height': im.size[1], 'bytes': len(data), 'encoding': encoding_of(data),
           'sha256': hashlib.sha256(data).hexdigest()}
    rec.update({k: v for k, v in meta.items() if v not in (None, '', [], {})})
    h['versions'].append(rec)
    return rec


def open_doc(base: Path, rel: str, kind: str = '', create: bool = True) -> tuple[Path, dict]:
    """打开(必要时新建)一张图的画板:首次把当前图存为 v000;原路径上的图与所有版本都对不上时收为「外部更新」版本。
    返回 (画板目录, 台账)。rel 是 <key>_revN 这类由画板登记出去的文件时,回到原图的那份画板。"""
    rel = safe_rel(base, rel)
    opened = rel
    rel = _aliases(base).get(rel, rel)
    if rel != opened and not (base / rel).is_file():
        rel = opened                                   # 原图已不在:就地另起一份
    doc = doc_dir_of(base, rel)
    if not (doc / 'history.json').is_file() and not create:
        raise CanvasError('这张图还没有画板记录', 404, en='This image has no canvas record yet')

    def _sync(h: dict):
        loc = locate(rel, kind or h.get('kind') or '')   # 页面没带类别(如 CLI)时沿用上次记下的(服装 / 封面这类路径判不出的细分)
        if not h.get('source'):
            h.update({'source': rel, 'created_at': now_iso()})
        h.update({'storage': loc['storage'], 'kind': loc['kind'],
                  'meta': {k: v for k, v in loc.items() if k not in ('storage', 'kind')}})
        data = (base / rel).read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        hit = next((v for v in h['versions'] if v.get('sha256') == sha and not v.get('deleted')), None)
        if hit is None:
            first = not h['versions']
            hit = _add_version(doc, h, data, op='original' if first else 'external', parent=None if first else h.get('disk'),
                               source_mtime=int((base / rel).stat().st_mtime))
        h['disk'] = hit['id']
        if h['storage'] != 'plate' or not h.get('final'):
            h['final'] = hit['id']                     # 固定路径的图:原路径上的那一版就是最终版
        return h

    h = mutate(doc, _sync)
    if opened != rel:
        h['opened'] = opened
    return doc, h


# ---------------------------------------------------------------- 圈选区域 → 蒙版 / 带圈标注图
def _clamp01(v) -> float:
    try:
        return min(1.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def norm_regions(raw) -> list[dict]:
    """页面传来的区域清单 → 校验后的 [{n, shape, x, y, w, h | points, width, text}](坐标全是 0..1 归一化)。"""
    out = []
    for r in (raw if isinstance(raw, list) else [])[:MAX_REGIONS]:
        if not isinstance(r, dict):
            continue
        shape = str(r.get('shape') or '')
        rec = {'n': len(out) + 1, 'shape': shape, 'text': str(r.get('text') or '').strip()[:MAX_TEXT]}
        if shape in ('rect', 'ellipse'):
            x, y, w, h = (_clamp01(r.get(k)) for k in ('x', 'y', 'w', 'h'))
            w, h = min(w, 1 - x), min(h, 1 - y)
            if w < 0.004 or h < 0.004:
                continue
            rec.update({'x': x, 'y': y, 'w': w, 'h': h})
        elif shape in ('lasso', 'brush'):
            pts = [[_clamp01(p[0]), _clamp01(p[1])] for p in (r.get('points') or [])
                   if isinstance(p, (list, tuple)) and len(p) >= 2][:4000]
            if len(pts) < (3 if shape == 'lasso' else 1):
                continue
            rec['points'] = pts
            if shape == 'brush':
                rec['width'] = min(0.3, max(0.004, float(r.get('width') or 0.03)))
        else:
            continue
        out.append(rec)
    return out


def region_bbox(r: dict) -> tuple[float, float, float, float]:
    if r['shape'] in ('rect', 'ellipse'):
        return r['x'], r['y'], r['x'] + r['w'], r['y'] + r['h']
    xs, ys = [p[0] for p in r['points']], [p[1] for p in r['points']]
    pad = r.get('width', 0) / 2 if r['shape'] == 'brush' else 0
    return max(0.0, min(xs) - pad), max(0.0, min(ys) - pad), min(1.0, max(xs) + pad), min(1.0, max(ys) + pad)


def region_where(r: dict) -> str:
    """区域位置的文字说法(写进提示词,模型看不清圈时兜底):upper left, about x 12–38%, y 20–55% of the frame。"""
    x0, y0, x1, y1 = region_bbox(r)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    col = 'left' if cx < 0.36 else 'right' if cx > 0.64 else 'centre'
    row = 'upper' if cy < 0.36 else 'lower' if cy > 0.64 else 'middle'
    where = 'centre' if (row, col) == ('middle', 'centre') else f'{row} {col}'
    return f'{where} of the frame, about x {round(x0 * 100)}–{round(x1 * 100)}%, y {round(y0 * 100)}–{round(y1 * 100)}%'


def _draw_region(draw, r: dict, size: tuple[int, int], fill: int, grow: float = 0.0) -> None:
    """把一个区域画进 L 蒙版;grow(像素)把形状向外扩一圈(画描边的外沿用)。"""
    W, H = size
    if r['shape'] in ('rect', 'ellipse'):
        box = [r['x'] * W - grow, r['y'] * H - grow, (r['x'] + r['w']) * W + grow, (r['y'] + r['h']) * H + grow]
        (draw.rectangle if r['shape'] == 'rect' else draw.ellipse)(box, fill=fill)
        return
    pts = [(p[0] * W, p[1] * H) for p in r['points']]
    if r['shape'] == 'lasso':
        draw.polygon(pts, fill=fill)
        if grow:
            draw.line(pts + [pts[0]], fill=fill, width=max(1, int(grow * 2)), joint='curve')
        return
    width = max(2, int(r.get('width', 0.03) * min(W, H) + grow * 2))
    if len(pts) > 1:
        draw.line(pts, fill=fill, width=width, joint='curve')
    rad = width / 2
    for x, y in (pts[0], pts[-1]) if len(pts) > 1 else pts:
        draw.ellipse([x - rad, y - rad, x + rad, y + rad], fill=fill)


def render_mask(size: tuple[int, int], regions: list[dict]):
    """区域并集的 L 蒙版(255 = 可改)。"""
    from PIL import Image, ImageDraw
    m = Image.new('L', size, 0)
    d = ImageDraw.Draw(m)
    for r in regions:
        _draw_region(d, r, size, 255)
    return m


def _font(px: int):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=px)
    except TypeError:  # pragma: no cover  Pillow < 10.1
        return ImageFont.load_default()


def render_marked(im, regions: list[dict]):
    """带圈标注图:原图上按区域画彩色描边(只描边、不盖住圈内内容)+ 编号圆标。长边压到 MARKED_EDGE。"""
    from PIL import Image, ImageChops, ImageDraw
    out = im.convert('RGB').copy()
    if max(out.size) > MARKED_EDGE:
        out.thumbnail((MARKED_EDGE, MARKED_EDGE), Image.LANCZOS)
    W, H = out.size
    lw = max(3, min(W, H) // 220)
    for r in regions:
        color = REGION_COLORS[(r['n'] - 1) % len(REGION_COLORS)]
        inner, outer = Image.new('L', (W, H), 0), Image.new('L', (W, H), 0)
        _draw_region(ImageDraw.Draw(inner), r, (W, H), 255)
        _draw_region(ImageDraw.Draw(outer), r, (W, H), 255, grow=lw)
        out.paste(color, mask=ImageChops.subtract(outer, inner))
    d = ImageDraw.Draw(out)
    rad = max(13, min(W, H) // 42)
    font = _font(int(rad * 1.25))
    for r in regions:
        color = REGION_COLORS[(r['n'] - 1) % len(REGION_COLORS)]
        x0, y0, _, _ = region_bbox(r)
        cx = min(max(x0 * W, rad + 2), W - rad - 2)
        cy = min(max(y0 * H, rad + 2), H - rad - 2)
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=color, outline='white', width=2)
        try:
            d.text((cx, cy), str(r['n']), fill='white', font=font, anchor='mm', stroke_width=1, stroke_fill='black')
        except (ValueError, TypeError):  # pragma: no cover  旧版 Pillow 的位图默认字体不支持 anchor
            d.text((cx - rad / 3, cy - rad / 2), str(r['n']), fill='white', font=font)
    return out


def lock_outside(src, out, regions: list[dict]):
    """圈外锁定:模型返回的整图只取圈内(边缘羽化)贴回原图。返回 (合成图, drift, applied)。
    drift = 圈外(扩了一圈之后)原图与模型返回图的平均差(0..1);返回图与原图画幅差超过 3% 时无法对位,不合成(applied=False)。"""
    from PIL import Image, ImageChops, ImageFilter, ImageStat
    W, H = src.size
    if abs(out.size[0] / out.size[1] - W / H) / (W / H) > 0.03:
        return out, None, False
    res = out.convert('RGB')
    if res.size != (W, H):
        res = res.resize((W, H), Image.LANCZOS)
    mask = render_mask((W, H), regions)
    # 蒙版在缩小的图上扩边 + 羽化再放回原尺寸:4K 图上直接跑 MaxFilter / GaussianBlur 要好几秒
    sw = min(1024, W)
    small = mask.resize((sw, max(1, round(H * sw / W))), Image.BILINEAR)
    k = max(3, (min(small.size) // 60) | 1)
    grown = small.filter(ImageFilter.MaxFilter(k))
    soft = grown.filter(ImageFilter.GaussianBlur(max(1.5, min(small.size) / 140))).resize((W, H), Image.BILINEAR)
    base = src.convert('RGB')
    comp = Image.composite(res, base, soft)
    outside = ImageChops.invert(grown.filter(ImageFilter.MaxFilter(k)))
    drift = None
    if ImageStat.Stat(outside).sum[0] > 0:
        diff = ImageChops.difference(base.resize(small.size, Image.BILINEAR).convert('L'),
                                     res.resize(small.size, Image.BILINEAR).convert('L'))
        drift = round(ImageStat.Stat(diff, mask=outside.point(lambda v: 255 if v > 127 else 0)).mean[0] / 255, 4)
    if has_alpha(src):
        comp = comp.convert('RGBA')
        comp.putalpha(src.convert('RGBA').split()[-1])
    return comp, drift, True


# ---------------------------------------------------------------- 参考图托盘
def add_ref(base: Path, doc: Path, *, data: bytes | None = None, filename: str = '', library_rel: str = '', note: str = '') -> dict:
    """上传一张参考图(data)或从项目库里选一张(library_rel,不复制,只记路径)进托盘。返回 {n, file, name, note, source}。"""
    if data is not None:
        if len(data) > 30 * 1024 * 1024:
            raise CanvasError('参考图超过 30 MB', en='The reference image is larger than 30 MB')
        if encoding_of(data) == 'OTHER':
            raise CanvasError('参考图不是 PNG / JPEG / WebP', en='The reference image is not PNG / JPEG / WebP')
        load_image(data)

    def _do(h: dict):
        if sum(1 for r in h['refs'] if not r.get('removed')) >= 24:
            raise CanvasError('参考图托盘已满(24 张),先删掉不用的', en='The reference tray is full (24 images); remove the ones you no longer need')
        n = int(h.get('ref_seq') or 0) + 1
        h['ref_seq'] = n
        if data is not None:
            stem = re.sub(r'[^\w\-.]+', '_', Path(filename or 'ref.png').name)[-60:] or 'ref.png'
            if Path(stem).suffix.lower() not in IMG_EXTS:
                stem += '.png'
            (doc / 'refs').mkdir(exist_ok=True)
            out = doc / 'refs' / f'{n:02d}_{stem}'
            out.write_bytes(data)
            rel, source, name = out.relative_to(base).as_posix(), 'upload', Path(filename or stem).name
        else:
            rel = safe_rel(base, library_rel)
            source, name = 'library', rel
        rec = {'n': n, 'file': rel, 'name': name, 'note': str(note or '').strip()[:300], 'source': source, 'added_at': now_iso()}
        h['refs'].append(rec)
        return rec
    return mutate(doc, _do)


def update_ref(doc: Path, n: int, *, note: str | None = None, remove: bool = False) -> dict:
    def _do(h: dict):
        rec = next((r for r in h['refs'] if r.get('n') == int(n) and not r.get('removed')), None)
        if not rec:
            raise CanvasError(f'没有这张参考图:{n}', 404, en=f'No such reference image: {n}')
        if remove:
            rec['removed'] = True      # 编号不回收:历史任务里的 @图N 仍然指得清
        elif note is not None:
            rec['note'] = str(note).strip()[:300]
        return rec
    return mutate(doc, _do)


def library_images(base: Path, query: str = '', limit: int = 300) -> list[dict]:
    """「从项目库选」的候选:人物 / 生物 / 道具 / 场景概念图(不含 candidates 等子目录与全景、世界模型)+ 分镜背景图 + refs/ 里的图片。"""
    q = (query or '').strip().lower()
    groups = []
    for sub, label in (('characters', 'characters'), ('creatures', 'creatures'), ('props', 'props'), ('scenes', 'scenes')):
        d = base / 'assets' / 'concepts' / sub
        items = []
        for ent in (sorted(p for p in d.iterdir() if p.is_dir()) if d.is_dir() else []):
            files = sorted(f for f in ent.iterdir() if f.is_file() and f.suffix.lower() in IMG_EXTS)
            if sub == 'scenes' and (ent / 'plates').is_dir():
                files += sorted(f for f in (ent / 'plates').iterdir() if f.is_file() and f.suffix.lower() == '.png')
            for f in files:
                rel = f.relative_to(base).as_posix()
                if not q or q in rel.lower():
                    items.append({'file': rel, 'name': f'{ent.name}/{f.relative_to(ent).as_posix()}'})
        if items:
            groups.append({'group': label, 'items': items})
    rd = base / 'refs'
    items = [{'file': f.relative_to(base).as_posix(), 'name': f.relative_to(rd).as_posix()}
             for f in (sorted(rd.rglob('*')) if rd.is_dir() else [])
             if f.is_file() and f.suffix.lower() in IMG_EXTS and (not q or q in f.as_posix().lower())]
    if items:
        groups.append({'group': 'refs', 'items': items})
    left = limit
    for g in groups:
        g['total'] = len(g['items'])
        g['items'] = g['items'][:max(0, left)]
        left -= len(g['items'])
    return [g for g in groups if g['items']]


# ---------------------------------------------------------------- 任务
def _job_dir(doc: Path, jid: str) -> Path:
    if not re.fullmatch(r'j[0-9a-z\-]{6,40}', jid or ''):
        raise CanvasError('任务号不合法', en='Invalid job id')
    return doc / 'jobs' / jid


def load_job(doc: Path, jid: str) -> dict:
    try:
        j = json.loads((_job_dir(doc, jid) / 'job.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise CanvasError(f'没有这个任务:{jid}', 404, en=f'No such job: {jid}') from None
    return j


def save_job(doc: Path, job: dict) -> dict:
    job['updated_at'] = now_iso()
    job['updated_ts'] = time.time()
    d = _job_dir(doc, job['id'])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / 'job.json.tmp'
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, d / 'job.json')
    return job


def set_job(doc: Path, jid: str, **patch) -> dict:
    job = load_job(doc, jid)
    job.update(patch)
    return save_job(doc, job)


def recent_jobs(doc: Path, h: dict, n: int = 6) -> list[dict]:
    out = []
    for jid in list(h.get('jobs') or [])[-n:]:
        try:
            out.append(load_job(doc, jid))
        except CanvasError:
            continue
    return out


def active_job(doc: Path, h: dict, run_ended=None) -> dict | None:
    """这张图上还在跑的任务(同一张图同时只跑一个)。顺手收拾已经死掉的:长时间没更新的,以及 Agent 已结束却没交提示词的
    (run_ended(run_id) → None 未结束 / 结束原因文本,由宿主按运行记录判断)。"""
    for job in reversed(recent_jobs(doc, h, 4)):
        st = job.get('status')
        if st not in ACTIVE:
            continue
        age = time.time() - float(job.get('updated_ts') or 0)
        if st == 'drafting' and run_ended and job.get('run_id'):
            why = run_ended(job['run_id'])
            if why is not None:
                set_job(doc, job['id'], status='failed', error=f'Agent 已结束但没有提交提示词:{why}'[:500],
                        error_en=f'The agent finished without submitting a prompt: {why}'[:500])
                continue
        if age > STALE_S.get(st, 1800):
            waiting = st == 'awaiting_confirm'
            set_job(doc, job['id'], status='cancelled' if waiting else 'failed',
                    error='待确认的提示词超过一天未确认,已取消' if waiting else '任务长时间没有进展,已按失败处理',
                    error_en='The prompt was not confirmed within a day; cancelled' if waiting else 'The job made no progress for a long time; marked as failed')
            continue
        return job
    return None


def _new_job(doc: Path, h: dict, payload: dict) -> dict:
    jid = f"j{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(2).hex()}"
    job = {'id': jid, 'created_at': now_iso(), 'source': h['source'], 'doc': doc.name, **payload}
    save_job(doc, job)
    h['jobs'] = (h.get('jobs') or [])[-40:] + [jid]
    return job


def resolve_mentions(text: str, image_no: dict[int, int]) -> str:
    """@图1 / @ref1 → [Image N](托盘里没有或本次没带的参考图保留原文,模型至少能看到用户写了什么)。"""
    return MENTION_RE.sub(lambda m: f'[Image {image_no[int(m.group(1))]}]' if int(m.group(1)) in image_no else m.group(0), text or '')


def compose_prompt(job: dict) -> str:
    """直接发送用的提示词,也是给 Agent 的骨架:图序固定 [Image 1] 原图、[Image 2] 带圈标注图(有圈选时)、其后是参考图;
    用户文字里的 @图N 按 job.images_by_ref(参考图编号 → 图序)换成 [Image N]。"""
    regions = job.get('regions') or []
    image_no = {int(k): v for k, v in (job.get('images_by_ref') or {}).items()}
    fix = lambda s: resolve_mentions(s, image_no).strip()   # noqa: E731
    lines = ['Edit [Image 1]. Keep the framing, composition, camera angle, lighting, colour and style exactly as in [Image 1], '
             'and leave everything that is not mentioned below unchanged. Return the complete image.']
    if regions:
        lines.append('[Image 2] is a copy of [Image 1] with numbered coloured outlines marking where to edit. '
                     'The outlines and numbers are guides only and must not appear in the result.')
        for r in regions:
            lines.append(f"Region {r['n']} ({region_where(r)}): {fix(r.get('text')) or 'apply the overall instruction here.'}")
    overall = fix(job.get('text'))
    if overall:
        lines.append(('Overall instruction: ' if regions else 'Instruction: ') + overall)
    for i in job.get('images') or []:
        if i.get('role') == 'ref':
            lines.append(f"[Image {i['n']}] is a reference image" + (f": {i['note']}" if i.get('note') else '') + '.')
    if regions and job.get('lock'):
        lines.append('Change only the marked regions.')
    return '\n'.join(lines)


def create_edit_job(base: Path, doc: Path, payload: dict, *, run_ended=None) -> dict:
    """建一个编辑任务:校验 → 画蒙版 / 带圈标注图 / 各区域裁切图 → 写 job.json(状态由调用方按发送方式定)。
    payload: {parent, regions, text, refs:[n…], provider, model, lock, mode: agent|direct, preview}。"""
    from PIL import Image
    regions = norm_regions(payload.get('regions'))
    text = str(payload.get('text') or '').strip()[:MAX_TEXT]
    if not text and not any(r['text'] for r in regions):
        raise CanvasError('写一下要改什么(整体说明或某个区域的说明)', en='Describe what to change (overall instruction or a region instruction)')
    mode = 'direct' if payload.get('mode') == 'direct' else 'agent'

    def _do(h: dict):
        busy = active_job(doc, h, run_ended)
        if busy:
            raise CanvasError(f"这张图有任务还在进行({busy['id']}),等它结束再发", 409, en=f"A job is still running on this image ({busy['id']}); wait for it to finish")
        parent = version_of(h, str(payload.get('parent') or h.get('final') or ''))
        want = [int(n) for n in (payload.get('refs') or []) if str(n).isdigit()]
        refs = [r for r in h['refs'] if not r.get('removed') and r['n'] in want]
        if len(refs) > MAX_REFS:
            raise CanvasError(f'一次最多带 {MAX_REFS} 张参考图,现在勾了 {len(refs)} 张', en=f'At most {MAX_REFS} reference images per edit; {len(refs)} are ticked')
        missing = [r['file'] for r in refs if not (base / r['file']).is_file()]
        if missing:
            raise CanvasError('参考图文件不存在:' + '、'.join(missing), en='Reference image file not found: ' + ', '.join(missing))
        preview = bool(payload.get('preview'))
        # 直接发送:要先看提示词就停在待确认(提示词 = 模板),否则排队出图;经 Agent:等 Agent 交提示词
        status = 'drafting' if mode == 'agent' else ('awaiting_confirm' if preview else 'queued')
        job = _new_job(doc, h, {'op': 'edit', 'status': status, 'parent': parent['id'], 'mode': mode, 'preview': preview,
                                'lock': bool(payload.get('lock')) and bool(regions), 'text': text, 'regions': regions,
                                'provider': str(payload.get('provider') or ''), 'model': str(payload.get('model') or '')})
        jd = _job_dir(doc, job['id'])
        src = doc / parent['file']
        suffix = src.suffix
        shutil.copy2(src, jd / f'source{suffix}')
        rel = lambda p: p.relative_to(base).as_posix()   # noqa: E731
        images = [{'n': 1, 'role': 'source', 'file': rel(jd / f'source{suffix}')}]
        if regions:
            im = load_image(src.read_bytes())
            render_marked(im, regions).save(jd / 'marked.jpg', 'JPEG', quality=90)
            render_mask(im.size, regions).save(jd / 'mask.png')
            images.append({'n': 2, 'role': 'marked', 'file': rel(jd / 'marked.jpg')})
            W, H = im.size
            for r in regions:
                x0, y0, x1, y1 = region_bbox(r)
                px, py = (x1 - x0) * 0.15 + 0.01, (y1 - y0) * 0.15 + 0.01
                crop = im.convert('RGB').crop((int(max(0, x0 - px) * W), int(max(0, y0 - py) * H),
                                               int(min(1, x1 + px) * W), int(min(1, y1 + py) * H)))
                crop.thumbnail((1024, 1024), Image.LANCZOS)
                crop.save(jd / f"region_{r['n']}.jpg", 'JPEG', quality=88)
                r['crop'] = rel(jd / f"region_{r['n']}.jpg")
        for r in refs:
            images.append({'n': len(images) + 1, 'role': 'ref', 'ref': r['n'], 'file': r['file'], 'note': r.get('note') or '', 'name': r.get('name')})
        job['images'] = images
        job['images_by_ref'] = {str(i['ref']): i['n'] for i in images if i['role'] == 'ref'}
        job['template_prompt'] = compose_prompt(job)
        if status == 'awaiting_confirm':
            job['prompt'] = job['template_prompt']
        job['parent_size'] = [parent['width'], parent['height']]
        return save_job(doc, job)
    return mutate(doc, _do)


def create_upscale_job(base: Path, doc: Path, payload: dict, *, run_ended=None) -> dict:
    """建一个放大任务。payload: {parent, mode: redraw|fidelity, provider, model, upscaler, width, height}。
    目标宽高按原图画幅校正(高由宽推),必须比原图大;保真超分另核对该模型的最大倍数。"""
    mode = 'fidelity' if payload.get('mode') == 'fidelity' else 'redraw'

    def _do(h: dict):
        busy = active_job(doc, h, run_ended)
        if busy:
            raise CanvasError(f"这张图有任务还在进行({busy['id']}),等它结束再发", 409, en=f"A job is still running on this image ({busy['id']}); wait for it to finish")
        parent = version_of(h, str(payload.get('parent') or h.get('final') or ''))
        sw, sh = parent['width'], parent['height']
        try:
            w = int(payload.get('width') or 0)
        except (TypeError, ValueError):
            w = 0
        if w <= 0:
            raise CanvasError('缺少目标宽度', en='Missing target width')
        hgt = max(2, int(round(w * sh / sw / 2)) * 2)
        w = max(2, w // 2 * 2)
        if max(w, hgt) > MAX_EDGE:
            raise CanvasError(f'目标尺寸 {w}x{hgt} 超过单边上限 {MAX_EDGE}', en=f'Target size {w}x{hgt} exceeds the per-side limit of {MAX_EDGE}')
        if w * hgt < sw * sh * 1.05:
            raise CanvasError(f'目标尺寸 {w}x{hgt} 没有比当前 {sw}x{sh} 大', en=f'Target size {w}x{hgt} is not larger than the current {sw}x{sh}')
        rec = {'op': 'upscale', 'status': 'queued', 'parent': parent['id'], 'mode': mode, 'target': [w, hgt], 'parent_size': [sw, sh],
               'provider': str(payload.get('provider') or ''), 'model': str(payload.get('model') or '')}
        if mode == 'fidelity':
            from modules import genmedia
            up = next((u for u in genmedia.image_upscalers() if u['id'] == str(payload.get('upscaler') or '')), None)
            if not up:
                raise CanvasError('没有选保真超分的模型', en='No fidelity upscaler selected')
            if not up['configured']:
                raise CanvasError(f"{up['label']} 需要 Fal API Key(「生成模型」页图像或视频的 Fal 标签页)", en=f"{up['id']} needs a Fal API key (Fal tab of image or video on the Generation Models page)")
            if max(w / sw, hgt / sh) > up['max_factor'] + 1e-6:
                raise CanvasError(f"{up['label']} 最多放大 {up['max_factor']} 倍", en=f"{up['id']} upscales at most {up['max_factor']}x")
            rec.update({'upscaler': up['id'], 'provider': up['provider'], 'model': up['id']})
        job = _new_job(doc, h, rec)
        jd = _job_dir(doc, job['id'])
        src = doc / parent['file']
        shutil.copy2(src, jd / f'source{src.suffix}')
        job['images'] = [{'n': 1, 'role': 'source', 'file': (jd / f'source{src.suffix}').relative_to(base).as_posix()}]
        return save_job(doc, job)
    return mutate(doc, _do)


# ---------------------------------------------------------------- 出图(code/canvas_edit.py 在子进程里调;generate / upscale 可注入,测试不联网)
@contextlib.contextmanager
def image_env(provider: str, model: str):
    """本次出图用指定的图像渠道 / 模型(空 = 全局);与 `genmedia image --provider/--model` 同一机制,只作用于当前进程。"""
    keys = ('VIDEOAGENTS_IMAGE_PROVIDER', 'VIDEOAGENTS_IMAGE_MODEL')
    saved = {k: os.environ.get(k) for k in keys}
    try:
        if provider:
            os.environ['VIDEOAGENTS_IMAGE_PROVIDER'] = provider
            if model:
                os.environ['VIDEOAGENTS_IMAGE_MODEL'] = model
            else:
                os.environ.pop('VIDEOAGENTS_IMAGE_MODEL', None)
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def channel_facts(provider: str, model: str) -> dict:
    """指定(或全局)图像渠道的生效配置摘要:{provider, model, limits, ref_capacity};配置读不到时抛 CanvasError。"""
    from modules import genmedia
    try:   # 显式传渠道 / 模型,不动环境变量:这个函数也在服务进程里被调(「放大」页查上限),改环境变量会串到同进程别的出图
        cfg = genmedia.get_config('image', provider, model)
    except Exception as e:  # noqa: BLE001
        raise CanvasError(f'图像渠道不可用:{e}', en=f'Image channel unavailable: {e}') from None
    if cfg.get('provider') in ('agentics', 'rhapi'):
        name = str(cfg.get('model') or cfg.get('i2i') or '')
    else:
        name = str(cfg.get('model') or '')
    try:
        limits = genmedia.image_size_limits(cfg)
    except Exception:  # noqa: BLE001
        limits = {'mode': 'unknown'}
    return {'provider': cfg.get('provider') or '', 'model': name, 'limits': limits,
            'ref_capacity': genmedia.image_ref_capacity(cfg)}


def _default_generate(prompt: str, output: str, refs: list[str], size: str, seed: int | None) -> None:
    from modules import genmedia
    genmedia.generate_image(prompt, output, refs=refs, size=size, seed=seed)


def _job_usage(jd: Path) -> dict | None:
    """本次出图的用量:genmedia 把接口返回的 usage 追加在输出目录的 usage_ledger.jsonl(目前只有火山 / BytePlus 图像接口返回),取最后一条。"""
    try:
        rec = json.loads((jd / 'usage_ledger.jsonl').read_text(encoding='utf-8').strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        return None
    out = {k: rec.get(k) for k in ('total_tokens', 'completion_tokens', 'generated_images') if rec.get(k)}
    return out or None


def _same_aspect(a: tuple[int, int], b: tuple[int, int], tol: float = 0.03) -> bool:
    return abs(a[0] / a[1] - b[0] / b[1]) / (b[0] / b[1]) <= tol


def _fail(doc: Path, jid: str, err: Exception):
    # error 中文、error_en 英文(有的话),宿主按界面语言取;渠道侧的报错原文没有英文版,原样给
    set_job(doc, jid, status='failed', error=str(err)[:800], error_en=str(getattr(err, 'en', '') or '')[:800])


def run_edit(base: Path, doc: Path, jid: str, prompt: str, *, generate=None, log=print) -> dict:
    """按提示词出一张编辑结果并落成新版本(编辑不改分辨率:模型返回的图按原图尺寸对位)。返回版本记录;失败把任务记 failed 后原样抛出。"""
    from PIL import Image
    job = load_job(doc, jid)
    if job.get('op') != 'edit':
        raise CanvasError(f'{jid} 不是编辑任务', en=f'{jid} is not an edit job')
    prompt = (prompt or '').strip()
    if not prompt:
        raise CanvasError('提示词为空', en='The prompt is empty')
    job = set_job(doc, jid, status='running', prompt=prompt, error='', error_en='')
    try:
        import random
        jd = _job_dir(doc, jid)
        images = sorted(job['images'], key=lambda i: i['n'])
        refs = [str(base / i['file']) for i in images]
        src_bytes = (base / images[0]['file']).read_bytes()
        src = load_image(src_bytes)
        W, H = src.size
        with image_env(job.get('provider') or '', job.get('model') or ''):
            facts = channel_facts('', '')
            cap = facts['ref_capacity']
            if cap == 0:
                raise CanvasError(f"当前图像渠道 {facts['provider']} 没有配置图生图,无法以原图为参考编辑", en=f"The current image channel {facts['provider']} has no image-to-image configured, so it cannot edit with the original as reference")
            if cap is not None and cap < len(refs):
                parts = '原图' + ('、带圈标注图' if job.get('regions') else '') + f"、参考图 {sum(1 for i in images if i['role'] == 'ref')} 张"
                raise CanvasError(f"当前图像渠道 {facts['provider']} 的图生图一次只收 {cap} 张图,本次要带 {len(refs)} 张({parts});"
                                  "减少参考图或换一个模型",
                                  en=f"The current image channel {facts['provider']} accepts {cap} images per image-to-image request, but this edit "
                                     f"carries {len(refs)}; use fewer reference images or pick another model")
            from modules import genmedia
            rw, rh = genmedia.fit_image_request(facts['limits'], W, H)
            seed = random.randint(1, 2 ** 31 - 1)
            raw = jd / 'result.png'
            log(f"[canvas] 编辑 {job['source']} ← {job['parent']}:{facts['provider']} {facts['model'] or '(默认模型)'},请求 {rw}x{rh},图 {len(refs)} 张")
            (generate or _default_generate)(prompt, str(raw), refs, f'{rw}x{rh}', seed)
        out = load_image(raw.read_bytes())
        model_size = list(out.size)
        regions = job.get('regions') or []
        lock = {'applied': False}
        raw_im = None
        if _same_aspect(out.size, (W, H)):
            if out.size != (W, H):
                out = out.resize((W, H), Image.LANCZOS)
            if job.get('lock') and regions:
                raw_im = out
                out, drift, applied = lock_outside(src, out, regions)
                lock = {'applied': applied, 'drift': drift, 'warn': bool(drift is not None and drift > DRIFT_WARN)}
        like = encoding_of(src_bytes)

        def _do(h: dict):
            v = _add_version(doc, h, encode_image(out, like), op='edit', parent=job['parent'], job=jid, mode=job.get('mode'),
                             provider=facts['provider'], model=facts['model'], prompt=prompt, text=job.get('text'),
                             regions=[{k: r[k] for k in r if k != 'crop'} for r in regions],
                             refs=[{'n': i['ref'], 'file': i['file'], 'note': i.get('note') or ''} for i in images if i['role'] == 'ref'],
                             seed=seed, model_size=model_size, lock=lock, usage=_job_usage(jd),
                             aspect_changed=not _same_aspect(tuple(model_size), (W, H)) or None)
            if lock.get('applied') and raw_im is not None:
                name = f"{v['id']}.raw{Path(v['file']).suffix}"
                (doc / name).write_bytes(encode_image(raw_im, like))
                v['lock'] = {**lock, 'raw': name}
            return v
        ver = mutate(doc, _do)
        set_job(doc, jid, status='done', version=ver['id'])
        log(f"[canvas] 已存为 {ver['id']}({ver['width']}x{ver['height']})"
            + (f",圈外锁定,圈外差 {lock['drift']}" if lock.get('applied') else ''))
        return ver
    except Exception as e:
        _fail(doc, jid, e)
        raise


def run_upscale(base: Path, doc: Path, jid: str, *, generate=None, upscale=None, log=print) -> dict:
    """跑一个放大任务并落成新版本。重绘放大 = 图像模型以原图为唯一参考按固定提示词重出;保真超分 = genmedia.upscale_image。
    模型返回的图没有比原图大就判失败,不拿插值凑数。"""
    from PIL import Image
    job = load_job(doc, jid)
    if job.get('op') != 'upscale':
        raise CanvasError(f'{jid} 不是放大任务', en=f'{jid} is not an upscale job')
    job = set_job(doc, jid, status='running', error='', error_en='')
    try:
        from modules import genmedia
        jd = _job_dir(doc, jid)
        src_path = base / job['images'][0]['file']
        src_bytes = src_path.read_bytes()
        sw, sh = load_image(src_bytes).size
        tw, th = job['target']
        raw = jd / 'result.png'
        info = {'mode': job['mode'], 'target': [tw, th]}
        if job['mode'] == 'fidelity':
            res = (upscale or genmedia.upscale_image)(str(src_path), str(raw), job['upscaler'], tw, th)
            provider, model = res.get('provider') or job.get('provider'), res.get('model') or job.get('upscaler')
            info.update({'upscaler': job['upscaler'], 'model_size': res.get('model_size'), 'factor': res.get('factor')})
            out = load_image(raw.read_bytes())
        else:
            import random
            with image_env(job.get('provider') or '', job.get('model') or ''):
                facts = channel_facts('', '')
                if facts['ref_capacity'] == 0:
                    raise CanvasError(f"当前图像渠道 {facts['provider']} 没有配置图生图,无法以原图为参考放大", en=f"The current image channel {facts['provider']} has no image-to-image configured, so it cannot upscale with the original as reference")
                rw, rh = genmedia.fit_image_request(facts['limits'], tw, th)
                if facts['limits'].get('mode') == 'pixels' and rw * rh < sw * sh * 1.05:
                    raise CanvasError(f"{facts['provider']} {facts['model']} 在这个画幅下最大 {rw}x{rh},放不大当前的 {sw}x{sh}", en=f"{facts['provider']} {facts['model']} tops out at {rw}x{rh} for this aspect ratio, which is not larger than the current {sw}x{sh}")
                seed = random.randint(1, 2 ** 31 - 1)
                log(f"[canvas] 重绘放大 {job['source']} ← {job['parent']}:{facts['provider']} {facts['model'] or '(默认模型)'},{sw}x{sh} → 请求 {rw}x{rh}")
                (generate or _default_generate)(UPSCALE_PROMPT, str(raw), [str(src_path)], f'{rw}x{rh}', seed)
            provider, model = facts['provider'], facts['model']
            out = load_image(raw.read_bytes())
            info.update({'model_size': list(out.size), 'requested': [rw, rh], 'seed': seed})
            if _same_aspect(out.size, (sw, sh)) and out.size[0] * out.size[1] > rw * rh * 1.02:
                out = out.resize((rw, rh), Image.LANCZOS)
        if out.size[0] * out.size[1] < sw * sh * 1.05:
            raise CanvasError(f'模型返回 {out.size[0]}x{out.size[1]},没有比原图 {sw}x{sh} 大(该模型没有按请求尺寸出图)', en=f'The model returned {out.size[0]}x{out.size[1]}, which is not larger than the original {sw}x{sh} (it did not honour the requested size)')
        info['factor'] = info.get('factor') or round(out.size[0] / sw, 3)
        data = encode_image(out, encoding_of(src_bytes))
        ver = mutate(doc, lambda h: _add_version(doc, h, data, op='upscale', parent=job['parent'], job=jid, provider=provider, model=model,
                                                 upscale=info, usage=_job_usage(jd),
                                                 aspect_changed=not _same_aspect(out.size, (sw, sh)) or None))
        set_job(doc, jid, status='done', version=ver['id'])
        log(f"[canvas] 已存为 {ver['id']}({ver['width']}x{ver['height']})")
        return ver
    except Exception as e:
        _fail(doc, jid, e)
        raise


# ---------------------------------------------------------------- 非 AI 操作:裁剪 / 翻转 / 旋转 / 缩放(同样记为版本)
def transform(base: Path, doc: Path, parent_id: str, op: str, params: dict | None = None) -> dict:
    from PIL import Image
    params = params or {}
    h = load_history(doc)
    parent = version_of(h, parent_id)
    data = (doc / parent['file']).read_bytes()
    im = load_image(data)
    W, H = im.size
    if op == 'crop':
        x, y, w, hh = (_clamp01(params.get(k)) for k in ('x', 'y', 'w', 'h'))
        box = (int(round(x * W)), int(round(y * H)), int(round(min(1.0, x + w) * W)), int(round(min(1.0, y + hh) * H)))
        if box[2] - box[0] < 32 or box[3] - box[1] < 32:
            raise CanvasError('选区太小:裁切后每边至少 32 px', en='Selection too small: each side must be at least 32 px after cropping')
        if (box[2] - box[0], box[3] - box[1]) == (W, H):
            raise CanvasError('选区等于整图,无需裁剪', en='The selection equals the whole image; nothing to crop')
        out, detail = im.crop(box), {'box_px': list(box)}
    elif op == 'flip_h':
        out, detail = im.transpose(Image.FLIP_LEFT_RIGHT), {}
    elif op == 'flip_v':
        out, detail = im.transpose(Image.FLIP_TOP_BOTTOM), {}
    elif op == 'rotate':
        deg = int(params.get('deg') or 0)
        if deg not in (90, 180, 270):
            raise CanvasError('旋转角度只能是 90 / 180 / 270', en='Rotation must be 90 / 180 / 270')
        out = im.transpose({90: Image.ROTATE_270, 180: Image.ROTATE_180, 270: Image.ROTATE_90}[deg])   # 顺时针
        detail = {'deg': deg}
    elif op == 'resize':
        try:
            w, hh = int(params.get('width') or 0), int(params.get('height') or 0)
        except (TypeError, ValueError):
            w = hh = 0
        if w < 16 or hh < 16 or max(w, hh) > MAX_EDGE:
            raise CanvasError(f'缩放尺寸须在 16 到 {MAX_EDGE} 之间', en=f'Resize dimensions must be between 16 and {MAX_EDGE}')
        if (w, hh) == (W, H):
            raise CanvasError('尺寸没有变化', en='The size is unchanged')
        out, detail = im.resize((w, hh), Image.LANCZOS), {'from': [W, H]}
    else:
        raise CanvasError(f'未知操作:{op}', en=f'Unknown operation: {op}')
    payload = encode_image(out, encoding_of(data))
    return mutate(doc, lambda hist: _add_version(doc, hist, payload, op='transform', parent=parent['id'], transform={'op': op, **detail}))


def unlock_version(base: Path, doc: Path, vid: str) -> dict:
    """圈外锁定的版本「改用整图」:取锁定前模型返回的整图另存一个版本(原版本保留)。"""
    h = load_history(doc)
    v = version_of(h, vid)
    raw = (v.get('lock') or {}).get('raw')
    if not raw or not (doc / raw).is_file():
        raise CanvasError('这个版本没有可用的整图(不是圈外锁定出的)', en='This version has no full-image result (it was not produced with outside-lock)')
    data = (doc / raw).read_bytes()
    keep = {k: v.get(k) for k in ('job', 'mode', 'provider', 'model', 'prompt', 'text', 'regions', 'refs', 'seed', 'model_size')}
    return mutate(doc, lambda hist: _add_version(doc, hist, data, op='edit', parent=v.get('parent'), from_raw=vid,
                                                 lock={'applied': False, 'from_raw': vid}, **keep))


def delete_version(base: Path, doc: Path, vid: str) -> dict:
    """从历史里移除一个版本(文件挪进 trash/,不真删)。原图、最终版、原路径上的那一版、正在用作任务底图的版本不能删。"""
    def _do(h: dict):
        v = version_of(h, vid)
        if v.get('op') == 'original' or vid in (h.get('final'), h.get('disk')):
            raise CanvasError('原图 / 最终版不能删除', en='The original / final version cannot be deleted')
        if v.get('plate_key'):
            raise CanvasError(f"这个版本已登记为背景图 {v['plate_key']},不能删除", en=f"This version is registered as plate {v['plate_key']} and cannot be deleted")
        busy = active_job(doc, h)
        if busy and busy.get('parent') == vid:
            raise CanvasError('有任务正在用这个版本作底图', en='A running job is using this version as its base')
        trash = doc / 'trash'
        trash.mkdir(exist_ok=True)
        for name in (v.get('file'), v.get('thumb'), (v.get('lock') or {}).get('raw')):
            if name and (doc / name).is_file():
                os.replace(doc / name, trash / name)
        v['deleted'] = now_iso()
        return {'ok': True, 'id': vid}
    return mutate(doc, _do)


# ---------------------------------------------------------------- 最终版
OP_LABELS = {'original': '原图', 'external': '外部更新', 'edit': '编辑', 'upscale': '放大', 'transform': '调整'}


def lineage(h: dict, vid: str) -> list[dict]:
    """从最近一个原图 / 外部更新版本到 vid 的修改链(旧 → 新),不含那个起点。"""
    by = {v['id']: v for v in h['versions']}
    chain, cur, seen = [], by.get(vid), set()
    while cur and cur['id'] not in seen and cur.get('op') not in ('original', 'external'):
        seen.add(cur['id'])
        chain.append(cur)
        cur = by.get(cur.get('parent'))
    return chain[::-1]


def describe_changes(h: dict, vid: str) -> str:
    """修改链的文字摘要(登记背景图的 change、发给修改师同步设定用):每步一行,带用户原话。"""
    lines = []
    for v in lineage(h, vid):
        if v['op'] == 'edit':
            parts = [f"区域{r['n']}:{r['text']}" for r in v.get('regions') or [] if r.get('text')]
            if v.get('text'):
                parts.append(v['text'])
            refs = '、'.join(f"图{r['n']}={r['note']}" for r in v.get('refs') or [] if r.get('note'))
            lines.append(f"{v['id']} 编辑:" + (';'.join(parts) or '(未写说明)') + (f"(参考图:{refs})" if refs else ''))
        elif v['op'] == 'upscale':
            lines.append(f"{v['id']} 放大到 {v['width']}x{v['height']}")
        elif v['op'] == 'transform':
            t = v.get('transform') or {}
            lines.append(f"{v['id']} 调整:{t.get('op')}" + (f" {t.get('deg')}°" if t.get('deg') else ''))
    return '\n'.join(lines)


def adopt(base: Path, doc: Path, vid: str, *, repoint: bool = True, log=print) -> dict:
    """设为最终版。固定路径的图:把该版本写回原路径(原路径上的图若不在历史里先收为外部更新);草图同时更新台账。
    分镜背景图:登记为 <key>_revN(登记过的版本直接复用;选的是原路径上那一版 = 改回原图)并把引用的分镜改指过去。
    返回 {final, storage, target_file, replaced, plate_key, created}。"""
    h = load_history(doc)
    v = version_of(h, vid)
    rel, storage, meta = h['source'], h.get('storage') or 'fixed', h.get('meta') or {}
    src = base / rel
    if not src.is_file():
        raise CanvasError(f'原图已不在:{rel}', 404, en=f'The original image is gone: {rel}')
    result = {'final': vid, 'storage': storage, 'target_file': rel, 'replaced': [], 'created': False}
    if storage == 'plate':
        from modules import shot_plates
        is_disk = v['sha256'] == hashlib.sha256(src.read_bytes()).hexdigest()
        existing = meta['key'] if is_disk else (v.get('plate_key') or '')
        also = [x['plate_key'] for x in h['versions'] if x.get('plate_key')]
        try:
            res = shot_plates.adopt_canvas_plate(
                base, meta['sid'], meta['key'], None if existing else doc / v['file'], existing_key=existing, also_from=also,
                repoint=repoint, change=describe_changes(h, vid), note='',
                channel={'provider': v.get('provider'), 'model': v.get('model')} if v.get('provider') else None,
                canvas={'doc': doc.name, 'version': vid}, log=log)
        except (ValueError, LookupError) as e:
            raise CanvasError(str(e)) from None
        result.update({'target_file': res['file'], 'plate_key': res['key'], 'replaced': res['replaced'], 'created': res['created'],
                       'sync_errors': [e for s in res.get('syncs') or [] for e in (s.get('errors') or [])]})
        if res['created']:
            _set_alias(base, res['file'], rel)

        def _do(hist: dict):
            vv = version_of(hist, vid)
            if not is_disk:
                vv['plate_key'] = res['key']
            hist['final'] = vid
            hist['final_log'].append({'version': vid, 'at': now_iso(), 'plate_key': res['key'], 'replaced': res['replaced']})
        mutate(doc, _do)
        return result
    data = (doc / v['file']).read_bytes()
    open_doc(base, rel, h.get('kind') or '')          # 原路径上的图若被别处改过,先收进历史再覆盖
    tmp = src.with_name(src.name + '.canvas.tmp')
    tmp.write_bytes(data)
    os.replace(tmp, src)
    if storage == 'sketch':
        from modules import storyboard_board as sbb
        patch = {'status': 'done', 'error': '', 'mode': 'canvas', 'canvas_version': vid}
        if v.get('provider'):
            patch.update({'provider': v['provider'], 'model': v.get('model') or ''})
        sbb.update_index(base, meta['ep'], meta['key'], patch)

    def _do(hist: dict):
        hist['final'] = hist['disk'] = vid
        hist['final_log'].append({'version': vid, 'at': now_iso(), 'target_file': rel})
    mutate(doc, _do)
    return result


def impact(base: Path, h: dict) -> dict:
    """设为最终版之前给用户看的影响清单:{users: [谁在用这张图], notes: [提示代号]}(代号由页面翻成文字)。"""
    rel, storage, kind, meta = h['source'], h.get('storage'), h.get('kind'), h.get('meta') or {}
    users, notes = [], []
    if storage == 'plate':
        from modules import shot_plates
        keys = dict.fromkeys([meta.get('key'), *[x['plate_key'] for x in h['versions'] if x.get('plate_key')]])
        for k in keys:
            for u in shot_plates.plate_users(base, meta.get('sid') or '', k or ''):
                users.append(u['ep'] + '/' + u['shot_id'] + ('(end)' if u['role'] == 'end' else ''))
        notes.append('plate_new_key')
    else:
        for f in sorted((base / 'assets' / 'prompts').glob('ep*/grp*.json')):
            try:
                text = f.read_text(encoding='utf-8')
            except OSError:
                continue
            if rel not in text:
                continue
            try:
                d = json.loads(text)
            except ValueError:
                continue
            if rel in (d.get('refs') or []):
                users.append(f'{f.parent.name}/{f.stem}')
    if users:
        notes.append('videos_unchanged')
    if kind in ('character', 'costume'):
        notes.append('avatar')
    if kind == 'sketch' and (base / 'assets' / 'storyboard' / str(meta.get('ep') or '') / 'animatic.mp4').is_file():
        notes.append('animatic')
    if kind == 'cover' and any((base / 'publish').glob(f"*/package/{meta.get('ep') or '_'}")):
        notes.append('publish_copy')
    return {'users': list(dict.fromkeys(users)), 'notes': notes}


def provenance(base: Path, h: dict) -> dict:
    """原图的出处(能查到的才有):分镜背景图取库条目,草图取草图台账。"""
    meta = h.get('meta') or {}
    try:
        if h.get('storage') == 'plate':
            from modules import shot_plates
            e = next((x for x in shot_plates.load_library(base, meta['sid'])['plates'] if x.get('key') == meta['key']), None) or {}
            ch = e.get('channel') or {}
            return {k: v for k, v in {'provider': ch.get('provider'), 'model': ch.get('model'), 'prompt': e.get('prompt'), 'seed': e.get('seed'),
                                      'source_kind': (e.get('pano_ref') or {}).get('kind'), 'written_at': e.get('written_at')}.items() if v}
        if h.get('storage') == 'sketch':
            from modules import storyboard_board as sbb
            r = sbb.load_index(base, meta['ep'])['shots'].get(meta['key']) or {}
            return {k: r.get(k) for k in ('provider', 'model', 'prompt', 'note', 'mode', 'updated_at') if r.get(k)}
    except Exception:  # noqa: BLE001
        pass
    return {}


def public_job(job: dict | None) -> dict | None:
    if not job:
        return None
    keep = ('id', 'op', 'status', 'mode', 'preview', 'parent', 'created_at', 'updated_at', 'error', 'error_en', 'version', 'run_id', 'prompt',
            'template_prompt', 'provider', 'model', 'target', 'upscaler', 'lock', 'text')
    return {k: job.get(k) for k in keep if job.get(k) not in (None, '')}


def state(base: Path, doc: Path, h: dict, *, run_ended=None) -> dict:
    """页面要的全部状态(路径都是项目内相对路径,URL 由宿主拼)。"""
    rel = h['source']
    droot = doc.relative_to(base).as_posix()
    jobs = recent_jobs(doc, h, 6)
    active = active_job(doc, h, run_ended)
    if active:
        jobs = recent_jobs(doc, h, 6)                 # active_job 可能刚把死任务改成 failed
    last = next((j for j in reversed(jobs) if j.get('status') not in ACTIVE), None)
    vers = []
    for v in h['versions']:
        if v.get('deleted'):
            continue
        d = dict(v)
        d['file_rel'] = f"{droot}/{v['file']}"
        d['thumb_rel'] = f"{droot}/{v['thumb']}" if v.get('thumb') else d['file_rel']
        raw = (v.get('lock') or {}).get('raw')
        if raw:
            d['raw_rel'] = f'{droot}/{raw}'
        vers.append(d)
    kind = h.get('kind') or 'image'
    return {'file': rel, 'opened': h.get('opened') or rel, 'doc': droot, 'kind': kind, 'storage': h.get('storage') or 'fixed',
            'meta': h.get('meta') or {}, 'info': {**file_info(base / rel), 'path': rel}, 'versions': vers,
            'final': h.get('final'), 'disk': h.get('disk'), 'refs': [r for r in h['refs'] if not r.get('removed')],
            'job': public_job(active), 'last_job': public_job(last), 'provenance': provenance(base, h),
            'setting_kind': SETTING_KINDS.get(kind) or '', 'pref_kind': PREF_KINDS.get(kind) or '',
            'drift_warn': DRIFT_WARN, 'max_refs': MAX_REFS}


def set_ui(doc: Path, label: str = '', oid: str = '') -> None:
    """页面打开时带来的对象名 / 对象编号(服装号、人物名这类路径里判不出的),只在变了的时候写。"""
    label, oid = str(label or '').strip()[:200], re.sub(r'[^\w\-.]', '', str(oid or ''))[:80]
    if not (label or oid):
        return
    h = load_history(doc)
    ui = h.get('ui') or {}
    if (label and ui.get('label') != label) or (oid and ui.get('oid') != oid):
        mutate(doc, lambda hist: hist.update({'ui': {'label': label or ui.get('label') or '', 'oid': oid or ui.get('oid') or ''}}))
