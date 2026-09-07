"""Meter-based scene and deterministic shot-group timeline compiler (no GPU required).

Authoring contract: docs/whitebox.md. Existing layout/blocking/camera data remains
canonical; inferred legacy values are always reported, never written back to it.
"""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path

PALETTE = ['#e63946', '#1d78d8', '#2ea043', '#f59e0b', '#8e44ad', '#00acc1', '#e91e63', '#795548']
LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'


def component(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError(f'Invalid identifier: {value!r}')
    return value


def read(path, default=None):
    if not path.is_file():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding='utf-8'))


def number(value, name, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name}: expected finite number')
    if minimum is not None and value < minimum:
        raise ValueError(f'{name}: must be >= {minimum}')
    return value


def vector(value, name, size=3, positive=False):
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f'{name}: expected {size} numbers')
    return [number(x, name, 0.001 if positive else None) for x in value]


def xyz(xy, dimensions, y=0):
    x, z = vector(xy, 'xy', 2)
    return [(x - .5) * dimensions[0], y, (z - .5) * dimensions[2]]


def point(pt, landmarks, dimensions):
    if isinstance(pt, str):
        pt = {'landmark': pt}
    if not isinstance(pt, dict):
        raise ValueError('Position needs xy or landmark')
    if pt.get('landmark') and pt['landmark'] not in landmarks:
        raise ValueError(f"Unknown landmark {pt['landmark']}")
    xy = pt.get('xy', landmarks.get(pt.get('landmark'), {}).get('xy'))
    return xyz(xy, dimensions, pt.get('height_m', 0))


def load_scene(base: Path, sid: str):
    component(sid)
    layout = read(base / 'assets/concepts/scenes' / sid / 'layout.json')
    if not layout:
        raise FileNotFoundError(f'{sid}: missing scene layout.json')
    authored = read(base / 'bible/scenes' / sid / 'whitebox.json', {})
    dimensions = authored.get('dimensions_m') or layout.get('dimensions_m') or [20, 3, 12]
    vector(dimensions, 'dimensions_m', positive=True)
    warnings = []
    if not authored.get('dimensions_m') and not layout.get('dimensions_m'):
        warnings.append('场景尺寸暂按 20 × 12 m、层高 3 m 推断，请由场景建模 Agent 校准。')
    objects = authored.get('objects')
    if objects is None:
        warnings.append('几何体由布局地标推断；物体范围与高度需对照俯视图校准。')
        objects = []
        for lm in layout.get('landmarks', []):
            kind = lm.get('kind')
            if kind in ('direction', 'space', 'entrance', 'opening'):
                continue
            size = {'ground': [dimensions[0] * .8, .02, 2], 'path': [dimensions[0] * .8, .06, 1.2],
                    'boundary': [dimensions[0] * .35, 2.4, .2], 'building': [4, 3, 3],
                    'vegetation': [1.5, 2.5, 1.5], 'furniture': [1.2, .75, .7],
                    'structure': [.4, 2.8, .4]}.get(kind, [.4, .8, .4])
            objects.append({'id': lm['id'], 'shape': 'box', 'size_m': size,
                            'position': xyz(lm['xy'], dimensions, size[1] / 2)})
    seen = set()
    for obj in objects:
        if obj['id'] in seen:
            raise ValueError(f"Duplicate object {obj['id']}")
        seen.add(obj['id'])
        if obj.get('shape', 'box') not in ('box', 'sphere', 'cylinder'):
            raise ValueError(f"Unsupported primitive {obj['shape']}")
        vector(obj['size_m'], 'size_m', positive=True)
        if 'position' not in obj and 'xy' in obj:
            obj['position'] = xyz(obj['xy'], dimensions, obj.get('elevation_m', obj['size_m'][1]/2))
        vector(obj['position'], 'position')
        number(obj.get('yaw', 0), 'yaw')
    return {'schema_version': 'whitebox_scene.v1', 'scene_id': sid,
            'name': layout.get('scene_name', sid), 'units': 'meters',
            'dimensions_m': dimensions, 'objects': objects,
            'landmarks': layout.get('landmarks', []), 'views': layout.get('views', []),
            'layout_top': f'assets/concepts/scenes/{sid}/{layout.get("layout_top", "layout_top.png")}',
            'inferred': authored.get('inferred', not bool(authored)),
            'scale_basis': authored.get('scale_basis', 'layout.dimensions_m' if layout.get('dimensions_m') else 'legacy default'),
            'warnings': warnings}


