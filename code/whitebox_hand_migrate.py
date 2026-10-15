#!/usr/bin/env python3
"""白模手部通道左右口径迁移(#121)。

2026-10 之前渲染器把 right_hand 挂在人物局部 +X 肩(解剖学左肩),修正后按人物自身左右:面朝局部 +Z 时
right_hand 在 −X、left_hand 在 +X。存量数据里的 right_hand / left_hand 两个**键名**互换、坐标一字不动,
迁移后画面与迁移前完全一致(原先写 right_hand 的那条手臂本来就画在人物左肩,迁移后它叫 left_hand)。

默认只读(dry-run):逐集在临时影子目录里放迁移后的文件重新编译,核对 ①编译错误不增加 ②每个人物/群演/道具
= 原数据左右键名互换(几何不变)③迁移前有效的审查指纹、导演台批准、最新版本指纹迁移后仍有效,打印清单;
--apply 才写。

改写范围(宿主消费的白模数据,每集):
  directing/<ep>/whitebox_plans/<grp>.json        actors[]/scene_actors[]/extras[] 的 keyframes;顶层加
                                                  "hand_convention": "anatomical";迁移前有效的
                                                  cameras[].placement_fingerprint 按迁移后内容重打(v3)
  directing/<ep>/whitebox/director/overrides.json 导演台覆盖层 groups.*.actors/extras.*.keyframes(加同一标记)
  directing/<ep>/whitebox/director/versions/<grp>/v<N>.json  版本快照 group.actors/extras[].keyframes(A|B 对比用,
                                                  加同一标记;最新版本若与当前编译结果一致,其 sha 换成迁移后的)
  directing/<ep>/whitebox/director/notes.json     迁移前仍有效的「批准本组」与最新版本记录的组指纹换成迁移后的
  directing/<ep>/whitebox/episode.json            编译产物里 groups[].actors/extras[].keyframes 同样互换
不改(只计数列出):shots/*/blocking.json、shot_list.json、assets/prompts 等 Agent 写的源文件镜像
(whitebox_keyframes / whitebox_path / whitebox_staging…;blocking.json 改了会让机位契约 source 指纹失效),
以及 runs/、qa/、backups 等历史存档。白模视频因渲染器文件变化本就全部判过期,重导出后画面与旧视频相同。

幂等:迁移过的计划/快照/覆盖层带 hand_convention 标记;项目级台账 directing/whitebox_hand_migration.json 记录
已迁移文件,台账写入之后才新建或改过(mtime 更新)的文件视为新口径,一律跳过。改写前原文件备份到
qa/whitebox-hand121-<时间>/before/<原相对路径>(附 manifest.json);--rollback <该目录> 把未再改动过的文件原样恢复。

用法:
  python3 code/whitebox_hand_migrate.py                                   # 全部项目 dry-run,副本项目标注
  python3 code/whitebox_hand_migrate.py --project fengshen3 --project jidi --report /tmp/hand121.json
  python3 code/whitebox_hand_migrate.py --project fengshen3 --apply
  python3 code/whitebox_hand_migrate.py --all --apply                     # 全部项目(含副本)
  python3 code/whitebox_hand_migrate.py --project fengshen3 --rollback data/projects/fengshen3/qa/whitebox-hand121-20261010-120000
须在合并本修复、重启服务后立即迁移,迁移期间不要派白模调度单、不要在导演台改动。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import modules.whitebox as wb  # noqa: E402
from modules import director as dm  # noqa: E402
from modules import whitebox_camera as wcam  # noqa: E402

HAND_KEYS = ('left_hand', 'right_hand')
OTHER = {'left_hand': 'right_hand', 'right_hand': 'left_hand'}
LEGACY_SIDES = (('left_hand', -1), ('right_hand', 1))   # 2026-10 前的渲染口径:right_hand 挂 +X 肩
MARKER, CONVENTION = 'hand_convention', wb.HAND_CONVENTION
LEDGER_REL = 'directing/whitebox_hand_migration.json'
LEDGER_SCHEMA = 'whitebox_hand_migration.v1'
BACKUP_PREFIX = 'whitebox-hand121-'
INT, ANY = object(), object()
TRACK_KINDS = ('actors', 'scene_actors', 'extras')
PATTERNS = {
    'plan': [(TRACK_KINDS, INT, 'keyframes', INT, HAND_KEYS)],
    'overrides': [('groups', ANY, ('actors', 'extras'), ANY, 'keyframes', INT, HAND_KEYS)],
    'version': [('group', ('actors', 'extras'), INT, 'keyframes', INT, HAND_KEYS)],
    'episode': [('groups', INT, ('actors', 'extras'), INT, 'keyframes', INT, HAND_KEYS)],
}
GROUP_PAT = [(('actors', 'extras'), INT, 'keyframes', INT, HAND_KEYS)]
STAGING_PAT = PATTERNS['plan']
MIRROR_GLOBS = ('shots/*/blocking.json', 'shot_list.json')
WORKAROUND_RE = re.compile(r'解剖|镜像|mirror|渲染器|renderer', re.I)
RIGHT_TEXT_RE = re.compile(r'右手|right hand', re.I)
LEFT_TEXT_RE = re.compile(r'左手|left hand', re.I)

# ------------------------------------------------------------------ JSON 文本定位(保字节)
_STR = re.compile(r'"(?:[^"\\]|\\.)*"', re.S)
_SCALAR = re.compile(r'[^,\]\}\s]+')
_WS = re.compile(r'[ \t\r\n]*')


def scan(text: str) -> list:
    """逐个列出对象键与字符串值的位置:[(kind 'key'|'str', start, end, path)];path 为键/下标元组(键的 path 含该键)。"""
    tokens = []

    def ws(i):
        return _WS.match(text, i).end()

    def value(i, path):
        i = ws(i)
        c = text[i]
        if c == '{':
            i = ws(i + 1)
            if text[i] == '}':
                return i + 1
            while True:
                m = _STR.match(text, i)
                if not m:
                    raise ValueError(f'JSON 键位置 {i} 解析失败')
                key = json.loads(m.group())
                tokens.append(('key', m.start(), m.end(), path + (key,)))
                i = ws(m.end())
                if text[i] != ':':
                    raise ValueError(f'JSON 位置 {i} 缺冒号')
                i = ws(value(i + 1, path + (key,)))
                if text[i] == ',':
                    i = ws(i + 1)
                    continue
                if text[i] == '}':
                    return i + 1
                raise ValueError(f'JSON 位置 {i} 解析失败')
        if c == '[':
            i = ws(i + 1)
            if text[i] == ']':
                return i + 1
            k = 0
            while True:
                i = ws(value(i, path + (k,)))
                k += 1
                if text[i] == ',':
                    i += 1
                    continue
                if text[i] == ']':
                    return i + 1
                raise ValueError(f'JSON 位置 {i} 解析失败')
        if c == '"':
            m = _STR.match(text, i)
            tokens.append(('str', m.start(), m.end(), path))
            return m.end()
        m = _SCALAR.match(text, i)
        if not m:
            raise ValueError(f'JSON 位置 {i} 解析失败')
        return m.end()

    if ws(value(0, ())) != len(text):
        raise ValueError('JSON 末尾有多余内容')
    return tokens


def match(path, pattern) -> bool:
    if len(path) != len(pattern):
        return False
    for comp, pat in zip(path, pattern):
        if pat is INT:
            if not isinstance(comp, int):
                return False
        elif pat is ANY:
            if not isinstance(comp, str):
                return False
        elif isinstance(pat, tuple):
            if comp not in pat:
                return False
        elif comp != pat:
            return False
    return True


def apply_edits(text: str, edits: list) -> str:
    out, last = [], 0
    for start, end, repl in sorted(edits):
        if start < last:
            raise ValueError('重叠的改写区间')
        out += [text[last:start], repl]
        last = end
    return ''.join(out) + text[last:]


def swap_obj(obj, patterns, path=()):
    """语义层:按 patterns 把命中的 left_hand/right_hand 键名互换(校验用)。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            p = path + (k,)
            out[OTHER[k] if k in HAND_KEYS and any(match(p, pat) for pat in patterns) else k] = swap_obj(v, patterns, p)
        return out
    if isinstance(obj, list):
        return [swap_obj(v, patterns, path + (i,)) for i, v in enumerate(obj)]
    return obj


