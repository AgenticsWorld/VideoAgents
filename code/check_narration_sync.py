#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_narration_sync.py — 旁白挂点同步机检(narration_anchor_sync,WORKFLOW.md §8B)。

事实源:`directing/epNN/shot_list.json` 的 `narration_anchors`(冻结分镜的旁白挂点唯一事实源)。
被检方:`assets/audio/narration/epNN/narration_track.json`(或 manifest.json,narrator 产出)。

背景(前科 DEF-ep05-audio-0005):shot_list 挂点改版后,混音仍读旧 narration_track 人工连续
铺排,成片旁白与画面错位。本机检把「两文件必须同步」变成硬闸:

  1. track_found            旁白轨清单存在且可解析
  2. segment_ids_match      segments 的 N-xx 集合 == narration_anchors 集合(双向,不多不漏)
  3. anchor_group_match     逐段:段落 anchor 里的 grp 引用必须包含当前挂点定稿的 anchor_group;
                            段落引用的镜号(如有)须与 anchor_shots 相交;anchor 无任何可机读
                            grp 引用 = FAIL(自由文本挂点不可用于铺轨)
  4. groups_exist           段落引用的全部 grp/sh 在 shot_list 的 generation_groups/shots 中合法
  5. window_fit_current     逐段实测 duration_s ≤ 当前挂点 window_s×0.9(§7D ② 按当前分镜重验)
  6. anchor_fingerprint     track 的 anchor_sync.narration_anchors_sha256 == 当前 narration_anchors
                            规范化 JSON 的 sha256——shot_list 挂点一变,指纹即失配,旧轨自动作废

用法:
  python3 code/check_narration_sync.py --project <slug> --ep epNN            # 只检(消费侧必跑)
  python3 code/check_narration_sync.py --project <slug> --ep epNN \
      --stamp --task-id <task_id>     # 1–5 全过后把指纹写入 track 的 anchor_sync(narrator 交付时)

调用时机(缺一即视为流程违规):
  - narrator 交付前:--stamp 盖指纹,随后 vc register 登记新版本;
  - audio-mixing 铺轨前、10-editing/edit 封装前:只检模式,FAIL 即停手上报 orchestrator
    (指纹失配 = shot_list 已改版,须回派 narrator 重签/重合成,严禁按旧轨继续)。