def pose_from(text):
    if re.search(r'躺|卧|lying|lies|reclin', text, re.I):
        return 'lie'
    if re.search(r'坐|落座|seat|sitting|sits', text, re.I):
        return 'sit'
    return 'stand'


def sample(keys, t):
    if t <= keys[0]['t']:
        return copy.deepcopy(keys[0])
    for a, b in zip(keys, keys[1:]):
        if t < b['t']:
            u = (t-a['t'])/(b['t']-a['t'])
            if a.get('hold'):
                u = 0
            if a.get('easing') == 'smooth':
                u = u*u*(3-2*u)
            out = copy.deepcopy(a)
            for key in ('position', 'target'):
                if key in a:
                    out[key] = [x+(y-x)*u for x, y in zip(a[key], b[key])]
            for key in ('fov', 'yaw'):
                if key in a:
                    delta = b[key]-a[key]
                    if key == 'yaw':
                        delta = (delta+math.pi) % (2*math.pi)-math.pi
                    out[key] = a[key]+delta*u
            out['t'] = t
            return out
    return copy.deepcopy(keys[-1])


def validate_keys(keys, duration, camera=False):
    if not isinstance(keys, list) or not keys:
        raise ValueError('Empty keyframes')
    previous = -1
    for key in keys:
        t = number(key['t'], 'keyframe.t', 0)
        if t <= previous or t > duration + 1e-6:
            raise ValueError('Keyframe times must increase within duration')
        previous = t
        vector(key['position'], 'position')
        if camera:
            vector(key['target'], 'target')
            if math.dist(key['position'], key['target']) < .001:
                raise ValueError('Camera position equals target')
            if not 1 <= number(key['fov'], 'fov') <= 150:
                raise ValueError('fov must be 1..150 degrees')
        else:
            if key.get('pose', 'stand') not in ('stand', 'sit', 'lie'):
                raise ValueError('pose must be stand/sit/lie')
            number(key.get('yaw', 0), 'yaw')
    if abs(keys[0]['t']) > 1e-6 or abs(keys[-1]['t']-duration) > 1e-6:
        raise ValueError('Keyframes must cover 0..duration')


