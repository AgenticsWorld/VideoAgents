"""Reviewed camera contracts: invalidate stale staging and reject camera drift.

Contracts are authored from the shot/camera/composition intent, never inferred
from the trajectory being checked. Legacy plans remain readable until reviewed.
"""
import hashlib
import json
import math
from pathlib import Path


def source_fingerprint(base, ep, shot):
    def read(name):
        p = Path(base)/'directing'/ep/'shots'/shot['shot_id']/f'{name}.json'
        data = json.loads(p.read_text()) if p.is_file() else {}
        return {k: v for k, v in data.items() if not k.startswith('whitebox_')
                or k in ('whitebox_contract', 'whitebox_spatial_resolution', 'whitebox_lens_resolution')}
    source = {'shot': {k: shot.get(k) for k in ('shot_id', 'duration_s', 'size', 'camera', 'camera_position', 'view_tile')},
              'camera': read('camera'), 'composition': read('composition'), 'blocking': read('blocking')}
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def placement_fingerprint(base, ep, gid):
    def read(rel):
        p = Path(base)/rel
        return json.loads(p.read_text()) if p.is_file() else {}
    source = read(f'directing/{ep}/shot_list.json')
    group = next((g for g in source.get('generation_groups', []) if g['group_id'] == gid), {})
    plan = read(f'directing/{ep}/whitebox_plans/{gid}.json')
    payload = {'group': {k: group.get(k) for k in ('scene_id', 'scene_no', 'blocking_map', 'scene_presence')},
               'staging': {k: plan.get(k) for k in ('actors', 'scene_actors', 'extras', 'props')},
               'scene': read(f'bible/scenes/{group.get("scene_id")}/whitebox.json')}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def direction(key):
    d = [b-a for a, b in zip(key['position'], key['target'])]
    length = math.sqrt(sum(x*x for x in d))
    if length < 1e-8:
        raise ValueError('camera_contract: position and target coincide')
    return [x/length for x in d]


def check_camera(base, ep, shot, camera, group_id=None):
    p = Path(base)/'directing'/ep/'shots'/shot['shot_id']/'camera.json'
    source = json.loads(p.read_text()) if p.is_file() else {}
    contract = source.get('whitebox_contract')
    if not contract:
        return []
    if not isinstance(contract, dict):
        return [f'{shot["shot_id"]}: camera_contract: expected an object']
    sid = shot['shot_id']; errors = []
    def fail(message):
        errors.append(f'{sid}: camera_contract: {message}')
    if camera.get('source_fingerprint') != source_fingerprint(base, ep, shot):
        fail('source changed; review staging against camera/composition/blocking again')
    if group_id and camera.get('placement_fingerprint') != placement_fingerprint(base, ep, group_id):
        fail('actor/prop/scene placement changed; review framing and occlusion again')
    movement = source.get('movement') or shot.get('camera', {}).get('movement', 'static')
    if camera.get('movement') != movement:
        fail(f'movement must be {movement}')
    keys = camera['keyframes']; first = keys[0]
    if 'origin_m' in contract and math.dist(first['position'], contract['origin_m']) > contract.get('origin_tolerance_m', .02):
        fail('camera origin differs from reviewed placement')
    if 'view_axis' in contract:
        axis = contract['view_axis']; norm = math.sqrt(sum(x*x for x in axis))
        if not math.isfinite(norm) or norm < 1e-8:
            fail('view axis must be a nonzero finite vector')
            return errors
        dot = sum(a*b/norm for a, b in zip(direction(first), axis))
        if math.degrees(math.acos(max(-1, min(1, dot)))) > contract.get('axis_tolerance_deg', .5):
            fail('camera view axis differs from reviewed framing')
    focal = shot.get('camera', {}).get('focal_mm')
    if isinstance(focal, (int, float)) and focal > 0:
        fov = math.degrees(2*math.atan(24/(2*focal)))
        if any(abs(k['fov']-fov) > .01 for k in keys):
            fail(f'FOV must preserve {focal}mm / 24mm vertical gate')
    if movement == 'static' and any(math.dist(k['position'], first['position']) > 1e-5 or
            math.dist(direction(k), direction(first)) > 1e-5 for k in keys):
        fail('locked camera translated or rotated')
    for k in keys:
        if 'height_m' in contract and not contract['height_m'][0] <= k['position'][1] <= contract['height_m'][1]:
            fail('camera height outside reviewed eye-level/low-angle range'); break
        pitch = math.degrees(math.asin(direction(k)[1]))
        if 'pitch_deg' in contract and not contract['pitch_deg'][0] <= pitch <= contract['pitch_deg'][1]:
            fail('view pitch outside reviewed range'); break
    if contract.get('fixed_axis') and any(math.dist(direction(k), direction(first)) > 1e-5 for k in keys):
        fail('optical axis changed during single-axis translation')
    track = contract.get('translation')
    if track:
        axis = track['axis']; length = math.sqrt(sum(x*x for x in axis))
        if not math.isfinite(length) or length < 1e-8:
            fail('translation axis must be a nonzero finite vector')
            return errors
        axis = [x/length for x in axis]
        offsets = track['offsets']
        if (len(offsets) < 2 or offsets[0][0] != 0 or abs(offsets[-1][0]-camera['duration_s']) > 1e-6
                or any(b[0] <= a[0] for a, b in zip(offsets, offsets[1:]))):
            fail('translation offsets must increase and cover the shot duration')
            return errors
        if any(k.get('easing', 'linear') not in ('linear', None) for k in keys):
            fail('nonlinear easing violates constant-speed segments')
        # Sample authored segment boundaries and trajectory keys, plus interiors;
        # include both sides of a boundary to catch erroneous holds/discontinuities.
        from modules.whitebox import sample
        times = sorted(set([k['t'] for k in keys]+[o[0] for o in offsets]+[i*camera['duration_s']/120 for i in range(121)]))
        for t in times:
            j = next((i for i in range(len(offsets)-1) if t <= offsets[i+1][0]), len(offsets)-2)
            a, b = offsets[j:j+2]
            alpha = (t-a[0])/(b[0]-a[0])
            offset = a[1]+(b[1]-a[1])*alpha
            expected = [v+offset*x for v, x in zip(first['position'], axis)]
            if math.dist(sample(keys, t)['position'], expected) > 1e-4:
                fail(f'translation distance/axis/phase differs at {t:.3f}s'); break
    return errors


def check_matches(base, ep, groups):
    cameras = {c['shot_id']: (g, c) for g in groups for c in g['cameras']}
    errors = []
    for sid, (group, camera) in cameras.items():
        p = Path(base)/'directing'/ep/'shots'/sid/'camera.json'
        source = json.loads(p.read_text()) if p.is_file() else {}
        match = source.get('whitebox_contract', {}).get('match')
        if not match:
            continue
        peer = cameras.get(match['shot_id'])
        if not peer:
            errors.append({'group_id': group['group_id'], 'error': f'{sid}: camera_contract: missing matched shot {match["shot_id"]}'})
            continue
        a, b = camera['keyframes'][0], peer[1]['keyframes'][0]
        same = math.dist(direction(a), direction(b)) < 1e-5
        if match.get('mode', 'identical') == 'identical':
            same = same and math.dist(a['position'], b['position']) < 1e-5 and abs(a['fov']-b['fov']) < .01
        if not same:
            errors.append({'group_id': group['group_id'], 'error': f'{sid}: camera_contract: {match.get("mode", "identical")} mismatch with {match["shot_id"]}'})
    return errors
