#!/usr/bin/env python3
"""camera_view_check.py — 镜级画面视角站位机检(camera_view_consistent,2026-09-03)。

背景:俯视动线图 / 组级 blocking_map 是**导演台视角**(地图坐标、罗盘方位),视频模型看到的是**画面**。
prompt 每镜的"空间位置"要素逐字来自 blocking.json 的 `space_fragment_en`(机检 blocking_bound);
2026-09-03 起该片段改为**画面视角站位句**:按本镜机位(shot_list 该镜 `view_tile` → 场景 layout.json
`views[tile].camera_from → looking_at` 两地标连线,辅以 `camera_position` 文字)重新描述角色在当前画面中的
位置与关系——画幅侧(画左/画中/画右)、景深层(前景/中景/背景)、对镜朝向、与同框人物的相对位置,外加一个
逐字地标词作固定参照;**不写罗盘方位(东/南/西/北)**——那是俯视图视角;同时必须与导演台站位一致。
组级不变量(所在区域/固定参照物/身体朝向/相邻人物/不能改变的位置关系)在 shot_list 组 `blocking_map.station_table`
(机检 station_table_ok,`render_blocking_map.py` 内置),本脚本不查。

规则:
  ① 每入画角色 blocking.json 条目带结构化 `frame_position`:
       {"side": left|center|right|offscreen, "depth": foreground|midground|background,
        "facing_camera": toward|away|left|right|three_quarter_toward|three_quarter_away}
     存量项目缺 frame_position 按 WARN(回派 blocking 补写),--strict 按 FAIL;缺/非法枚举值 = FAIL;
  ② `space_fragment_en` 去掉该场景 layout.json 地标词(name_en)后不得含罗盘方位词
     (东侧/南墙/向北/西南…/north/south/east/west);须含与 frame_position.side 一致的画幅侧词
     (画左/画右/画中/画外 或 screen-left/screen-right/center/off-screen 等);
  ③ 几何一致(画面描述 vs 导演台站位):机位向量 = views[view_tile] 的 camera_from → looking_at;
     角色本镜位置 = 组 blocking_map 该角色 start→path→end 折线按本镜在组内的**时长占比**取镜首/镜尾两点
     (blocking.json 条目可用 `xy_start`/`xy_end` 归一化坐标覆盖);叉积定左右、点积定纵深:
       - frame_position.side 与几何相反(离轴 >8°)= FAIL;几何离轴 >25° 却写 center = WARN;
       - 角色在机位背后而 side 未写 offscreen = WARN;
       - 同镜两角色 depth 层次与几何纵深相反(前后差 >5% 画幅)= FAIL;
     view_tile 为空 / 场景无布局包 / blocking_map 缺该角色 = 跳过几何项并 WARN。
  ④ 项目输出设置「人物精确空间位置」关闭时整体 skipped(与 layout_map_bound 同口径)。

用法:python3 code/camera_view_check.py --project <slug> --ep ep01               # 全集
     python3 code/camera_view_check.py --project <slug> --ep ep01 sh030 sh031    # 只查指定镜
     python3 code/camera_view_check.py --project <slug> --ep ep01 --strict       # 缺 frame_position 也按违规
退出码:0=通过(可含 WARN),1=有违规。blocking 批产出后全批跑;prompt 写组前对该组各镜复核。
"""
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args, spatial_blocking_enabled  # noqa: E402

SIDES = ("left", "center", "right", "offscreen")
DEPTHS = ("foreground", "midground", "background")
FACINGS = ("toward", "away", "left", "right", "three_quarter_toward", "three_quarter_away")
DEPTH_RANK = {"foreground": 0, "midground": 1, "background": 2}

SIDE_WORDS = {
    "left": ("画左", "画面左", "画幅左", "左三分", "左侧画", "screen-left", "screen left", "frame-left", "frame left",
             "left of frame", "left third", "on the left"),
    "right": ("画右", "画面右", "画幅右", "右三分", "右侧画", "screen-right", "screen right", "frame-right", "frame right",
              "right of frame", "right third", "on the right"),
    "center": ("画中", "画幅中", "画面中", "居中", "正中", "中轴", "center of frame", "centre of frame", "centered",
               "centred", "middle of the frame", "dead center", "on the center line"),
    "offscreen": ("画外", "出画", "框外", "不入画", "off-screen", "offscreen", "out of frame", "out of shot"),
}
# 罗盘方位(俯视图视角)——去掉地标词后不得出现;"东西"(物件)单独放行
_COMPASS_CN = re.compile(r"[向朝靠偏往自从在]?[东南西北]{2}|[东南西北][侧面方边端墙角头部行走去望]|[向朝靠偏往][东南西北]")
_COMPASS_EN = re.compile(r"(?<![A-Za-z])(north|south|east|west)(ern|ward|wards|erly)?(?![A-Za-z])", re.I)
SIDE_ANGLE_DEG, CENTER_MAX_DEG, DEPTH_EPS = 8.0, 25.0, 0.05


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def compass_hits(text: str, landmark_words):
    t = text or ""
    for w in sorted(set(landmark_words), key=len, reverse=True):
        if w:
            t = t.replace(w, " ")
    hits = [m.group(0) for m in _COMPASS_CN.finditer(t) if "东西" not in m.group(0)]
    hits += [m.group(0) for m in _COMPASS_EN.finditer(t)]
    return hits