def compile_group(base, ep, group, shots, scene):
    gid = component(group['group_id'])
    duration = sum(number(shots[s]['duration_s'], 'duration_s', .001) for s in group['shots'])
    declared = group.get('total_duration_s', duration)
    if abs(declared-duration) > .05:
        raise ValueError(f'{gid}: group duration differs from shot durations')
    dims = scene['dimensions_m']; landmarks = {x['id']: x for x in scene['landmarks']}
    plan = read(base / 'directing' / ep / 'whitebox_plans' / f'{gid}.json', {})
    warnings = list(scene['warnings']); actors = []
    routes = (group.get('blocking_map') or {}).get('characters', [])
    docs = {s: read(base / 'directing' / ep / 'shots' / s / 'blocking.json', {}) for s in group['shots']}
    for index, route in enumerate(routes):
        pts = [route['start'], *(route.get('path') or []), route.get('end') or route['start']]
        positions = [point(p, landmarks, dims) for p in pts]
        times = [0] + [p.get('t', duration*i/(len(pts)-1)) if isinstance(p, dict) else duration*i/(len(pts)-1)
                       for i, p in enumerate(pts[1:-1], 1)] + [duration]
        cid = component(route['id']); posture = route.get('pose') or pose_from(route.get('route_en', ''))
        height = route.get('height_m', 1.4 if cid.startswith('CRE-') else 1.7)
        keys = []
        for i, (pos, t) in enumerate(zip(positions, times)):
            nxt = positions[min(i+1, len(positions)-1)]
            yaw = math.atan2(nxt[0]-pos[0], nxt[2]-pos[2]) if nxt != pos else (keys[-1]['yaw'] if keys else 0)
            keys.append({'t': t, 'position': pos, 'pose': posture, 'yaw': yaw})
        # Numeric per-shot anchors and beats override the legacy evenly spaced route.
        offset = 0; keyed = {k['t']: k for k in keys}; pose_events = {0: posture}
        for sid in group['shots']:
            sd = shots[sid]['duration_s']; doc = docs[sid]
            entry = next((x for x in doc.get('characters', [])+doc.get('creatures', []) if x.get('id') == cid), {})
            for field, t in [('xy_start', offset), ('xy_end', offset+sd)]:
                if entry.get(field) is not None:
                    keyed[t] = {**sample(keys, t), 't': t, 'position': xyz(entry[field], dims)}
            initial_pose = entry.get('pose') or pose_from(entry.get('start_pos', ''))
            if entry.get('pose') or re.search(r'坐|躺|卧|seat|sitting|lying', entry.get('start_pos', ''), re.I):
                keyed[offset] = {**keyed.get(offset, sample(keys, offset)), 't': offset, 'pose': initial_pose}
                pose_events[offset] = initial_pose
            for beat in entry.get('path', []) + entry.get('beats', []):
                if not isinstance(beat.get('t'), (float, int)) or not 0 <= beat['t'] <= sd:
                    continue
                t = offset+beat['t']; k = {**keyed.get(t, sample(keys, t)), 't': t}
                if beat.get('xy') is not None:
                    k['position'] = xyz(beat['xy'], dims)
                elif beat.get('landmark'):
                    k['position'] = point(beat, landmarks, dims)
                if beat.get('pose'):
                    k['pose'] = beat['pose']
                    pose_events[t] = beat['pose']
                if beat.get('xy') is not None or beat.get('landmark') or beat.get('pose'):
                    keyed[t] = k
            offset += sd
        keys = [keyed[t] for t in sorted(keyed)]
        for key in keys:
            key['pose'] = pose_events[max(t for t in pose_events if t <= key['t'])]
        actor = {'id': cid, 'label': route.get('label', cid), 'letter': LETTERS[index % 26],
                 'color': PALETTE[index % len(PALETTE)], 'kind': 'creature' if cid.startswith('CRE-') else 'person',
                 'size_m': route.get('size_m', [height*.28, height, height*.22]), 'keyframes': keys}
        actors.append(actor)
        if route.get('mounted'):
            actors.append({'id': component(route['mounted']), 'label': route['mounted'], 'letter': '',
                           'color': actor['color'], 'kind': 'creature', 'size_m': [0.65, 1.5, 2.1],
                           'rider': cid, 'keyframes': copy.deepcopy(keys)})
            for key in actor['keyframes']:
                key['position'][1] += 1.45
                key['pose'] = 'sit'
    warnings.append('存量人物身高、文字姿态及未标时动线按规约推断；精确节拍可在白模计划中覆盖。')
    cameras = []; offset = 0
    for sid in group['shots']:
        shot = shots[sid]; sd = shot['duration_s']
        cam = read(base / 'directing' / ep / 'shots' / sid / 'camera.json', {})
        view = next((v for v in scene['views'] if v.get('tile') == shot.get('view_tile')), None)
        if not view:
            if not scene['views']:
                raise ValueError(f'{sid}: missing layout views')
            view = scene['views'][0]
            warnings.append(f'{sid}: 缺少 view_tile，暂取布局的第一个机位，请校准。')
        pos = point(view['camera_from'], landmarks, dims)
        target = point(view['looking_at'], landmarks, dims)
        text = shot.get('camera_position', '')
        pos[1] = .25 if re.search(r'贴地|ground', text, re.I) else (dims[1]*.85 if re.search(r'高机位|俯拍|high', text, re.I) else 1.6)
        target[1] = .15 if pos[1] == .25 else 1.2
        lens = re.search(r'(\d+(?:\.\d+)?)\s*mm', text)
        fov = math.degrees(2*math.atan(24/(2*float(lens[1])))) if lens else 50
        # Layout views establish the axis; they are not calibrated lens positions.
        # Frame the shot's subjects along that axis when no numeric camera track exists.
        subjects = [a for a in actors if a['id'] in shot.get('characters', []) + shot.get('creatures', [])]
        if subjects and not cam.get('whitebox_keyframes'):
            centers = [sample(a['keyframes'], offset)['position'] for a in subjects]
            direction = [target[0]-pos[0], target[2]-pos[2]]
            length = math.hypot(*direction) or 1
            low = pos[1] == .25
            target = [sum(p[0] for p in centers)/len(centers), .16 if low else 1.2,
                      sum(p[2] for p in centers)/len(centers)]
            frame_height = {'ECU':.35,'CU':.7,'MCU':1.2,'MS':2.1,'MLS':2.8,'FS':3.4,'WS':5,'EWS':9}.get(shot.get('size_code'),3.4)
            spread = max((math.dist(a,b) for a in centers for b in centers),default=0)
            distance = max(frame_height,spread/1.4)/(2*math.tan(math.radians(fov/2)))
            pos = [target[0]-direction[0]/length*distance, pos[1], target[2]-direction[1]/length*distance]
            warnings.append(f'{sid}: 沿布局视轴按景别对准镜首主体，机距为推断值。')
        a = {'t': 0, 'position': pos, 'target': target, 'fov': fov}
        b = copy.deepcopy(a); b['t'] = sd
        move = cam.get('movement', 'static')
        delta = [target[i]-pos[i] for i in range(3)]
        if move in ('push_in', 'pull_out'):
            sign = .2 if move == 'push_in' else -.2
            b['position'] = [p+d*sign for p, d in zip(pos, delta)]
        elif move in ('follow', 'track_right', 'track_left'):
            if move == 'follow' and actors:
                start, end = sample(actors[0]['keyframes'], offset), sample(actors[0]['keyframes'], offset+sd)
                shift = [y-x for x, y in zip(start['position'], end['position'])]
            else:
                length = math.hypot(delta[0], delta[2]) or 1
                sign = 1 if move == 'track_right' else -1
                shift = [-delta[2]/length*sign, 0, delta[0]/length*sign]
            b['position'] = [x+y for x, y in zip(pos, shift)]
            b['target'] = [x+y for x, y in zip(target, shift)]
        elif move != 'static':
            warnings.append(f'{sid}: 运镜 {move} 需白模计划提供数值关键帧，暂按固定机位显示。')
        if move != 'static':
            warnings.append(f'{sid}: 运镜幅度由 {move} 推断，请校准数值轨迹。')
        cameras.append({'shot_id': sid, 'start': offset, 'duration_s': sd, 'movement': move,
                        'keyframes': cam.get('whitebox_keyframes') or [a, b]})
        offset += sd
    if 'actors' in plan:
        # Replace tracks while preserving the established letter/color assignment.
        overrides = {a['id']: a for a in plan['actors']}
        if len(overrides) != len(plan['actors']) or set(overrides) != {a['id'] for a in actors}:
            raise ValueError(f'{gid}: plan actor IDs must match blocking map (including mounts)')
        actors = [{**a, **{k:v for k,v in overrides[a['id']].items() if k not in ('color', 'letter', 'id')}} for a in actors]
    if 'cameras' in plan:
        cameras = plan['cameras']
    expected = set(group.get('characters_union', [])) | set(group.get('creatures_union', []))
    if expected - {a['id'] for a in actors}:
        raise ValueError(f'{gid}: cast missing from blocking map: {sorted(expected-{a["id"] for a in actors})}')
    for actor in actors:
        vector(actor['size_m'], 'actor.size_m', positive=True)
        validate_keys(actor['keyframes'], duration)
    cursor = 0
    if [c['shot_id'] for c in cameras] != group['shots']:
        raise ValueError(f'{gid}: cameras must match group shot order')
    for camera in cameras:
        sd = shots[camera['shot_id']]['duration_s']
        if abs(camera['start']-cursor) > 1e-6 or abs(camera['duration_s']-sd) > 1e-6:
            raise ValueError(f'{gid}: camera intervals must match shot timing')
        validate_keys(camera['keyframes'], sd, True); cursor += sd
    return {'schema_version': 'whitebox_group.v1', 'group_id': gid, 'scene_id': group['scene_id'],
            'scene_no': group.get('scene_no'), 'duration_s': duration, 'actors': actors, 'cameras': cameras,
            'continuity_from': group.get('continuity_from'), 'continuity': plan.get('continuity', {}),
            'warnings': warnings, 'authored': bool(plan)}