def canon(obj) -> str:
    """类型敏感的规范串(3.0 与 3 不同),判定迁移前后除键名外逐值一致。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def swap_text(text: str, patterns) -> tuple[str, dict]:
    """文本层互换键名:只改命中路径上的键字面量,其余字节原样。返回 (新文本, 统计)。"""
    edits, tracks = [], set()
    for kind, s, e, path in scan(text):
        if kind == 'key' and path[-1] in HAND_KEYS and any(match(path, p) for p in patterns):
            edits.append((s, e, json.dumps(OTHER[path[-1]])))
            tracks.add(path[:path.index('keyframes')])
    new = apply_edits(text, edits)
    if edits and canon(json.loads(new)) != canon(swap_obj(json.loads(text), patterns)):
        raise ValueError('键名互换校验失败')
    return new, {'keys': len(edits), 'tracks': len(tracks)}


def add_marker(text: str) -> str:
    """在顶层对象开头插入 "hand_convention": "anatomical",沿用原文件的缩进与分隔符写法。"""
    m = re.match(r'\{([ \t\r\n]*)', text)
    if not m or text[m.end()] == '}':
        raise ValueError('顶层不是非空 JSON 对象')
    first = _STR.match(text, m.end())
    colon = ': ' if text.startswith(': ', first.end()) else ':'
    ws = m.group(1)
    sep = (',' + ws) if '\n' in ws else (', ' if colon == ': ' else ',')
    new = text[:m.end()] + json.dumps(MARKER) + colon + json.dumps(CONVENTION) + sep + text[m.end():]
    old_obj = json.loads(text)
    if canon(json.loads(new)) != canon({**old_obj, MARKER: CONVENTION}):
        raise ValueError('标记插入校验失败')
    return new


def replace_str_values(text: str, path_pred, mapping: dict) -> tuple[str, int]:
    """把路径满足 path_pred 且值在 mapping 里的字符串值换成 mapping[值]。"""
    edits = []
    for kind, s, e, path in scan(text):
        if kind == 'str' and path_pred(path):
            v = json.loads(text[s:e])
            if v in mapping and mapping[v] != v:
                edits.append((s, e, json.dumps(mapping[v])))
    return apply_edits(text, edits), len(edits)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_text(p: Path) -> str:
    return p.read_bytes().decode('utf-8')   # 不经 universal newline,保留 CRLF 等原字节


# ------------------------------------------------------------------ 台账 / 跳过规则
def load_ledger(base: Path) -> dict:
    p = base / LEDGER_REL
    try:
        doc = json.loads(read_text(p))
        if doc.get('schema') == LEDGER_SCHEMA and isinstance(doc.get('files'), dict):
            return doc
    except (OSError, ValueError):
        pass
    return {}


def skip_reason(base: Path, rel: str, obj, ledger: dict) -> str | None:
    if isinstance(obj, dict) and obj.get(MARKER) == CONVENTION:
        return 'marked'
    if rel in ledger.get('files', {}):
        return 'ledger'
    ts = ledger.get('applied_ts')
    if ts and (base / rel).stat().st_mtime > ts:
        return 'newer'   # 台账写入之后才新建/改过 = 新口径数据
    return None


# ------------------------------------------------------------------ 影子目录编译
def build_shadow(root: Path, base: Path, files: dict) -> Path:
    """root/<项目名>:改动文件写实体,其余条目软链回原项目。files: {相对路径: bytes}。"""
    shadow = root / base.name
    need = {Path(r).parent for r in files}
    for r in list(need):
        need |= set(r.parents)
    need.discard(Path('.'))
    want = {Path(r): b for r, b in files.items()}

    def mk(rel: Path):
        dst = shadow / rel
        dst.mkdir(parents=True, exist_ok=True)
        for child in (base / rel).iterdir():
            crel = rel / child.name
            if crel in need and child.is_dir() and not child.is_symlink():
                mk(crel)
            elif crel in want:
                (dst / child.name).write_bytes(want[crel])
            else:
                (dst / child.name).symlink_to(child)
    mk(Path('.'))
    return shadow


def compile_with(base: Path, ep: str, sides) -> dict:
    keep = wb.HAND_SIDES
    wb.HAND_SIDES = sides
    try:
        return wb.compile_episode(base, ep)
    finally:
        wb.HAND_SIDES = keep


def swap_warning(text: str) -> str:
    return re.sub(r'left_hand|right_hand', lambda m: OTHER[m.group()], text)


def compare_compiles(pre: dict, post: dict) -> dict:
    pre_err = {e.get('group_id') for e in pre.get('errors', [])}
    post_err = {e.get('group_id') for e in post.get('errors', [])}
    pre_g = {g['group_id']: g for g in pre.get('groups', [])}
    post_g = {g['group_id']: g for g in post.get('groups', [])}
    geometry, warnings = [], []
    for gid in sorted(set(pre_g) & set(post_g)):
        a, b = pre_g[gid], post_g[gid]
        for kind in ('actors', 'extras', 'props'):
            if canon(swap_obj(a.get(kind), [(INT, 'keyframes', INT, HAND_KEYS)])) != canon(b.get(kind)):
                geometry.append(f'{gid}.{kind}')
        if sorted(swap_warning(w) for w in a.get('warnings', [])) != sorted(b.get('warnings', [])):
            warnings.append(gid)
    return {'new_errors': sorted(post_err - pre_err, key=str), 'fixed_errors': sorted(pre_err - post_err, key=str),
            'lost_groups': sorted(set(pre_g) - set(post_g)), 'geometry_mismatch': geometry,
            'warning_mismatch': warnings, 'groups': len(post_g), 'errors': len(post_err)}


# ------------------------------------------------------------------ 每集规划
def prose_flags(obj, pre_channels: set) -> dict:
    """计划散文:镜像绕行注释(迁移后自动变对,散文过时)、文字写右/左手但所用通道迁移后成了另一侧(需重调)。"""
    workaround, right, left = [], [], []

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, path + (k,))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, path + (i,))
        elif isinstance(o, str):
            where = '.'.join(map(str, path))
            if re.search(r'(left|right)_hand', o) and WORKAROUND_RE.search(o):
                workaround.append((where, o[:160]))
            else:
                if RIGHT_TEXT_RE.search(o):
                    right.append((where, o[:160]))
                if LEFT_TEXT_RE.search(o):
                    left.append((where, o[:160]))
    walk(obj, ())
    out = {}
    if workaround:
        out['workaround_prose'] = workaround
    if right and 'right_hand' in pre_channels and not workaround:
        out['text_right_hand_now_left_channel'] = right
    if left and 'left_hand' in pre_channels and not workaround:
        out['text_left_hand_now_right_channel'] = left
    return out


def channels(obj, patterns) -> set:
    found = set()

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                p = path + (k,)
                if k in HAND_KEYS and any(match(p, pat) for pat in patterns):
                    found.add(k)
                walk(v, p)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, path + (i,))
    walk(obj, ())
    return found


def count_mirror_keys(text: str) -> int:
    return len(re.findall(r'"(?:left|right)_hand"\s*:', text))


def plan_episode(base: Path, ep: str, ledger: dict) -> dict:
    ep_dir = base / 'directing' / ep
    res = {'ep': ep, 'files': [], 'skipped': {}, 'flags': {}, 'mirrors': {}, 'blocked': None,
           'placement': {'restamped': 0, 'stale_left': 0}, 'approvals': {'restamped': 0, 'stale_left': 0},
           'versions_sha': 0}
    changes = {}   # rel -> {'kind', 'before': bytes, 'after': str, ...}

    def note_skip(rel, why):
        res['skipped'][rel] = why

    # 1) 计划
    for p in sorted((ep_dir / 'whitebox_plans').glob('*.json')):
        rel = p.relative_to(base).as_posix()
        raw = p.read_bytes()
        try:
            text = raw.decode('utf-8')
            obj = json.loads(text)
        except ValueError as e:
            note_skip(rel, f'invalid-json: {e}')
            continue
        why = skip_reason(base, rel, obj, ledger)
        if why:
            note_skip(rel, why)
            continue
        new, stat = swap_text(text, PATTERNS['plan'])
        if not stat['keys']:
            continue
        gid = p.stem
        # 迁移前有效的放置指纹按迁移后内容重打;本已失效的不动(不替 Agent 漂白过期审查)
        fp_map, stale = {}, 0
        for cam in obj.get('cameras') or []:
            saved = cam.get('placement_fingerprint') if isinstance(cam, dict) else None
            if not isinstance(saved, str) or saved in fp_map:
                continue
            if wcam._matches_placement(saved, base, ep, gid):
                payload = wcam._placement_payload(base, ep, gid)
                payload['staging'] = swap_obj(payload['staging'], STAGING_PAT)
                fp_map[saved] = wcam._fingerprint(payload, prefix=wcam.PLACEMENT_PREFIX)
            else:
                stale += 1
        new, n_fp = replace_str_values(new, lambda path: match(path, ('cameras', INT, 'placement_fingerprint')), fp_map)
        new = add_marker(new)
        res['placement']['restamped'] += n_fp
        res['placement']['stale_left'] += stale
        pre_ch = channels(obj, PATTERNS['plan'])
        flags = prose_flags(obj, pre_ch)
        if flags:
            res['flags'][rel] = flags
        changes[rel] = {'kind': 'plan', 'before': raw, 'after': new, **stat, 'placement_restamped': n_fp,
                        'channels_before': sorted(pre_ch)}

    # 2) 导演台覆盖层
    ov = ep_dir / 'whitebox' / 'director' / 'overrides.json'
    if ov.is_file():
        rel = ov.relative_to(base).as_posix()
        raw = ov.read_bytes()
        obj = json.loads(raw.decode('utf-8'))
        why = skip_reason(base, rel, obj, ledger)
        if why:
            note_skip(rel, why)
        else:
            new, stat = swap_text(raw.decode('utf-8'), PATTERNS['overrides'])
            if stat['keys']:
                changes[rel] = {'kind': 'overrides', 'before': raw, 'after': add_marker(new), **stat}

    # 3) 版本快照(sha 稍后按编译结果补)
    for p in sorted((ep_dir / 'whitebox' / 'director' / 'versions').glob('*/v*.json')):
        rel = p.relative_to(base).as_posix()
        raw = p.read_bytes()
        try:
            obj = json.loads(raw.decode('utf-8'))
        except ValueError as e:
            note_skip(rel, f'invalid-json: {e}')
            continue
        why = skip_reason(base, rel, obj, ledger)
        if why:
            note_skip(rel, why)
            continue
        new, stat = swap_text(raw.decode('utf-8'), PATTERNS['version'])
        if stat['keys']:
            changes[rel] = {'kind': 'version', 'before': raw, 'after': add_marker(new), **stat,
                            'gid': obj.get('group_id') or p.parent.name, 'v': obj.get('v'), 'sha': obj.get('sha')}

    # 4) 编译产物 episode.json(只互换键名,其余原样;下次编译自然刷新)
    epj = ep_dir / 'whitebox' / 'episode.json'
    if epj.is_file():
        rel = epj.relative_to(base).as_posix()
        why = skip_reason(base, rel, None, ledger)
        if why:
            note_skip(rel, why)
        else:
            raw = epj.read_bytes()
            new, stat = swap_text(raw.decode('utf-8'), PATTERNS['episode'])
            if stat['keys']:
                changes[rel] = {'kind': 'episode', 'before': raw, 'after': new, **stat}

    # 镜像源文件只计数
    for pattern in MIRROR_GLOBS:
        for p in ep_dir.glob(pattern):
            n = count_mirror_keys(read_text(p))
            if n:
                res['mirrors'][p.relative_to(base).as_posix()] = n
    for p in (base / 'assets' / 'prompts' / ep).glob('*.json'):
        n = count_mirror_keys(read_text(p))
        if n:
            res['mirrors'][p.relative_to(base).as_posix()] = n

    if not any(c['kind'] in ('plan', 'overrides') for c in changes.values()):
        # 计划都不用迁:只剩快照/编译产物的,照样迁(它们只供显示),不涉指纹
        res['files'] = _file_rows(changes)
        res['_changes'] = changes
        return res

    # 5) 影子编译核对:迁移前按旧口径编译 vs 迁移后按新口径编译
    try:
        pre = compile_with(base, ep, LEGACY_SIDES)
    except (OSError, ValueError, KeyError) as e:
        res['blocked'] = f'迁移前编译失败:{e}'
        res['files'] = _file_rows(changes)
        res['_changes'] = changes
        return res
    shadow_files = {rel: c['after'].encode('utf-8') for rel, c in changes.items() if c['kind'] in ('plan', 'overrides')}
    with tempfile.TemporaryDirectory(prefix='hand121-') as tmp:
        shadow = build_shadow(Path(tmp), base, shadow_files)
        post = compile_with(shadow, ep, wb.HAND_SIDES)
    cmp = compare_compiles(pre, post)
    res['compile'] = cmp
    if cmp['new_errors'] or cmp['lost_groups'] or cmp['geometry_mismatch']:
        res['blocked'] = '迁移后编译与迁移前不一致:' + json.dumps(
            {k: cmp[k] for k in ('new_errors', 'lost_groups', 'geometry_mismatch') if cmp[k]}, ensure_ascii=False)

    # 6) 组指纹映射:导演台批准 / 最新版本
    pre_sha = {g['group_id']: dm.group_sha(pre, g) for g in pre.get('groups', []) if g.get('scene_id') in pre.get('scenes', {})}
    post_sha = {g['group_id']: dm.group_sha(post, g) for g in post.get('groups', []) if g.get('scene_id') in post.get('scenes', {})}
    sha_map = {pre_sha[g]: post_sha[g] for g in pre_sha if g in post_sha and pre_sha[g] != post_sha[g]}
    notes_p = ep_dir / 'whitebox' / 'director' / 'notes.json'
    if notes_p.is_file() and sha_map:
        rel = notes_p.relative_to(base).as_posix()
        raw = notes_p.read_bytes()
        doc = json.loads(raw.decode('utf-8'))
        latest = {}
        for gid, recs in (doc.get('versions') or {}).items():
            vs = sorted((r for r in recs if isinstance(r, dict)), key=lambda r: r.get('v', 0))
            if vs:
                latest[gid] = vs[-1].get('v')
        valid = {}   # 只换「迁移前对得上当前编译结果」的指纹,本已过期的不动
        for gid, rec in (doc.get('approvals') or {}).items():
            if not isinstance(rec, dict) or not rec.get('sha') or gid not in post_sha:
                continue
            if rec['sha'] != pre_sha.get(gid):
                res['approvals']['stale_left'] += 1
            elif rec['sha'] in sha_map:
                valid[('approvals', gid, 'sha')] = rec['sha']
                res['approvals']['restamped'] += 1
        for gid, recs in (doc.get('versions') or {}).items():
            for i, r in enumerate(recs):
                if isinstance(r, dict) and r.get('v') == latest.get(gid) and r.get('sha') == pre_sha.get(gid) and r.get('sha') in sha_map:
                    valid[('versions', gid, i, 'sha')] = r['sha']
        if valid:
            text = raw.decode('utf-8')
            new, n = replace_str_values(text, lambda path: path in valid, sha_map)
            changes[rel] = {'kind': 'notes', 'before': raw, 'after': new, 'keys': 0, 'tracks': 0, 'sha_restamped': n}
            res['versions_sha'] = sum(1 for k in valid if k[0] == 'versions')
        for rel_v, c in changes.items():
            if c['kind'] == 'version' and c.get('v') == latest.get(c['gid']) and c.get('sha') == pre_sha.get(c['gid']) \
                    and c.get('sha') in sha_map:
                c['after'], _ = replace_str_values(c['after'], lambda path: path == ('sha',), sha_map)
                c['sha_restamped'] = 1
    res['files'] = _file_rows(changes)
    res['_changes'] = changes
    return res


def _file_rows(changes: dict) -> list:
    return [{'file': rel, **{k: v for k, v in c.items() if k not in ('before', 'after')}} for rel, c in sorted(changes.items())]


# ------------------------------------------------------------------ 项目级
def episodes(base: Path) -> list:
    d = base / 'directing'
    return sorted(p.name for p in d.iterdir() if p.is_dir() and re.fullmatch(r'ep\d+', p.name)) if d.is_dir() else []


def plan_project(base: Path, only_eps=None) -> dict:
    ledger = load_ledger(base)
    out = {'project': base.name, 'ledger': bool(ledger), 'episodes': []}
    for ep in episodes(base):
        if only_eps and ep not in only_eps:
            continue
        r = plan_episode(base, ep, ledger)
        if r['files'] or r['skipped'] or r['mirrors'] or r['blocked']:
            out['episodes'].append(r)
    return out


def apply_project(base: Path, plan: dict, stamp: str) -> dict:
    backup = base / 'qa' / f'{BACKUP_PREFIX}{stamp}'
    manifest = {'schema': 'whitebox_hand_migration_backup.v1', 'project': base.name, 'created_at': stamp, 'files': {}}
    written, conflicts, blocked = [], [], []
    for r in plan['episodes']:
        if r['blocked']:
            blocked.append(r['ep'])
            continue
        for rel, c in r['_changes'].items():
            p = base / rel
            if p.read_bytes() != c['before']:
                conflicts.append(rel)   # 规划之后文件被改过:不覆盖
                continue
            dst = backup / 'before' / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(c['before'])
            data = c['after'].encode('utf-8')
            tmp = p.with_name(p.name + '.hand121.tmp')
            tmp.write_bytes(data)
            shutil.copymode(p, tmp)
            os.replace(tmp, p)
            manifest['files'][rel] = {'kind': c['kind'], 'sha256_before': sha256(c['before']), 'sha256_after': sha256(data)}
            written.append(rel)
    if written:
        (backup / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        ledger = load_ledger(base) or {'schema': LEDGER_SCHEMA, 'convention': CONVENTION, 'files': {}}
        ledger['files'].update({rel: {'kind': manifest['files'][rel]['kind'], 'backup': backup.relative_to(base).as_posix()}
                                for rel in written})
        ledger['applied_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        # 全部写完之后的时刻:此后才新建 / 再改的文件按新口径跳过
        ledger['applied_ts'] = max([time.time()] + [(base / rel).stat().st_mtime for rel in written])
        ledger['tool'] = 'code/whitebox_hand_migrate.py'
        lp = base / LEDGER_REL
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'written': written, 'conflicts': conflicts, 'blocked_episodes': blocked,
            'backup': backup.relative_to(base).as_posix() if written else None}


def rollback(base: Path, backup: Path) -> dict:
    manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))
    restored, conflicts = [], []
    for rel, info in manifest['files'].items():
        p = base / rel
        if not p.is_file() or sha256(p.read_bytes()) != info['sha256_after']:
            conflicts.append(rel)   # 迁移后又被改过:不覆盖,需人工处理
            continue
        p.write_bytes((backup / 'before' / rel).read_bytes())
        restored.append(rel)
    ledger = load_ledger(base)
    if ledger:
        for rel in restored:
            ledger['files'].pop(rel, None)
        lp = base / LEDGER_REL
        if ledger['files']:
            lp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding='utf-8')
        else:
            lp.unlink()
    return {'restored': restored, 'conflicts': conflicts}


# ------------------------------------------------------------------ 输出
def is_copy(name: str, names) -> bool:
    return any(o != name and name.startswith(o + '-') for o in names)


def summarize(proj: dict, copy: bool) -> dict:
    s = {'project': proj['project'], 'copy': copy, 'episodes': 0, 'plans': 0, 'tracks': 0, 'keys': 0,
         'overrides': 0, 'versions': 0, 'episode_json': 0, 'placement_restamped': 0, 'placement_stale_left': 0,
         'approvals_restamped': 0, 'approvals_stale_left': 0, 'skipped': 0, 'mirror_files': 0, 'mirror_keys': 0,
         'blocked': [], 'workaround': [], 'retune': []}
    for r in proj['episodes']:
        if r['files']:
            s['episodes'] += 1
        for f in r['files']:
            kind = f['kind']
            if kind == 'plan':
                s['plans'] += 1
                s['tracks'] += f['tracks']
                s['keys'] += f['keys']
            elif kind == 'overrides':
                s['overrides'] += 1
            elif kind == 'version':
                s['versions'] += 1
            elif kind == 'episode':
                s['episode_json'] += 1
        s['placement_restamped'] += r['placement']['restamped']
        s['placement_stale_left'] += r['placement']['stale_left']
        s['approvals_restamped'] += r['approvals']['restamped']
        s['approvals_stale_left'] += r['approvals']['stale_left']
        s['skipped'] += len(r['skipped'])
        s['mirror_files'] += len(r['mirrors'])
        s['mirror_keys'] += sum(r['mirrors'].values())
        if r['blocked']:
            s['blocked'].append(f"{r['ep']}: {r['blocked']}")
        for rel, fl in r['flags'].items():
            name = rel.split('/')[1] + '/' + Path(rel).stem
            if 'workaround_prose' in fl:
                s['workaround'].append(name)
            if 'text_right_hand_now_left_channel' in fl or 'text_left_hand_now_right_channel' in fl:
                s['retune'].append(name)
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-root', type=Path, default=None, help='projects 根目录(缺省 $VIDEOAGENTS_DATA_DIR/projects)')
    ap.add_argument('--project', action='append', default=[], help='只处理这些项目(可重复)')
    ap.add_argument('--ep', action='append', default=[], help='只处理这些集(可重复)')
    ap.add_argument('--all', action='store_true', help='--apply 时处理全部项目(含副本)')
    ap.add_argument('--apply', action='store_true', help='写入(缺省只读 dry-run)')
    ap.add_argument('--rollback', type=Path, help='按备份目录(qa/whitebox-hand121-*)恢复,须配 --project')
    ap.add_argument('--report', type=Path, help='把逐文件明细写成 JSON')
    args = ap.parse_args(argv)
    if args.data_root:
        root = args.data_root
    else:
        from _common import DATA_DIR
        root = DATA_DIR / 'projects'
    root = root.expanduser().resolve()
    names = sorted(p.name for p in root.iterdir() if p.is_dir() and (p / 'directing').is_dir())
    if args.rollback:
        if len(args.project) != 1:
            ap.error('--rollback 须指定且只指定一个 --project')
        out = rollback(root / args.project[0], args.rollback.resolve())
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1 if out['conflicts'] else 0
    if args.apply and not args.project and not args.all:
        ap.error('--apply 须用 --project 指定项目,或 --all 处理全部项目')
    targets = [n for n in names if not args.project or n in args.project]
    missing = set(args.project) - set(names)
    if missing:
        ap.error(f'找不到项目:{", ".join(sorted(missing))}')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    rows, report, rc = [], {'mode': 'apply' if args.apply else 'dry-run', 'projects': []}, 0
    for name in targets:
        base = root / name
        proj = plan_project(base, set(args.ep) or None)
        summary = summarize(proj, is_copy(name, names))
        entry = {'summary': summary, 'episodes': [{k: v for k, v in r.items() if k != '_changes'} for r in proj['episodes']]}
        if args.apply:
            entry['applied'] = apply_project(base, proj, stamp)
            if entry['applied']['conflicts'] or entry['applied']['blocked_episodes']:
                rc = 1
        elif summary['blocked']:
            rc = 1
        report['projects'].append(entry)
        if summary['plans'] or summary['versions'] or summary['episode_json'] or summary['overrides'] or summary['blocked'] \
                or summary['skipped'] or summary['mirror_files']:
            rows.append(summary)
    hdr = ('项目', '副本', '集', '计划', '轨', '键', '覆盖层', '快照', 'episode', '放置指纹重打/本已失效',
           '批准重打/本已过期', '已跳过', '镜像文件/键', '散文绕行', '需重调')
    print(('dry-run(未写任何文件)' if not args.apply else '已写入') + f',数据根 {root}')
    print(' | '.join(hdr))
    for s in rows:
        print(' | '.join(str(x) for x in (
            s['project'], '是' if s['copy'] else '', s['episodes'], s['plans'], s['tracks'], s['keys'], s['overrides'],
            s['versions'], s['episode_json'], f"{s['placement_restamped']}/{s['placement_stale_left']}",
            f"{s['approvals_restamped']}/{s['approvals_stale_left']}", s['skipped'],
            f"{s['mirror_files']}/{s['mirror_keys']}", len(s['workaround']), len(s['retune']))))
        for b in s['blocked']:
            print(f"    ! 阻断 {b}")
    for entry in report['projects']:
        a = entry.get('applied')
        if a and (a['written'] or a['conflicts'] or a['blocked_episodes']):
            print(f"{entry['summary']['project']}: 写入 {len(a['written'])} 个文件,备份 {a['backup']};"
                  f"冲突 {len(a['conflicts'])};阻断集 {a['blocked_episodes']}")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        print(f'明细:{args.report}')
    return rc


if __name__ == '__main__':
    sys.exit(main())