def side_word_present(text: str, side: str) -> bool:
    low = norm(text)
    return any(w.lower() in low for w in SIDE_WORDS.get(side, ()))


# ---------------------------------------------------------------- geometry
def _xy(pt, landmarks):
    if pt is None:
        return None
    if isinstance(pt, str):
        pt = {"landmark": pt}
    xy = pt.get("xy")
    lid = pt.get("landmark")
    if xy is None and lid in landmarks:
        xy = landmarks[lid].get("xy")
    if isinstance(xy, (list, tuple)) and len(xy) == 2:
        try:
            return float(xy[0]), float(xy[1])
        except (TypeError, ValueError):
            return None
    return None


def polyline(entry, landmarks):
    pts = [_xy(entry.get("start"), landmarks)]
    pts += [_xy(p, landmarks) for p in (entry.get("path") or [])]
    pts.append(_xy(entry.get("end"), landmarks) or pts[0])
    pts = [p for p in pts if p]
    return pts


def point_at(pts, f: float):
    """折线按弧长取 f∈[0,1] 处的点。"""
    if not pts:
        return None
    if len(pts) == 1:
        return pts[0]
    segs = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
    total = sum(segs)
    if total <= 1e-9:
        return pts[0]
    target = max(0.0, min(1.0, f)) * total
    acc = 0.0
    for (a, b), L in zip(zip(pts, pts[1:]), segs):
        if acc + L >= target or (a, b) == (pts[-2], pts[-1]):
            t = 0.0 if L <= 1e-9 else (target - acc) / L
            t = max(0.0, min(1.0, t))
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        acc += L
    return pts[-1]


def camera_axis(layout, tile):
    """返回 (C, d_unit, right_unit) 或 None。俯视图坐标 x 向右 y 向下:朝向 d 时,画面右手 = (-dy, dx)。"""
    if not layout or not tile:
        return None
    lms = {lm.get("id"): lm for lm in layout.get("landmarks") or [] if lm.get("id")}
    v = next((v for v in layout.get("views") or [] if isinstance(v, dict) and v.get("tile") == tile), None)
    if not v:
        return None
    c = _xy(v.get("camera_from"), lms)
    t = _xy(v.get("looking_at"), lms)
    if not c or not t:
        return None
    dx, dy = t[0] - c[0], t[1] - c[1]
    L = math.hypot(dx, dy)
    if L <= 1e-9:
        return None
    d = (dx / L, dy / L)
    return c, d, (-d[1], d[0])


def project(cam, p):
    """返回 (纵深 depth, 横向 side, 离轴角 deg;右为正)。"""
    c, d, r = cam
    vx, vy = p[0] - c[0], p[1] - c[1]
    depth = vx * d[0] + vy * d[1]
    side = vx * r[0] + vy * r[1]
    ang = math.degrees(math.atan2(side, depth))
    return depth, side, ang


def expected_sides(ang: float):
    """几何离轴角 → 允许的 side 集合。"""
    if abs(ang) <= SIDE_ANGLE_DEG:
        return {"left", "center", "right"}
    allowed = {"right" if ang > 0 else "left"}
    if abs(ang) <= CENTER_MAX_DEG:
        allowed.add("center")
    return allowed


# ---------------------------------------------------------------- per shot
def load_layout(proj_root: Path, sid: str):
    f = proj_root / "assets" / "concepts" / "scenes" / (sid or "") / "layout.json"
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:  # noqa: BLE001
        return None


