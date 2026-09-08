"""Meter-based scene and deterministic shot-group timeline compiler (no GPU required).

Authoring contract: docs/whitebox.md. Existing layout/blocking/camera data remains
canonical; inferred legacy values are always reported, never written back to it.
"""
from __future__ import annotations

import copy
import json
import math
import re
from fractions import Fraction
from pathlib import Path

from modules.output_format import resolve_output
from modules.scene_cast import scene_cast_groups

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


def render_format(settings, width=None, height=None):
    """Keep camera, preview and encoded pixels at the project's native aspect."""
    aspect, _, _ = resolve_output(settings)
    try:
        a, b = (Fraction(x) for x in aspect.split(':'))
        if a <= 0 or b <= 0:
            raise ValueError()
        ratio = a / b
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError(f'Invalid project aspect ratio: {aspect}') from error
    a, b = ratio.numerator, ratio.denominator
    if width is None and height is None:
        scale = max(1, 960 // (2 * max(a, b)))
        width, height = 2 * a * scale, 2 * b * scale
    elif width is None or height is None:
        raise ValueError('Specify both width and height, or neither')
    if any(isinstance(v, bool) or not isinstance(v, int) or v % 2 or not 128 <= v <= 1920 for v in (width, height)):
        raise ValueError('Render dimensions must be even integers within 128..1920')
    if width * b != height * a:
        raise ValueError(f'Export dimensions must match project aspect {aspect}')
    return {'aspect_ratio': aspect, 'width': width, 'height': height}


def xyz(xy, dimensions, y=0):
    x, z = vector(xy, 'xy', 2)
    return [(x - .5) * dimensions[0], number(y, 'altitude_m'), (z - .5) * dimensions[2]]


def point(pt, landmarks, dimensions):
    if isinstance(pt, str):
        pt = {'landmark': pt}
    if not isinstance(pt, dict):
        raise ValueError('Position needs xy or landmark')
    if 'position' in pt:
        return vector(pt['position'], 'position')
    if pt.get('landmark') and pt['landmark'] not in landmarks:
        raise ValueError(f"Unknown landmark {pt['landmark']}")
    xy = pt.get('xy', landmarks.get(pt.get('landmark'), {}).get('xy'))
    return xyz(xy, dimensions, pt.get('altitude_m', pt.get('height_m', 0)))


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
            'render': render_format(read(base / 'settings.json', {})),
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
            for key in ('bend', 'pitch', 'roll'):
                if key in a or key in b:
                    out[key] = a.get(key, 0) + (b.get(key, 0)-a.get(key, 0))*u
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
            for axis in ('pitch', 'roll'):
                number(key.get(axis, 0), axis)
            if not 0 <= number(key.get('bend', 0), 'bend') <= math.pi/2:
                raise ValueError('bend must be 0..pi/2 radians')
            if 'visible' in key and not isinstance(key['visible'], bool):
                raise ValueError('visible must be a boolean')
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
            for suffix, t in [('start', offset), ('end', offset+sd)]:
                key = {**sample(keys, t), 't': t}
                if entry.get(f'position_{suffix}') is not None:
                    key['position'] = vector(entry[f'position_{suffix}'], 'position')
                else:
                    if entry.get(f'xy_{suffix}') is not None:
                        key['position'] = xyz(entry[f'xy_{suffix}'], dims, key['position'][1])
                    if entry.get(f'altitude_{suffix}_m') is not None:
                        key['position'][1] = number(entry[f'altitude_{suffix}_m'], 'altitude_m')
                if any(field in entry for field in (f'position_{suffix}', f'xy_{suffix}', f'altitude_{suffix}_m')):
                    keyed[t] = key
            initial_pose = entry.get('pose') or pose_from(entry.get('start_pos', ''))
            if entry.get('pose') or re.search(r'坐|躺|卧|seat|sitting|lying', entry.get('start_pos', ''), re.I):
                keyed[offset] = {**keyed.get(offset, sample(keys, offset)), 't': offset, 'pose': initial_pose}
                pose_events[offset] = initial_pose
            for beat in entry.get('path', []) + entry.get('beats', []):
                if not isinstance(beat.get('t'), (float, int)) or not 0 <= beat['t'] <= sd:
                    continue
                t = offset+beat['t']; k = {**keyed.get(t, sample([keyed[x] for x in sorted(keyed)], t)), 't': t}
                if beat.get('position') is not None:
                    k['position'] = vector(beat['position'], 'position')
                elif beat.get('xy') is not None:
                    k['position'] = xyz(beat['xy'], dims, k['position'][1])
                elif beat.get('landmark'):
                    k['position'] = point({**beat, 'altitude_m': k['position'][1]}, landmarks, dims)
                if 'altitude_m' in beat or 'height_m' in beat:
                    k['position'] = list(k['position'])
                    k['position'][1] = number(beat.get('altitude_m', beat.get('height_m')), 'altitude_m')
                if beat.get('pose'):
                    k['pose'] = beat['pose']
                    pose_events[t] = beat['pose']
                if any(field in beat for field in ('position', 'xy', 'landmark', 'altitude_m', 'height_m', 'pose')):
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
    if 'actors' not in plan:
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
            altitude = sum(p[1] for p in centers)/len(centers)
            pos[1] += altitude
            target = [sum(p[0] for p in centers)/len(centers), altitude + (.16 if low else 1.2),
                      sum(p[2] for p in centers)/len(centers)]
            frame_height = {'ECU':.35,'CU':.7,'MCU':1.2,'MS':2.1,'MLS':2.8,'FS':3.4,'WS':5,'EWS':9}.get(shot.get('size_code'),3.4)
            spread = max((math.dist(a,b) for a in centers for b in centers),default=0)
            fmt = scene['render']
            distance = max(frame_height,spread/(fmt['width']/fmt['height'])*1.2)/(2*math.tan(math.radians(fov/2)))
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
    # Scene occupants are separate from shot subjects / blocking-map letters.
    for actor in plan.get('scene_actors', []):
        if actor['id'] not in group.get('scene_cast', []) or actor['id'] in {a['id'] for a in actors}:
            raise ValueError('scene_actors must be unique scene cast outside the blocking-map cast')
        actors.append(copy.deepcopy(actor))
    if 'cameras' in plan:
        cameras = plan['cameras']
        warnings = [w for w in warnings if not any(w.startswith(s+':') for s in group['shots'])]
    warnings.extend(plan.get('warnings', []))
    expected = set(group.get('characters_union', [])) | set(group.get('creatures_union', []))
    if expected - {a['id'] for a in actors}:
        raise ValueError(f'{gid}: cast missing from blocking map: {sorted(expected-{a["id"] for a in actors})}')
    # Background performers remain separate from the registered cast. They are
    # needed for shots whose only subjects are unnamed passengers, for example.
    extras = plan.get('extras', [])
    seen = {a['id'] for a in actors}
    for extra in extras:
        eid = component(extra['id'])
        if not eid.startswith('EXTRA-') or eid in seen:
            raise ValueError('Extra IDs must be unique EXTRA- identifiers outside the cast')
        seen.add(eid)
        if extra.get('kind', 'person') not in ('person', 'creature'):
            raise ValueError('Extra kind must be person/creature')
    for actor in actors + extras:
        vector(actor['size_m'], 'actor.size_m', positive=True)
        validate_keys(actor['keyframes'], duration)
    props = plan.get('props', [])
    prop_ids = set()
    for prop in props:
        pid = component(prop['id'])
        if pid in prop_ids or prop.get('shape', 'box') not in ('box', 'sphere', 'cylinder'):
            raise ValueError('Props need unique IDs and supported primitive shapes')
        prop_ids.add(pid)
        vector(prop['size_m'], 'prop.size_m', positive=True)
        vector(prop['position'], 'prop.position')
        number(prop.get('yaw', 0), 'prop.yaw')
        for axis in ('pitch', 'roll'):
            number(prop.get(axis, 0), 'prop.'+axis)
        if set(prop.get('shot_ids', [])) - set(group['shots']):
            raise ValueError('Prop shot_ids must belong to the group')
        if prop.get('keyframes') is not None:
            validate_keys(prop['keyframes'], duration)
    cursor = 0
    if [c['shot_id'] for c in cameras] != group['shots']:
        raise ValueError(f'{gid}: cameras must match group shot order')
    for camera in cameras:
        sd = shots[camera['shot_id']]['duration_s']
        if 'visible_actor_ids' in camera:
            visible_ids = camera['visible_actor_ids']
            if not isinstance(visible_ids, list) or any(not isinstance(cid, str) for cid in visible_ids) or set(visible_ids) - seen - set(group.get('scene_cast', [])):
                raise ValueError('visible_actor_ids must reference actors or extras in the group')
            if group.get('scene_cast_enabled') and not str(camera.get('visibility_override_reason') or '').strip():
                camera.pop('visible_actor_ids')
                warnings.append(f"{camera['shot_id']}: 已忽略无明确原因的人物过滤，按实际机位显示在场人物。")
        if abs(camera['start']-cursor) > 1e-6 or abs(camera['duration_s']-sd) > 1e-6:
            raise ValueError(f'{gid}: camera intervals must match shot timing')
        validate_keys(camera['keyframes'], sd, True); cursor += sd
    return {'schema_version': 'whitebox_group.v1', 'group_id': gid, 'scene_id': group['scene_id'],
            'scene_no': group.get('scene_no'), 'duration_s': duration, 'actors': actors, 'extras': extras,
            'props': props, 'cameras': cameras,
            'continuity_from': group.get('continuity_from'), 'continuity': plan.get('continuity', {}),
            'warnings': warnings, 'authored': bool(plan)}


def complete_scene_actors(groups, contexts, raw_groups, errors):
    # Snapshot authored tracks before filling holes so a later authored entrance
    # or departure, rather than a synthetic placeholder, remains authoritative.
    anchors = {}
    for i, group in enumerate(groups):
        context = contexts[group['group_id']]
        key = context['key']
        for actor in group['actors']:
            presence = context.get('presence', {}).get(actor['id'])
            if presence:
                if presence['state'] != 'present' or (actor.get('scene_inherited_from')
                        and all(frame.get('visible') is False for frame in actor['keyframes'])):
                    for frame in actor['keyframes']:
                        frame['visible'] = presence['state'] == 'present'
                actor['presence'] = copy.deepcopy(presence)
            anchors.setdefault((key, actor['id']), []).append((i, copy.deepcopy(actor), group['group_id']))
    for i, group in enumerate(groups):
        gid = group['group_id']; context = contexts[gid]
        group['scene_cast'] = context['actor_ids']
        present = {a['id'] for a in group['actors']}
        colors = {a.get('color') for a in group['actors']}
        for cid in context['actor_ids']:
            if cid in present:
                continue
            candidates = anchors.get((context['key'], cid), [])
            if not candidates:
                if context.get('presence', {}).get(cid, {}).get('state') in ('absent', 'remote'):
                    continue  # An absent body needs no invented spatial anchor.
                errors.append({'group_id': gid, 'error': f'{cid}: 同场次缺少空间锚点，请补 scene_actors'})
                continue
            previous = [entry for entry in candidates if entry[0] < i]
            index, actor, origin = previous[-1] if previous else candidates[0]
            actor = copy.deepcopy(actor)
            anchor = copy.deepcopy(actor['keyframes'][-1 if index < i else 0])
            actor['keyframes'] = [{**copy.deepcopy(anchor), 't': t} for t in (0, group['duration_s'])]
            actor['letter'] = ''
            actor['color'] = next((color for color in PALETTE if color not in colors), actor.get('color', PALETTE[0]))
            colors.add(actor['color'])
            actor['scene_inherited_from'] = origin
            group['actors'].append(actor)
            group['warnings'].append(f'{cid}: 同场次在场人物，沿用 {origin} 的'+('尾' if index < i else '首')+'姿态与位置；补充走位可写 scene_actors。')
        for cid, presence in (raw_groups[gid].get('scene_presence') or {}).items():
            if cid not in context['actor_ids'] or not isinstance(presence, dict) or presence.get('state') not in ('absent', 'remote', 'present') or not presence.get('reason'):
                errors.append({'group_id': gid, 'error': f'{cid}: scene_presence requires a scene actor, state and reason'})
        for cid, presence in context.get('presence', {}).items():
            for actor in group['actors']:
                if actor['id'] == cid:
                    if presence['state'] != 'present' or (actor.get('scene_inherited_from')
                            and all(frame.get('visible') is False for frame in actor['keyframes'])):
                        for key in actor['keyframes']:
                            key['visible'] = presence['state'] == 'present'
                    actor['presence'] = copy.deepcopy(presence)


def compile_episode(base: Path, ep: str):
    component(ep)
    source = read(base / 'directing' / ep / 'shot_list.json')
    if not source:
        raise FileNotFoundError(f'{ep}: missing shot_list.json')
    shots = {s['shot_id']: s for s in source.get('shots', [])}
    contexts = scene_cast_groups(source)
    raw_groups = {g['group_id']: g for g in source.get('generation_groups', [])}
    scenes = {}; groups = []; errors = []; by_id = {}
    for raw in source.get('generation_groups', []):
        gid = raw.get('group_id', '?')
        try:
            sid = component(raw['scene_id'])
            if sid not in scenes:
                scenes[sid] = load_scene(base, sid)
            group = compile_group(base, ep, {**raw, 'scene_cast': contexts[gid]['actor_ids'],
                                  'scene_cast_enabled': 'scene_cast' in raw}, shots, scenes[sid])
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
    complete_scene_actors(groups, contexts, raw_groups, errors)
    return {'schema_version': 'whitebox_episode.v1', 'staging_version': 3, 'project': base.name, 'ep': ep,
            'render': render_format(read(base / 'settings.json', {})),
            'scenes': scenes, 'groups': groups, 'errors': errors,
            'source_group_count': len(source.get('generation_groups', []))}