def compile_episode(base: Path, ep: str):
    component(ep)
    source = read(base / 'directing' / ep / 'shot_list.json')
    if not source:
        raise FileNotFoundError(f'{ep}: missing shot_list.json')
    shots = {s['shot_id']: s for s in source.get('shots', [])}
    scenes = {}; groups = []; errors = []; by_id = {}
    for raw in source.get('generation_groups', []):
        gid = raw.get('group_id', '?')
        try:
            sid = component(raw['scene_id'])
            if sid not in scenes:
                scenes[sid] = load_scene(base, sid)
            group = compile_group(base, ep, raw, shots, scenes[sid])
            prev = by_id.get(group['continuity_from'])
            policy = group['continuity']
            for key in ('actors', 'camera'):
                if policy.get(key, 'validate' if key == 'actors' else 'cut') not in ('cut', 'validate', 'inherit'):
                    raise ValueError('continuity policy must be cut/validate/inherit')
            if any(v == 'inherit' for v in policy.values()) and (not prev or prev['scene_id'] != sid or prev['scene_no'] != group['scene_no']):
                raise ValueError(f'{gid}: cannot inherit across scene/time boundaries or missing predecessor')
            if prev and prev['scene_id'] == sid and prev['scene_no'] == group['scene_no']:
                before = {a['id']: a for a in prev['actors']}
                for actor in group['actors']:
                    if actor['id'] not in before or policy.get('actors') == 'cut':
                        continue
                    last = before[actor['id']]['keyframes'][-1]; first = actor['keyframes'][0]
                    if policy.get('actors') == 'inherit':
                        actor['keyframes'][0] = {**copy.deepcopy(last), 't': 0}
                    elif math.dist(last['position'], first['position']) > .05 or last.get('pose') != first.get('pose'):
                        group['warnings'].append(f"{actor['id']}: 与 {prev['group_id']} 的位置/姿态不连续，请确认切换或继承。")
                last = prev['cameras'][-1]['keyframes'][-1]
                if policy.get('camera') == 'inherit':
                    group['cameras'][0]['keyframes'][0] = {**copy.deepcopy(last), 't': 0}
                elif policy.get('camera') == 'validate' and any(group['cameras'][0]['keyframes'][0][k] != last[k] for k in ('position', 'target', 'fov')):
                    group['warnings'].append(f"机位与 {prev['group_id']} 不连续。")
            by_id[gid] = group; groups.append(group)
        except (ValueError, KeyError, TypeError, FileNotFoundError) as error:
            errors.append({'group_id': gid, 'error': str(error)})
    return {'schema_version': 'whitebox_episode.v1', 'project': base.name, 'ep': ep,
            'scenes': scenes, 'groups': groups, 'errors': errors,
            'source_group_count': len(source.get('generation_groups', []))}