def check_shot(shot: dict, group: dict, blocking: dict, layout, proj_root: Path, strict: bool):
    sid_shot = shot.get("shot_id")
    gid = group.get("group_id") if group else None
    tag = f"{gid or '?'}/{sid_shot}"
    errs, warns = [], []
    landmarks = {lm.get("id"): lm for lm in (layout or {}).get("landmarks") or [] if lm.get("id")}
    lm_words = [lm.get("name_en") or "" for lm in landmarks.values()] + [lm.get("name") or "" for lm in landmarks.values()]
    bm_entries = {}
    if group and isinstance(group.get("blocking_map"), dict):
        for e in group["blocking_map"].get("characters") or []:
            if isinstance(e, dict) and e.get("id"):
                bm_entries[e["id"]] = e
    # 本镜在组内的时长占比区间
    f0, f1 = 0.0, 1.0
    if group:
        shots_in = group.get("shots") or []
        durs = {s: float(shot_durations.get(s) or 0) for s in shots_in}
        total = sum(durs.values())
        if total > 0 and sid_shot in shots_in:
            before = sum(durs[s] for s in shots_in[:shots_in.index(sid_shot)])
            f0, f1 = before / total, (before + durs[sid_shot]) / total
    cam = camera_axis(layout, shot.get("view_tile"))
    if shot.get("view_tile") and cam is None and layout:
        warns.append(f"{tag}: layout.json views[{shot.get('view_tile')}] 缺 camera_from/looking_at 地标,几何项跳过")
    elif not shot.get("view_tile"):
        warns.append(f"{tag}: shot_list 该镜 view_tile 为空,几何项跳过")

    entries = [(c, "characters") for c in blocking.get("characters") or [] if isinstance(c, dict)]
    entries += [(c, "creatures") for c in blocking.get("creatures") or [] if isinstance(c, dict)]
    geo = {}   # cid → (mean_depth, [angles])
    declared = {}
    for ch, kind in entries:
        cid = ch.get("id", "?")
        fp = ch.get("frame_position")
        frag = ch.get("space_fragment_en") or ""
        if not isinstance(fp, dict):
            msg = f"{tag}: {cid} 缺 frame_position(画面视角站位结构,2026-09-03;回派 blocking 补写)"
            (errs if strict else warns).append(msg)
            if frag:
                hits = compass_hits(frag, lm_words)
                if hits:
                    warns.append(f"{tag}: {cid} space_fragment_en 含罗盘方位词 {hits}(俯视图视角;改按本镜机位写画面视角)")
            continue
        side, depth, facing = fp.get("side"), fp.get("depth"), fp.get("facing_camera")
        if side not in SIDES:
            errs.append(f"{tag}: {cid} frame_position.side={side!r} 不在 {SIDES}")
        if depth not in DEPTHS:
            errs.append(f"{tag}: {cid} frame_position.depth={depth!r} 不在 {DEPTHS}")
        if facing not in FACINGS:
            errs.append(f"{tag}: {cid} frame_position.facing_camera={facing!r} 不在 {FACINGS}")
        if not frag:
            errs.append(f"{tag}: {cid} 缺 space_fragment_en")
        else:
            hits = compass_hits(frag, lm_words)
            if hits:
                errs.append(f"{tag}: {cid} space_fragment_en 含罗盘方位词 {hits}(俯视图视角;去掉地标词后不得出现东/南/西/北)")
            if side in SIDES and not side_word_present(frag, side):
                errs.append(f"{tag}: {cid} space_fragment_en 缺与 frame_position.side={side} 一致的画幅侧词"
                            f"(如 {'/'.join(SIDE_WORDS[side][:3])})")
        declared[cid] = (side, depth)
        if side == "offscreen" or cam is None:
            continue
        # 角色本镜位置:xy_start/xy_end 覆盖 > blocking_map 折线按时长占比
        pts = []
        for key in ("xy_start", "xy_end"):
            v = ch.get(key)
            if isinstance(v, (list, tuple)) and len(v) == 2:
                pts.append((float(v[0]), float(v[1])))
        if not pts:
            e = bm_entries.get(cid)
            if not e:
                # 骑乘态生物随骑手
                rider = next((r for r in bm_entries.values() if r.get("mounted") == cid), None)
                e = rider
            if not e:
                warns.append(f"{tag}: {cid} 不在组 blocking_map,几何项跳过")
                continue
            pl = polyline(e, landmarks)
            if not pl:
                warns.append(f"{tag}: {cid} blocking_map 起终点无法解析坐标,几何项跳过")
                continue
            pts = [point_at(pl, f0), point_at(pl, f1)]
        depths, angles, behind = [], [], False
        for p in pts:
            dp, _s, ang = project(cam, p)
            depths.append(dp)
            angles.append(ang)
            if dp <= 0:
                behind = True
        if behind:
            warns.append(f"{tag}: {cid} 按 view_tile {shot.get('view_tile')} 机位算在镜头背后(或与机位重合),"
                         "side 未写 offscreen;核对 view_tile 或 blocking_map")
            continue
        allowed = set()
        for ang in angles:
            allowed |= expected_sides(ang)
        if side in SIDES and side not in allowed:
            errs.append(f"{tag}: {cid} frame_position.side={side} 与导演台站位不符——按 view_tile {shot.get('view_tile')} 机位算,"
                        f"该角色离轴角 {', '.join(f'{a:+.0f}°' for a in angles)}(右为正),应为 {sorted(allowed)}")
        elif side == "center" and all(abs(a) > CENTER_MAX_DEG for a in angles):
            warns.append(f"{tag}: {cid} side=center 但几何离轴 {', '.join(f'{a:+.0f}°' for a in angles)} 偏大,核对构图")
        geo[cid] = sum(depths) / len(depths)
    # 纵深层次两两比对
    ids = [c for c in geo if declared.get(c, (None, None))[1] in DEPTH_RANK]
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            ra, rb = DEPTH_RANK[declared[a][1]], DEPTH_RANK[declared[b][1]]
            if ra == rb:
                continue
            near, far = (a, b) if ra < rb else (b, a)
            if geo[near] > geo[far] + DEPTH_EPS:
                errs.append(f"{tag}: {near} 写 {declared[near][1]} 却比 {far}({declared[far][1]})离机位更远"
                            f"(纵深 {geo[near]:.2f} vs {geo[far]:.2f}),与导演台站位不符")
    return errs, warns


