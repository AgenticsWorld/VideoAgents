"""场景图(正向 / 反向)——白模关闭项目的场景一致性方案(A 方案,2026-09-17)。

适用范围:项目「输出设置 → 白模」(output.spatial_blocking)**关闭**的项目(非 Seedance 2.5 的默认路径)。白模开启的项目走
白模视频 + 全景 + 分镜背景图那条链(modules/shot_plates.py / whitebox_refs.py),本模块一律跳过、互不触碰。

做法(offer SCN-0006 三组实测,docs/scene_plates.md):
  1. 正向图 front:环境概念工位(p4)出的主视角图——站在入口往内看,整间主体陈设一次入画;连同站位/看向/画内清单登记到
     assets/concepts/scenes/<sid>/scene_plates.json。
  2. 反向图 reverse:以正向图为母版,站在场景远端朝入口回望,补全正向图缺的那面(门、窗等开口尤其要对);由宿主脚本
     code/render_scene_plates.py 按需生成(p6-scene-plates 节点,分镜定稿后)。
  3. 每镜用哪张:分镜/镜头规划工位在 shot_list 每镜写 `plate_view: front | reverse`(语义判定,不靠坐标);
     code/sync_scene_plates.py --write 把两张整图挂进组 refs(角色/生物 sheet 之后),`Shot 1:` 前写 `Scene plates:` 段,
     每个 Shot 段头写 `Scene plate: this shot uses [Image N] … and not [Image M].`;机检 scene_plate_bound。

配置:
  项目级 settings.json output.scene_plates ∈ auto(默认) | single | pair
    auto   = 正向必出;本项目任一集 shot_list 里该场景有镜标 reverse 才出反向图(分镜定稿后按需)
    single = 只出正向(平面动画 / 单面布景);标 reverse 的镜照用正向图并 WARN
    pair   = 每场景正反两张都出(p4 一并出)
  场景级 scene_plates.json mode ∈ inherit(默认) | single | pair,优先于项目级。
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
from pathlib import Path

from modules.prompt_layout import paragraphize
from modules.shot_plates import _remap_images, GC_KEY, _SPATIAL_RE, _GRID_RE, _MAPUSE_RE, _MAPONLY_RE, _TILE_RE
from modules.whitebox import component, read

SCHEMA = 'scene_plates.v1'
FILE = 'scene_plates.json'
PROJECT_MODES = ('auto', 'single', 'pair')
SCENE_MODES = ('inherit', 'single', 'pair')
VIEWS = ('front', 'reverse')
DEFAULT_FRONT_FILES = ('main_01.png', 'main.png')
REVERSE_FILE = 'reverse_01.png'
BLOCK_KEY = 'Scene plates:'
LINE_KEY = 'Scene plate:'
GC_EXTRA = "no top-down or bird's-eye view, no map or floor-plan imagery, no tiled grid or contact sheet"
NEGATIVE_BASE = ('people, person, human figure, silhouette, crowd, animals, text, watermark, logo, grid lines, split screen, collage, '
                 "top-down view, bird's-eye view, map, floor plan, fisheye, barrel distortion, tilted horizon, dutch angle, "
                 'shallow depth of field, bokeh, blurred background, vignette, duplicated furniture, mirrored copy of the reference composition')

_BLOCK_RE = re.compile(r'\s*' + re.escape(BLOCK_KEY) + r'.*?never shows the room from above\.', re.S)
_LINE_RE = re.compile(r'\s*' + re.escape(LINE_KEY) + r' this shot uses \[Image\s*\d+\] \([^)]*\)(?: and not \[Image\s*\d+\])?\.')
_SHOT_HEAD_RE = r'Shot\s*%d\s*(?:[:：]|[｜|][^。\n]*。)'
_MARKERS_RE = re.compile(r'\s*Map markers:[^.]*\.')          # 8 月旧链路的俯视动线图图例句,一并清理
_ROUTE_MAP_RE = re.compile(r' route on the map:')


def _now():
    return dt.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')


# ---------------------------------------------------------------- 配置
def project_mode(base: Path) -> str:
    st = read(base / 'settings.json', {}) or {}
    m = str(((st.get('output') or {}).get('scene_plates')) or 'auto').strip().lower()
    return m if m in PROJECT_MODES else 'auto'


def whitebox_on(base: Path) -> bool:
    st = read(base / 'settings.json', {}) or {}
    return (st.get('output') or {}).get('spatial_blocking') is True


def scene_dir(base: Path, sid: str) -> Path:
    return base / 'assets/concepts/scenes' / component(sid)


def scene_plates_path(base: Path, sid: str) -> Path:
    return scene_dir(base, sid) / FILE


def load_scene_plates(base: Path, sid: str) -> dict:
    """读登记;没有登记但有旧版 main_01.png 时给一条兼容记录(legacy=True,站位/画内清单为空)。"""
    rec = read(scene_plates_path(base, sid), {}) or {}
    if rec:
        rec.setdefault('mode', 'inherit')
        return rec
    d = scene_dir(base, sid)
    for name in DEFAULT_FRONT_FILES:
        if (d / name).is_file():
            return {'schema_version': SCHEMA, 'scene_id': sid, 'mode': 'inherit', 'legacy': True,
                    'front': {'file': name, 'standing_en': '', 'looking_en': '', 'in_frame_en': [], 'behind_en': []}, 'reverse': None}
    return {}


def save_scene_plates(base: Path, sid: str, rec: dict):
    rec = dict(rec)
    rec['schema_version'] = SCHEMA
    rec['scene_id'] = sid
    rec.pop('legacy', None)
    p = scene_plates_path(base, sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def front_digest(base: Path, sid: str, rec: dict | None) -> str | None:
    """正向图文件 sha256(出反向图时记进 reverse.front_sha256;正向图重出后不一致 = 反向图过期)。"""
    import hashlib
    f = plate_file(base, sid, rec or {}, 'front')
    if not f:
        return None
    h = hashlib.sha256()
    with open(f, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def reverse_stale(base: Path, sid: str, rec: dict | None) -> bool:
    """反向图是否过期:已出反向图、记录了母版哈希、且当前正向图哈希不同。未记哈希的旧记录不判过期。"""
    rev = (rec or {}).get('reverse') or {}
    if not plate_file(base, sid, rec or {}, 'reverse') or not rev.get('front_sha256'):
        return False
    return front_digest(base, sid, rec) != rev['front_sha256']


def effective_mode(base: Path, rec: dict | None) -> str:
    m = str((rec or {}).get('mode') or 'inherit').strip().lower()
    return m if m in ('single', 'pair') else project_mode(base)


def plate_file(base: Path, sid: str, rec: dict, view: str) -> Path | None:
    v = (rec or {}).get(view) or {}
    name = v.get('file') if isinstance(v, dict) else None
    if not name:
        return None
    p = scene_dir(base, sid) / str(name)
    return p if p.is_file() else None


# ---------------------------------------------------------------- 分镜引用
def _shot_list_files(base: Path, ep: str | None = None):
    if ep:
        f = base / 'directing' / component(ep) / 'shot_list.json'
        return [f] if f.is_file() else []
    return sorted((base / 'directing').glob('ep*/shot_list.json'))


def shot_scene_id(shot: dict, groups_by_shot: dict) -> str | None:
    return shot.get('scene_id') or shot.get('bible_scene') or (groups_by_shot.get(shot.get('shot_id')) or {}).get('scene_id')


def shots_by_scene(base: Path, sid: str, ep: str | None = None) -> list:
    """本项目各集 shot_list 里属于该场景的镜:{ep, shot_id, group_id, plate_view}。"""
    out = []
    for f in _shot_list_files(base, ep):
        sl = read(f, {}) or {}
        gmap = {}
        for g in sl.get('generation_groups') or []:
            for s in g.get('shots') or g.get('shot_ids') or []:
                gmap[s] = g
        for s in sl.get('shots') or []:
            if shot_scene_id(s, gmap) != sid:
                continue
            pv = str(s.get('plate_view') or '').strip().lower() or None
            out.append({'ep': f.parent.name, 'shot_id': s.get('shot_id'), 'group_id': s.get('group_id') or (gmap.get(s.get('shot_id')) or {}).get('group_id'),
                        'plate_view': pv if pv in VIEWS else None, 'plate_view_raw': s.get('plate_view')})
    return out


def reverse_needed(base: Path, sid: str, rec: dict | None) -> dict:
    mode = effective_mode(base, rec)
    shots = shots_by_scene(base, sid)
    by = [f"{s['ep']}/{s['shot_id']}" for s in shots if s['plate_view'] == 'reverse']
    if mode == 'single':
        return {'needed': False, 'mode': mode, 'needed_by': by, 'reason': 'single:只出正向' + (f';{len(by)} 镜标了 reverse 将改用正向图' if by else '')}
    if mode == 'pair':
        return {'needed': True, 'mode': mode, 'needed_by': by, 'reason': 'pair:每场景正反两张'}
    return {'needed': bool(by), 'mode': mode, 'needed_by': by, 'reason': (f'auto:{len(by)} 镜标 reverse' if by else 'auto:无镜标 reverse,暂不出')}


def scene_ids(base: Path, ep: str | None = None) -> list:
    """有登记或有旧版主图的场景;给了 ep 只取该集 shot_list 引用的场景。"""
    ids = set()
    if ep:
        for f in _shot_list_files(base, ep):
            sl = read(f, {}) or {}
            for g in sl.get('generation_groups') or []:
                if g.get('scene_id') or g.get('bible_scene'):
                    ids.add(g.get('scene_id') or g.get('bible_scene'))
            for s in sl.get('shots') or []:
                if s.get('scene_id') or s.get('bible_scene'):
                    ids.add(s.get('scene_id') or s.get('bible_scene'))
    else:
        root = base / 'assets/concepts/scenes'
        if root.is_dir():
            for d in root.iterdir():
                if d.is_dir() and load_scene_plates(base, d.name):
                    ids.add(d.name)
    return sorted(i for i in ids if isinstance(i, str))


def status(base: Path, ep: str | None = None, scenes: list | None = None) -> dict:
    rows, errors, warnings = [], [], []
    for sid in (scenes or scene_ids(base, ep)):
        rec = load_scene_plates(base, sid)
        if not rec:
            errors.append(f'{sid}: 没有 scene_plates.json 也没有 main_01.png(环境概念工位未出正向图)')
            rows.append({'scene_id': sid, 'front': None, 'reverse': None, 'state': 'missing_front'})
            continue
        front = plate_file(base, sid, rec, 'front')
        need = reverse_needed(base, sid, rec)
        rev = plate_file(base, sid, rec, 'reverse')
        state = 'ok'
        if not front:
            errors.append(f"{sid}: 正向图 {(rec.get('front') or {}).get('file')} 不存在"); state = 'missing_front'
        elif need['needed'] and not rev:
            errors.append(f"{sid}: 需要反向图({need['reason']})但未出:python3 code/render_scene_plates.py --project <slug> --scene {sid}"); state = 'missing_reverse'
        elif rev and reverse_stale(base, sid, rec):
            errors.append(f"{sid}: 反向图过期——正向图 {(rec.get('front') or {}).get('file')} 已重出,与母版不一致;重跑 python3 code/render_scene_plates.py --project <slug> --scene {sid}"); state = 'stale_reverse'
        elif rec.get('legacy'):
            warnings.append(f'{sid}: 只有旧版 main_01.png、无 scene_plates.json 登记(站位/画内清单为空,建议回派 environment-concept 补登记)'); state = 'legacy'
        for s in shots_by_scene(base, sid, ep):
            if s['plate_view'] is None and s.get('shot_id'):
                warnings.append(f"{s['ep']}/{s['shot_id']}: 未写 plate_view(按 front 处理)")
        rows.append({'scene_id': sid, 'mode': rec.get('mode', 'inherit'), 'effective': need['mode'], 'front': str(front.relative_to(base)) if front else None,
                     'reverse': str(rev.relative_to(base)) if rev else None, 'reverse_needed': need['needed'], 'needed_by': need['needed_by'],
                     'reverse_stale': bool(rev) and reverse_stale(base, sid, rec), 'state': state})
    return {'scenes': rows, 'errors': errors, 'warnings': warnings, 'project_mode': project_mode(base)}


# ---------------------------------------------------------------- 反向图提示词与出图
def _flat(v):
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        return '; '.join(x for x in (_flat(i) for i in v) if x)
    if isinstance(v, dict):
        return '; '.join(x for x in (_flat(i) for i in v.values()) if x)
    return ''


def _style(base: Path) -> tuple[str, str]:
    style = read(base / 'bible/style.json', {}) or {}
    neg = style.get('negative_prompt_en') or (style.get('style_prompt_en') or {}).get('negative') or ''
    if isinstance(neg, list):
        neg = ', '.join(str(x) for x in neg)
    frag = style.get('style_fragment_en')
    if not frag:
        sp = style.get('style_prompt_en') or {}
        frag = ', '.join(x for x in (str(sp.get('base') or ''), str(sp.get('register_day') or '')) if x)
    drop = re.compile(r'character|skin|hair|garment|fabric|embroider|face|costume|depth of field|bokeh', re.I)
    frag = ', '.join(c.strip() for c in str(frag).split(',') if c.strip() and not drop.search(c))
    return frag, str(neg)


def _scene_docs(base: Path, sid: str) -> tuple[str, str, str]:
    """(建筑/材质描述, 光照片段, 建筑负面串)。"""
    bdir = base / 'bible/scenes' / component(sid)
    arch = read(bdir / 'architecture.json', {}) or {}
    interior = arch.get('interior') or {}
    roof = arch.get('roof') or {}
    parts = [_flat(arch.get('form')), _flat(arch.get('materials')), _flat(arch.get('openings_style')),
             _flat(interior.get('furniture_tone')), _flat({k: roof.get(k) for k in ('form', 'covering')})]
    desc = ' '.join(p.rstrip('.。;') + '.' for p in parts if p)[:1500]
    light = ''
    for s in (read(bdir / 'lighting.json', {}) or {}).get('schemes') or []:
        if s.get('prompt_fragment_en'):
            light = re.sub(r'^\s*lighting:\s*', '', str(s['prompt_fragment_en']).strip(), flags=re.I)
            break
    neg = arch.get('negative') or []
    return desc, light, (', '.join(str(x) for x in neg) if isinstance(neg, list) else str(neg))


def scene_name(base: Path, sid: str) -> str:
    idx = read(base / 'bible/scenes/index.json', {}) or {}
    for s in idx.get('scenes') or []:
        if isinstance(s, dict) and s.get('id') == sid:
            return str(s.get('name_en') or s.get('name') or sid)
    return sid


def build_reverse_prompt(base: Path, sid: str, rec: dict) -> tuple[str, str]:
    """反向图提示词:[Image 1] = 正向图(母版)。站位/看向/画内清单优先取登记里 reverse 段已写的(工位可预填),否则由正向段推出。"""
    front = rec.get('front') or {}
    rev = rec.get('reverse') or {}
    name = scene_name(base, sid)
    f_stand = front.get('standing_en') or 'just inside the entrance'
    f_look = front.get('looking_en') or 'into the location'
    standing = rev.get('standing_en') or f'at the far end of the location, opposite where the front view stood ({f_stand})'
    looking = rev.get('looking_en') or f'back toward the entrance, i.e. toward where the front view was standing ({f_stand})'
    in_frame = rev.get('in_frame_en') or []
    behind = rev.get('behind_en') or front.get('in_frame_en') or []
    must_show = front.get('behind_en') or []
    desc, light, arch_neg = _scene_docs(base, sid)
    style, style_neg = _style(base)
    parts = [f'Empty location background plate, one single full-frame still with nobody present and nothing moving. Location: {name}.',
             f'This is the REVERSE view of the same location as [Image 1]. [Image 1] is the front view: standing {f_stand}, looking {f_look}. '
             f'This picture stands {standing}, looking {looking}: everything that was far away in [Image 1] is now close to the camera, and what was '
             f'behind the camera in [Image 1] is now in front of it. Match [Image 1] exactly in architecture, materials, colours, weathering, '
             f'light quality and every fixed element, but do NOT copy its composition or viewpoint — this is the opposite angle.',
             'Camera: wide-angle rectilinear lens, eye level, the lens axis horizontal — not tilted up or down, no dutch angle, every vertical edge '
             'stays vertical, straight lines stay straight, no fisheye.']
    if must_show:
        parts.append('This view must show, correctly placed, what the front view could not: ' + '; '.join(str(x) for x in must_show) + '. '
                     'Doors, windows and other openings must sit exactly where the location description puts them — one opening each, nothing added.')
    if in_frame:
        parts.append('In frame (left to right): ' + '; '.join(str(x) for x in in_frame) + '.')
    if behind:
        parts.append('Behind the camera, NOT visible in this frame (do not paint them in): ' + '; '.join(str(x) for x in behind) + '.')
    if light:
        parts.append('Lighting: ' + light.rstrip('.') + '.')
    if desc:
        parts.append('Materials and era (reference only): ' + desc)
    parts.append('Empty location plate: no people, no characters, no animals, no text, no watermark, one single photograph, everything in sharp focus front to back.')
    if style:
        parts.append('Style: ' + style)
    negative = ', '.join(x for x in (NEGATIVE_BASE, style_neg, arch_neg) if x)
    return '\n'.join(parts), negative


def plate_size(base: Path) -> str:
    """按项目画幅取长边 2560 的尺寸。"""
    try:
        from modules.whitebox import render_format
        fmt = render_format(read(base / 'settings.json', {}) or {}, width=2560)
        w, h = int(fmt['width']), int(fmt['height'])
        if h > w:
            fmt = render_format(read(base / 'settings.json', {}) or {}, height=2560)
            w, h = int(fmt['width']), int(fmt['height'])
        return f'{w}x{h}'
    except Exception:  # noqa: BLE001
        return '2560x1440'


def render_reverse(base: Path, sid: str, *, force: bool = False, dry_run: bool = False, seed: int | None = None, log=print) -> dict:
    rec = load_scene_plates(base, sid)
    if not rec:
        raise FileNotFoundError(f'{sid}: 没有 scene_plates.json / main_01.png,先由 environment-concept 出正向图并登记')
    front = plate_file(base, sid, rec, 'front')
    if not front:
        raise FileNotFoundError(f"{sid}: 正向图 {(rec.get('front') or {}).get('file')} 不存在")
    existing = plate_file(base, sid, rec, 'reverse')
    if existing and not force:
        if reverse_stale(base, sid, rec):
            log(f'   {sid}: 反向图 {existing.name} 过期(正向图已重出),重出')
        else:
            log(f'   {sid}: 反向图已有 {existing.name},跳过(--force 重出)')
            return rec
    prompt, negative = build_reverse_prompt(base, sid, rec)
    out = scene_dir(base, sid) / REVERSE_FILE
    (scene_dir(base, sid) / 'reverse_01.prompt.txt').write_text(prompt + '\n\nREFS: ' + front.name + '\n\nNEGATIVE: ' + negative + '\n', encoding='utf-8')
    if dry_run:
        log(f'   [dry-run] {sid}: 反向图提示词 {len(prompt)} 字 → reverse_01.prompt.txt(母版 {front.name})')
        return rec
    from modules.genmedia import generate_image, get_config, image_pref_env
    with image_pref_env('scenes'):
        cfg = get_config('image')
    if out.is_file():
        (scene_dir(base, sid) / 'candidates').mkdir(exist_ok=True)
        out.rename(scene_dir(base, sid) / 'candidates' / f'reverse_01.prev-{dt.datetime.now().strftime("%Y%m%d-%H%M%S")}.png')
    size = plate_size(base)
    if seed is None:
        import random
        seed = random.randint(1, 2 ** 31 - 1)
    log(f"   出 {sid} 反向图 {cfg.get('provider')}/{cfg.get('model')} {size} seed {seed},母版 {front.name} …")
    generate_image(prompt, str(out), negative=negative, refs=[str(front)], size=size, seed=seed)
    rev = dict(rec.get('reverse') or {})
    rev.update({'file': out.name, 'mirror_ref': front.name, 'front_sha256': front_digest(base, sid, rec), 'generated_at': _now(), 'seed': seed, 'size': size,
                'channel': {'provider': cfg.get('provider'), 'model': cfg.get('model')}, 'prompt': prompt, 'negative': negative})
    rev.setdefault('standing_en', f"at the far end of the location, opposite {((rec.get('front') or {}).get('standing_en') or 'the entrance')}")
    rev.setdefault('looking_en', 'back toward the entrance')
    rec['reverse'] = rev
    rec['reverse_needed_by'] = reverse_needed(base, sid, rec)['needed_by']
    save_scene_plates(base, sid, rec)
    log(f'saved: {out.relative_to(base)}')
    return rec


def render_needed(base: Path, ep: str | None = None, scenes: list | None = None, *, force=False, dry_run=False, seed=None, log=print) -> dict:
    """按生效模式给需要反向图的场景出图(auto 按各集 shot_list 的 plate_view 统计)。"""
    done, skipped = [], []
    for sid in (scenes or scene_ids(base, ep)):
        rec = load_scene_plates(base, sid)
        if not rec:
            skipped.append((sid, '无正向图登记')); continue
        need = reverse_needed(base, sid, rec)
        if not need['needed'] and not force:
            skipped.append((sid, need['reason'])); log(f"   {sid}: {need['reason']}"); continue
        if plate_file(base, sid, rec, 'reverse') and not force and not reverse_stale(base, sid, rec):
            skipped.append((sid, '反向图已有且母版未变')); log(f'   {sid}: 反向图已有且母版未变,跳过'); continue
        render_reverse(base, sid, force=force, dry_run=dry_run, seed=seed, log=log)
        done.append(sid)
    return {'rendered': done, 'skipped': skipped}


# ---------------------------------------------------------------- 组 prompt 接线(scene_plate_bound)
def _is_scene_image(ref: str, sid: str) -> bool:
    """本场景目录下的图(旧版 main*/var*、正/反向图、退役俯视图/九宫格)与退役的动线标注图 directing/*/blocking_maps/ 都归本模块管理;
    分镜背景图 plates/ 属白模链,不动。"""
    if '/blocking_maps/' in ref:
        return True
    return ref.startswith(f'assets/concepts/scenes/{sid}/') and '/plates/' not in ref and ref.rsplit('.', 1)[-1].lower() in ('png', 'jpg', 'jpeg', 'webp')


def _clean(text) -> str:
    return re.sub(r'[()（）]', ' ', str(text or '')).strip()


def plan_group(base: Path, ep: str, gid: str) -> dict:
    ep, gid = component(ep), component(gid)
    sl = read(base / 'directing' / ep / 'shot_list.json', {}) or {}
    g = next((x for x in sl.get('generation_groups') or [] if x.get('group_id') == gid), None)
    if not g:
        return {'group': None, 'scene_id': None, 'shots': [], 'warnings': [f'{gid}: shot_list.json 里没有这个组'], 'rec': {}}
    sid = g.get('scene_id') or g.get('bible_scene')
    shots_all = {s.get('shot_id'): s for s in sl.get('shots') or []}
    if not sid:
        for s in g.get('shots') or []:
            sid = (shots_all.get(s) or {}).get('scene_id') or (shots_all.get(s) or {}).get('bible_scene')
            if sid:
                break
    warnings = []
    rec = load_scene_plates(base, sid) if sid else {}
    mode = effective_mode(base, rec) if rec else None
    front = plate_file(base, sid, rec, 'front') if rec else None
    reverse = plate_file(base, sid, rec, 'reverse') if rec else None
    stale = bool(reverse) and reverse_stale(base, sid, rec)
    if stale:
        warnings.append(f'{gid}: 场景 {sid} 反向图过期(正向图已重出),不挂;重跑 code/render_scene_plates.py --scene {sid}'); reverse = None
    shots = []
    for k, shot_id in enumerate(g.get('shots') or g.get('shot_ids') or [], 1):
        pv = str((shots_all.get(shot_id) or {}).get('plate_view') or '').strip().lower()
        view = pv if pv in VIEWS else 'front'
        if pv not in VIEWS:
            warnings.append(f'{gid}/{shot_id}: 未写 plate_view(按 front)')
        if view == 'reverse' and mode == 'single':
            warnings.append(f'{gid}/{shot_id}: plate_view=reverse 但场景为 single 模式,改用正向图'); view = 'front'
        if view == 'reverse' and not reverse:
            warnings.append(f'{gid}/{shot_id}: plate_view=reverse 但反向图未出,暂用正向图(跑 code/render_scene_plates.py)'); view = 'front'
        shots.append({'shot_id': shot_id, 'shot_no': k, 'view': view})
    if rec and rec.get('legacy'):
        warnings.append(f'{gid}: 场景 {sid} 只有旧版 main_01.png、无 scene_plates.json 登记(站位/画内清单为空)')
    use_reverse = reverse is not None and any(s['view'] == 'reverse' for s in shots)
    return {'group': g, 'scene_id': sid, 'rec': rec, 'mode': mode, 'shots': shots, 'warnings': warnings,
            'front': str(front.relative_to(base)) if front else None,
            'reverse': str(reverse.relative_to(base)) if use_reverse else None}


def _view_words(rec: dict, view: str) -> str:
    v = (rec or {}).get(view) or {}
    stand, look = _clean(v.get('standing_en')), _clean(v.get('looking_en'))
    if view == 'front':
        base_words = 'the front view'
    else:
        base_words = 'the reverse view'
    extra = ', '.join(x for x in (f'standing {stand}' if stand else '', f'looking {look}' if look else '') if x)
    return base_words + (f', {extra}' if extra else '')


def build_block(rec: dict, n_front: int, n_reverse: int | None) -> str:
    f = (rec or {}).get('front') or {}
    r = (rec or {}).get('reverse') or {}
    def clause(v, n, role):
        stand, look = _clean(v.get('standing_en')), _clean(v.get('looking_en'))
        s = f'[Image {n}] is the {role} view: an empty wide-angle photograph of this location with nobody in it'
        if stand or look:
            s += ' — ' + ', '.join(x for x in (f'standing {stand}' if stand else '', f'looking {look}' if look else '') if x)
        items = [str(x) for x in (v.get('in_frame_en') or []) if str(x).strip()]
        if items:
            s += '; in frame: ' + ', '.join(items)
        behind = [str(x) for x in (v.get('behind_en') or []) if str(x).strip()]
        if behind:
            s += '; behind that camera and not in it: ' + ', '.join(behind)
        return s + '.'
    text = BLOCK_KEY + ' ' + clause(f, n_front, 'front')
    if n_reverse:
        text += ' ' + clause(r, n_reverse, 'reverse') + ' They are two views of one and the same location.'
    text += (' Each Shot below names the plate it uses: take only the architecture, openings, furniture placement, materials and lighting '
             'from that plate; the shot is a tighter view inside or beside it — frame it exactly as the Shot text describes and place the '
             'characters where the position text says. Never render a plate as-is, never freeze on an empty set, and the video never shows the room from above.')
    return text


def shot_line(rec: dict, view: str, n_use: int, n_other: int | None) -> str:
    text = f'{LINE_KEY} this shot uses [Image {n_use}] ({_view_words(rec, view)})'
    if n_other:
        text += f' and not [Image {n_other}]'
    return text + '.'


def apply_prompt(prompt: dict, plan: dict) -> tuple[dict, list]:
    """幂等回写:剔除本场景旧图 refs,角色/生物 sheet 之后插入正向(+反向)图,重排 [Image N],写 Scene plates 段与逐镜句。"""
    out = copy.deepcopy(prompt)
    warns = list(plan.get('warnings') or [])
    sid = plan.get('scene_id')
    if not plan.get('front'):
        return out, warns
    old = [r for r in (out.get('refs') or []) if isinstance(r, str)]
    vp = out.get('video_prompt') or ''
    vp = _BLOCK_RE.sub(' ', vp)
    vp = _LINE_RE.sub('', vp)
    # 旧链路残留(俯视图/九宫格声明句、tile 引用、动线图图例)整句清掉,免得删了图号留下空悬句
    for rx in (_SPATIAL_RE, _GRID_RE, _MAPUSE_RE, _MAPONLY_RE, _TILE_RE, _MARKERS_RE):
        vp = rx.sub('', vp)
    vp = _ROUTE_MAP_RE.sub(' route:', vp)
    keep = [r for r in old if not _is_scene_image(r, sid)]
    cut = 0
    for i, r in enumerate(keep):
        if r.startswith('assets/concepts/characters/') or r.startswith('assets/concepts/creatures/'):
            cut = i + 1
    plates = [plan['front']] + ([plan['reverse']] if plan.get('reverse') else [])
    new = keep[:cut] + plates + keep[cut:]
    vp, w2 = _remap_images(vp, old, new)
    warns += w2
    n_front = new.index(plan['front']) + 1
    n_reverse = new.index(plan['reverse']) + 1 if plan.get('reverse') else None
    block = build_block(plan.get('rec') or {}, n_front, n_reverse)
    m = re.search(r'Shot\s*1\s*[:：｜|]', vp)
    vp = (vp[:m.start()].rstrip() + '\n\n' + block + '\n\n' + vp[m.start():]) if m else (vp.rstrip() + '\n\n' + block)
    after = vp.find(block) + len(block)
    for s in plan['shots']:
        head = re.compile(_SHOT_HEAD_RE % s['shot_no']).search(vp, after)
        if not head:
            warns.append(f"Shot {s['shot_no']} 段头没找到,未插 {LINE_KEY} 句")
            continue
        n_use = n_reverse if (s['view'] == 'reverse' and n_reverse) else n_front
        n_other = (n_front if n_use == n_reverse else n_reverse) if n_reverse else None
        vp = vp[:head.end()] + ' ' + shot_line(plan.get('rec') or {}, s['view'] if n_use != n_front else 'front', n_use, n_other) + vp[head.end():]
    if GC_KEY in vp and GC_EXTRA not in vp:
        vp = vp.rstrip()
        vp = (vp[:-1] if vp.endswith('.') else vp) + ', ' + GC_EXTRA + '.'
    vp = paragraphize(re.sub(r'[ \t]{2,}', ' ', vp))
    out['refs'] = new
    out['video_prompt'] = vp
    out['scene_plates'] = {'scene_id': sid, 'mode': plan.get('mode'), 'front': plan['front'], 'reverse': plan.get('reverse'),
                           'shots': [{'shot_id': s['shot_id'], 'view': s['view']} for s in plan['shots']], 'source': 'sync_scene_plates.v1'}
    notes = [n for n in (out.get('notes') or []) if not str(n).startswith('场景图自动接线(')]
    notes.append(f"场景图自动接线(code/sync_scene_plates.py):refs 挂正向图{'+反向图' if plan.get('reverse') else ''},逐镜 "
                 + ', '.join(f"Shot {s['shot_no']}→{s['view']}" for s in plan['shots']) + ';本场景旧概念图/俯视图移出 refs')
    out['notes'] = notes
    return out, warns


def check_prompt(prompt: dict, plan: dict, gid: str, strict: bool = False) -> tuple[list, list]:
    errs, warns = [], list(plan.get('warnings') or [])
    refs = [r for r in (prompt.get('refs') or []) if isinstance(r, str)]
    vp = prompt.get('video_prompt') or ''
    if not plan.get('front'):
        (errs if strict else warns).append(f"{gid}: 场景 {plan.get('scene_id')} 尚无正向场景图(environment-concept 出 main_01.png 并登记 scene_plates.json)")
        return errs, warns
    for view in ('front', 'reverse'):
        f = plan.get(view)
        if not f:
            continue
        if f not in refs:
            errs.append(f'{gid}: refs 未挂{"正向" if view == "front" else "反向"}场景图 {f}(跑 code/sync_scene_plates.py --write)')
            continue
        n = refs.index(f) + 1
        if not re.search(re.escape(f'[Image {n}] is the {view} view'), vp):
            errs.append(f'{gid}: 正文缺 {BLOCK_KEY} 段对 [Image {n}]({view})的说明句(跑 --write)')
    for r in refs:
        if plan.get('scene_id') and _is_scene_image(r, plan['scene_id']) and r not in (plan.get('front'), plan.get('reverse')):
            errs.append(f'{gid}: refs 含本场景其它图 {r}(旧版概念图/俯视图/九宫格;场景锚只挂正向/反向图,跑 --write 清理)')
    for s in plan['shots']:
        seg = re.search((_SHOT_HEAD_RE % s['shot_no']) + r'(.*?)(?=Shot\s*\d+\s*[:：｜|]|Global constraints:|$)', vp, re.S)
        if not seg or not re.search(re.escape(LINE_KEY) + r' this shot uses \[Image\s*\d+\]', seg.group(1)):
            errs.append(f"{gid}/{s['shot_id']}: Shot {s['shot_no']} 段缺「{LINE_KEY} this shot uses [Image N] …」句(跑 --write)")
    return errs, warns


def sync_group(base: Path, ep: str, gid: str, write: bool = False, strict: bool = False) -> dict:
    ep, gid = component(ep), component(gid)
    path = base / 'assets/prompts' / ep / f'{gid}.json'
    plan = plan_group(base, ep, gid)
    res = {'group_id': gid, 'scene_id': plan.get('scene_id'), 'front': plan.get('front'), 'reverse': plan.get('reverse'),
           'errors': [], 'warnings': [], 'updated': False}
    if not plan.get('group'):
        res['errors'] = plan['warnings']; return res
    if not path.is_file():
        res['warnings'].append(f'{gid}: 组 prompt 尚未产出({path.relative_to(base)}),待 p7-prompt'); return res
    prompt = read(path, {}) or {}
    if write and plan.get('front'):
        new, warns = apply_prompt(prompt, plan)
        if new != prompt:
            bak = base / 'directing' / ep / 'scene_plates_backups' / f'{gid}.json'
            if not bak.is_file():
                bak.parent.mkdir(parents=True, exist_ok=True)
                bak.write_text(json.dumps(prompt, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
            path.write_text(json.dumps(new, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
            res['updated'] = True
        prompt = new
        res['warnings'] += [w for w in warns if w not in plan['warnings']]
    errs, warns = check_prompt(prompt, plan, gid, strict)
    res['errors'] += errs
    res['warnings'] += [w for w in warns if w not in res['warnings']]
    return res


def sync_episode(base: Path, ep: str, groups=None, write: bool = False, strict: bool = False) -> dict:
    ep = component(ep)
    sl = read(base / 'directing' / ep / 'shot_list.json', {}) or {}
    gids = [g.get('group_id') for g in sl.get('generation_groups') or [] if g.get('group_id')]
    if groups:
        gids = [g for g in gids if g in groups]
    out = {'groups': [], 'errors': [], 'warnings': [], 'updated_prompts': []}
    for gid in gids:
        r = sync_group(base, ep, gid, write, strict)
        out['groups'].append(r)
        out['errors'] += r['errors']
        out['warnings'] += r['warnings']
        if r['updated']:
            out['updated_prompts'].append(gid)
    return out