退出码:全 PASS=0,任一 FAIL=1。
项目「📤 输出设置」旁白开关(output.narration_enabled)关闭时整体跳过(skipped: narration off,退出码 0)。
"""
import hashlib
import json
import re
import sys
from datetime import datetime, timezone

from _common import narration_enabled, parse_args

WINDOW_FACTOR = 0.9          # §7D ②:实测时长 ≤ 窗口×0.9
GRP_RE = re.compile(r"grp\d{3}")
SH_RE = re.compile(r"sh\d{3}")


def canonical_fingerprint(narration_anchors) -> str:
    blob = json.dumps(narration_anchors, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def seg_num(seg: dict) -> str | None:
    for k in ("num", "narration_id", "id"):
        if seg.get(k):
            return str(seg[k])
    m = re.search(r"nar[_-]?(\d+)", str(seg.get("seg_id", "")))
    return f"N-{int(m.group(1)):02d}" if m else None


def anchor_tokens(anchor) -> tuple[set, set]:
    """从段落 anchor(dict/str,值可为自由文本)提取全部 grpNNN / shNNN 引用。"""
    text = json.dumps(anchor, ensure_ascii=False)
    return set(GRP_RE.findall(text)), set(SH_RE.findall(text))


def main():
    def configure(ap):
        ap.add_argument("--stamp", action="store_true",
                        help="内容检查(1-5)全过后,把当前挂点指纹写入 track 的 anchor_sync 块")
        ap.add_argument("--task-id", default=None, help="--stamp 必填:盖章任务 ID(留痕)")
        ap.add_argument("--shot-list", default=None, help="显式指定 shot_list.json 路径")
        ap.add_argument("--track", default=None, help="显式指定 narration_track/manifest 路径")

    args, proj = parse_args(__doc__, configure=configure)
    if not narration_enabled(proj):
        print(f"[narration_sync] {args.project}: skipped: narration off"
              "(项目输出设置「旁白」已关闭,全片无旁白,无轨可同步)-> PASS")
        sys.exit(0)
    ep = args.ep
    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool, detail: str = ""):
        checks.append((name, ok))
        print(f"[CHECK] {name}: {'PASS' if ok else 'FAIL'}" + (f"  {detail}" if detail else ""))

    sl_path = args.shot_list or (proj / "directing" / ep / "shot_list.json")
    shot_list = json.load(open(sl_path, encoding="utf-8"))
    anchors = shot_list.get("narration_anchors") or []
    anchor_by_id = {a["narration_id"]: a for a in anchors}
    valid_groups = {g["group_id"] for g in shot_list.get("generation_groups") or []}
    valid_shots = {s["shot_id"] for s in shot_list.get("shots") or []}
    fingerprint = canonical_fingerprint(anchors)

    print(f"=== narration_anchor_sync 机检 ({args.project}/{ep}) ===")
    print(f"shot_list            : {sl_path}")
    print(f"narration_anchors    : {len(anchors)} 条,指纹 {fingerprint[:16]}…")

    # 1. track_found
    track_path = args.track
    if not track_path:
        nar_dir = proj / "assets" / "audio" / "narration" / ep
        for name in ("narration_track.json", "manifest.json"):
            if (nar_dir / name).is_file():
                track_path = nar_dir / name
                break
    if not track_path:
        check("track_found", False, f"{nar_dir}/ 下无 narration_track.json 或 manifest.json")
        print("[RESULT] narration_anchor_sync: FAIL")
        sys.exit(1)
    track = json.load(open(track_path, encoding="utf-8"))
    segments = track.get("segments") or []
    check("track_found", True, str(track_path))

    # 2. segment_ids_match(双向)
    seg_ids = [seg_num(s) for s in segments]
    unknown = [i for i, n in enumerate(seg_ids) if n is None]
    seg_set = {n for n in seg_ids if n}
    missing = sorted(set(anchor_by_id) - seg_set)      # 挂点有、轨里没有
    extra = sorted(seg_set - set(anchor_by_id))        # 轨里有、挂点没有(旧编号残留)
    check("segment_ids_match", not (missing or extra or unknown),
          f"缺配 {missing or '无'};多余/旧编号 {extra or '无'};无法识别编号的段 {unknown or '无'}")

    # 3/4/5. 逐段核对挂点组、引用合法性、窗口
    bad_group, bad_ref, bad_window = [], [], []
    for s in segments:
        nid = seg_num(s)
        a = anchor_by_id.get(nid)
        if not a:
            continue                                   # 已在 check 2 计为 extra
        grps, shs = anchor_tokens(s.get("anchor", {}))
        if not grps:
            bad_group.append(f"{nid}(anchor 无可机读 grp 引用)")
        elif a.get("anchor_group") and a["anchor_group"] not in grps:
            bad_group.append(f"{nid}(轨 {sorted(grps)} 不含定稿 {a['anchor_group']})")
        if a.get("anchor_shots") and shs and not (shs & set(a["anchor_shots"])):
            bad_group.append(f"{nid}(轨镜号 {sorted(shs)} 与定稿 {a['anchor_shots']} 无交集)")
        illegal = sorted((grps - valid_groups) | (shs - valid_shots))
        if illegal:
            bad_ref.append(f"{nid}{illegal}")
        dur, win = s.get("duration_s"), a.get("window_s")
        if dur is None or win is None:
            bad_window.append(f"{nid}(缺 duration_s/window_s)")
        elif dur > win * WINDOW_FACTOR:
            bad_window.append(f"{nid}(实测 {dur}s > {win}×{WINDOW_FACTOR}={win * WINDOW_FACTOR:.2f}s)")
    check("anchor_group_match", not bad_group, "; ".join(bad_group) or "逐段挂点组与定稿一致")
    check("groups_exist", not bad_ref, "; ".join(bad_ref) or "全部 grp/sh 引用合法")
    check("window_fit_current", not bad_window, "; ".join(bad_window) or "逐段 ≤ 当前窗口×0.9")

    content_ok = all(ok for _, ok in checks)

    # --stamp:内容检查全过才允许盖章
    if args.stamp:
        if not args.task_id:
            sys.exit("错误:--stamp 必须带 --task-id(盖章留痕)")
        if not content_ok:
            print("[RESULT] narration_anchor_sync: FAIL(内容检查未过,拒绝盖章)")
            sys.exit(1)
        track["anchor_sync"] = {
            "shot_list": str(sl_path.relative_to(proj) if hasattr(sl_path, "relative_to") else sl_path),
            "narration_anchors_sha256": fingerprint,
            "anchor_count": len(anchors),
            "stamped_by_task": args.task_id,
            "stamped_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }
        tmp = str(track_path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(track, f, ensure_ascii=False, indent=1)
            f.write("\n")
        import os
        os.replace(tmp, track_path)
        print(f"[STAMP] anchor_sync 已写入 {track_path}(记得 vc register 登记新版本)")

    # 6. anchor_fingerprint
    sync = track.get("anchor_sync") or {}
    recorded = sync.get("narration_anchors_sha256")
    if recorded is None:
        check("anchor_fingerprint", False,
              "track 无 anchor_sync 指纹——narrator 未盖章(用 --stamp),旧产物一律视为过期")
    else:
        check("anchor_fingerprint", recorded == fingerprint,
              "指纹一致" if recorded == fingerprint else
              f"指纹失配(track 记 {recorded[:16]}…,当前 {fingerprint[:16]}…)——"
              "shot_list 挂点已改版,须回派 narrator 重签/重合成,严禁按旧轨铺排")

    all_ok = all(ok for _, ok in checks)
    print(f"[RESULT] narration_anchor_sync: {'PASS' if all_ok else 'FAIL'}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