shot_durations = {}


def main():
    args, proj_root = parse_args(
        "camera_view_consistent 机检:blocking 画面视角站位(frame_position/space_fragment_en)vs 机位与组级 blocking_map",
        configure=lambda ap: (
            ap.add_argument("shots", nargs="*", help="只查指定镜(如 sh030),缺省全集"),
            ap.add_argument("--strict", action="store_true", help="缺 frame_position 也按违规计(新产出批次用)"),
        ),
    )
    if not spatial_blocking_enabled(proj_root):
        print(f"[camera_view_consistent] {args.project}: skipped: spatial_blocking off(项目输出设置「人物精确空间位置」已关闭)-> PASS")
        sys.exit(0)
    sl_path = proj_root / "directing" / args.ep / "shot_list.json"
    if not sl_path.is_file():
        print(f"VIOLATION 缺 {sl_path}")
        sys.exit(1)
    sl = json.loads(sl_path.read_text())
    shots = [s for s in sl.get("shots") or [] if isinstance(s, dict) and s.get("shot_id")]
    shot_durations.update({s["shot_id"]: s.get("duration_s") for s in shots})
    group_of = {}
    for g in sl.get("generation_groups") or []:
        for s in g.get("shots") or []:
            group_of[s] = g
    only = set(args.shots)
    layouts = {}
    all_errs, all_warns, n = [], [], 0
    for s in shots:
        sid_shot = s["shot_id"]
        if only and sid_shot not in only:
            continue
        bf = proj_root / "directing" / args.ep / "shots" / sid_shot / "blocking.json"
        if not bf.is_file():
            all_warns.append(f"{sid_shot}: 无 blocking.json,跳过")
            continue
        try:
            bj = json.loads(bf.read_text())
        except Exception as e:  # noqa: BLE001
            all_errs.append(f"{sid_shot}: blocking.json 不是合法 JSON({e})")
            continue
        if not (bj.get("characters") or bj.get("creatures")):
            continue
        n += 1
        g = group_of.get(sid_shot)
        scene = s.get("scene_id") or (g or {}).get("scene_id") or ""
        if scene not in layouts:
            layouts[scene] = load_layout(proj_root, scene)
            if layouts[scene] is None:
                all_warns.append(f"场景 {scene} 无 layout.json(存量场景,回派 environment-concept 补布局包);几何项跳过")
        e, w = check_shot(s, g, bj, layouts[scene], proj_root, args.strict)
        all_errs += e
        all_warns += w
    for w in all_warns:
        print("WARN", w)
    for e in all_errs:
        print("VIOLATION", e)
    print(f"[camera_view_consistent] {args.project}/{args.ep}: {n} 镜核对, "
          f"违规 {len(all_errs)} 条, WARN {len(all_warns)} 条 -> {'FAIL' if all_errs else 'PASS'}")
    sys.exit(1 if all_errs else 0)


if __name__ == "__main__":
    main()
