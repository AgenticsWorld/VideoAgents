#!/usr/bin/env python3
"""分镜草图出图(故事板页,2026-09-11)。

按 `directing/<ep>/storyboard.json` 的草案镜出**铅笔手绘风格的小草图**,落
`assets/storyboard/<ep>/<S01-01>.png`,台账 `assets/storyboard/<ep>/index.json`(schema storyboard_sketches/1.0)。
参考图:只带出场人物 sheet(≤3),缩到 512px 长边;场景只靠文字(俯视图会误导模型),不用风格参考图;画幅取项目「输出设置」的视频画幅;渠道/模型按 --provider/--model
提示词(2026-09-12 用户拍板)**人物优先、背景留白**:草图只为看镜头机位、人物比例、神态、动作,地点只留一句短提示放在最后;
机位/神态从分镜文字自动推导成英文短语(modules/storyboard_board.py camera_hint / expression_hint)。
(缺省:台账里该镜上次用的 → 控制台故事板页保存的草图模型 state.json image_model_prefs.sketch → 全局图像渠道)。

两种出图方式:
- 单镜/逐镜:每镜一张图(--scene [--order]);
- 宫格批量(--grid,页面单集标题行「出草图」用):把待出的镜按集内顺序每 4 镜一批,一批出**一张 2×2 宫格图**
  (2026-09-12 由 3×3/9 镜降为 2×2/4 镜:每格更大才画得出表情;1 镜退回单张),切成小图落到各镜的 <S01-01>.png,
  台账记 mode=grid + grid{file,cols,rows,cell};宫格原图存 assets/storyboard/<ep>/_grids/。切出的小图约 1230×690,动态样片按画布等比缩放统一。

用法:
  python3 code/storyboard_sketch.py --project <slug> --ep ep01 --scene S01            # 整场逐镜出图(已出的跳过)
  python3 code/storyboard_sketch.py --project <slug> --ep ep01 --scene S01 --order 3  # 只出/重出这一镜
  python3 code/storyboard_sketch.py --project <slug> --ep ep01 --grid                 # 整集 2×2 宫格批量(已出的跳过;--scene 限一场)
  python3 code/storyboard_sketch.py --project <slug> --ep ep01 --grid --keys S01-01,S01-02,S02-01   # 指定键(≤4,宿主后台分批用;不论状态都出)
      [--note "修改意见/补充描述,会拼进提示词并存台账"] [--provider fal --model fal-ai/...] [--force] [--dry-run]
退出码:0 全部成功、1 有镜失败、2 storyboard.json 缺失或场/镜不存在。
宿主 CLI,Agent(07-directing/storyboard-sketch)只准调用,禁止复制/改写到项目 code/;不改 storyboard.json。
"""
import json
import os
import sys
import time

from _common import DATA_DIR, parse_args

from modules import storyboard_board as sbb


def _default_channel(rec: dict) -> tuple[str, str]:
    """渠道/模型缺省链:台账该镜上次记录 → 控制台故事板页保存的偏好(state.json image_model_prefs.sketch)→ 空(全局)。"""
    if rec.get("provider"):
        return str(rec.get("provider") or ""), str(rec.get("model") or "")
    try:
        from modules.genmedia import image_model_pref
        sm = image_model_pref("sketch")     # state.json image_model_prefs.sketch(兼容旧 sketch_model)
        return sm["provider"], sm["model"]
    except Exception:
        return "", ""


def _shrink_inplace(p, max_edge: int = 1280) -> None:
    """草图只看构图:成图长边压到 1280(有的渠道最小只出 2K,文件 1MB+ 白占预览页流量与磁盘)。"""
    try:
        from PIL import Image
        im = Image.open(p)
        if max(im.size) <= max_edge:
            return
        im.thumbnail((max_edge, max_edge))
        im.save(p, "PNG", optimize=True)
    except Exception:
        pass


def _set_channel(provider: str, model: str) -> None:
    for k, v in (("VIDEOAGENTS_IMAGE_PROVIDER", provider), ("VIDEOAGENTS_IMAGE_MODEL", model)):
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


def _ref_rel(r, root) -> str:
    return str(r.relative_to(root)) if str(r).startswith(str(root)) else str(r.relative_to(sbb.ROOT))


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _sketch_one(root, ep, scene, shot, names, catalog, aspect, args, rec: dict) -> bool:
    """单镜一张图。返回是否成功(dry-run 视为成功)。"""
    from modules.genmedia import generate_image, get_config
    key = shot["key"]
    note = "" if args.clear_note else (args.note.strip() or str(rec.get("note") or ""))
    prompt, negative = sbb.build_prompt(scene, shot, names, note)
    refs = sbb.collect_refs(root, ep, scene, shot, catalog)
    provider, model = (args.provider, args.model) if (args.provider or args.model) else _default_channel(rec)
    _set_channel(provider, model)
    rel = f"{sbb.SKETCH_DIR_REL.format(ep=ep)}/{key}.png"
    try:
        cfg = get_config("image")
        eff_provider, eff_model = cfg["provider"], str(cfg.get("model") or "")
    except Exception as e:  # noqa: BLE001  渠道未配置/genconfig 缺 image 段等,都算渠道配置失败
        print(f"FAIL {key} 渠道配置:{e}")
        if not args.dry_run:
            sbb.update_index(root, ep, key, {"status": "failed", "error": str(e)[:500],
                                             "scene_no": scene["scene_no"], "order": shot["order"]})
        return False
    if args.dry_run:
        print(f"[dry-run] {key} via {eff_provider} model={eff_model or '-'} aspect={aspect} size={sbb.sketch_size(eff_provider, aspect)} → {rel}")
        print(f"  prompt: {prompt}")
        print(f"  refs: {', '.join(str(r) for r in refs) or '-'}")
        return True
    sbb.update_index(root, ep, key, {
        "status": "running", "error": "", "scene_no": scene["scene_no"], "order": shot["order"],
        "shot_id": shot.get("shot_id") or "", "prompt": prompt, "note": note, "mode": "single", "grid": None,
        "provider": eff_provider, "model": eff_model, "aspect": aspect,
        "refs": [_ref_rel(r, root) for r in refs], "started_at": _now()})
    out = root / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.stem}.new.png")
    try:
        size = sbb.sketch_size(eff_provider, aspect)   # 火山 Seedream 5.x 硬限 ≥3686400 像素
        generate_image(prompt, str(tmp), negative=negative, refs=[str(r) for r in refs], aspect=aspect, size=size)
        if not tmp.is_file():
            raise RuntimeError("生成未落盘")
        _shrink_inplace(tmp)
        os.replace(tmp, out)
        sbb.update_index(root, ep, key, {"status": "done", "file": rel, "error": "", "finished_at": _now()})
        print(f"OK {key} → {rel}({eff_provider}/{eff_model})")
        return True
    except Exception as e:  # noqa: BLE001
        msg = str(e).strip()[-500:] or e.__class__.__name__
        sbb.update_index(root, ep, key, {"status": "failed", "error": msg})
        print(f"FAIL {key}:{msg}")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def _sketch_grid(root, ep, panels, names, catalog, aspect, args, index) -> int:
    """一批(≤4 镜,2×2)出一张宫格图并切分落盘。返回失败镜数。"""
    from modules.genmedia import generate_image, get_config
    keys = [sh["key"] for _, sh in panels]
    cols, rows = sbb.grid_layout(len(panels))
    for _, sh in panels:
        rec = index["shots"].get(sh["key"]) or {}
        sh["_note"] = "" if args.clear_note else (args.note.strip() or str(rec.get("note") or ""))
    prompt, negative = sbb.build_grid_prompt(panels, names, cols, rows, aspect)
    refs = sbb.collect_grid_refs(root, ep, panels, catalog)
    provider, model = (args.provider, args.model) if (args.provider or args.model) else _default_channel({})
    _set_channel(provider, model)
    try:
        cfg = get_config("image")
        eff_provider, eff_model = cfg["provider"], str(cfg.get("model") or "")
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {keys[0]}…{keys[-1]} 渠道配置:{e}")
        if not args.dry_run:
            for sc, sh in panels:
                sbb.update_index(root, ep, sh["key"], {"status": "failed", "error": str(e)[:500],
                                                       "scene_no": sc["scene_no"], "order": sh["order"]})
        return len(panels)
    size = sbb.grid_size(eff_provider, aspect)
    grid_rel = f"{sbb.GRID_DIR_REL.format(ep=ep)}/{time.strftime('%Y%m%d-%H%M%S')}_{keys[0]}_{keys[-1]}.png"
    if args.dry_run:
        print(f"[dry-run] grid {cols}x{rows} {', '.join(keys)} via {eff_provider} model={eff_model or '-'} aspect={aspect} size={size} → {grid_rel}")
        print(f"  prompt: {prompt}")
        print(f"  refs: {', '.join(str(r) for r in refs) or '-'}")
        return 0
    grid_meta = {"file": grid_rel, "cols": cols, "rows": rows, "keys": keys}
    for k, (sc, sh) in enumerate(panels):
        sbb.update_index(root, ep, sh["key"], {
            "status": "running", "error": "", "scene_no": sc["scene_no"], "order": sh["order"],
            "shot_id": sh.get("shot_id") or "", "prompt": prompt, "note": sh["_note"], "mode": "grid",
            "grid": dict(grid_meta, cell=k), "provider": eff_provider, "model": eff_model, "aspect": aspect,
            "refs": [_ref_rel(r, root) for r in refs], "started_at": _now()})
    grid_out = root / grid_rel
    grid_out.parent.mkdir(parents=True, exist_ok=True)
    tmp = grid_out.with_name(f"{grid_out.stem}.new.png")
    try:
        generate_image(prompt, str(tmp), negative=negative, refs=[str(r) for r in refs], aspect=aspect, size=size)
        if not tmp.is_file():
            raise RuntimeError("生成未落盘")
        os.replace(tmp, grid_out)
        outs = [root / sbb.SKETCH_DIR_REL.format(ep=ep) / f"{k}.new.png" for k in keys]
        sbb.split_grid(grid_out, cols, rows, len(panels), outs)
        for (sc, sh), tmp_tile in zip(panels, outs):
            rel = f"{sbb.SKETCH_DIR_REL.format(ep=ep)}/{sh['key']}.png"
            os.replace(tmp_tile, root / rel)
            sbb.update_index(root, ep, sh["key"], {"status": "done", "file": rel, "error": "", "finished_at": _now()})
        print(f"OK grid {cols}x{rows} {', '.join(keys)} → {grid_rel}({eff_provider}/{eff_model})")
        return 0
    except Exception as e:  # noqa: BLE001
        msg = str(e).strip()[-500:] or e.__class__.__name__
        for _, sh in panels:
            sbb.update_index(root, ep, sh["key"], {"status": "failed", "error": msg})
        print(f"FAIL grid {', '.join(keys)}:{msg}")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return len(panels)


def main() -> int:
    def configure(ap):
        ap.add_argument("--scene", default="", help="场次号,如 S01(storyboard.json scenes[].scene_no);--grid 时可省=整集")
        ap.add_argument("--order", type=int, default=None, help="草案镜序号(shots_draft[].order);缺省整场")
        ap.add_argument("--grid", action="store_true", help="宫格批量:每 ≤4 镜出一张 2×2 宫格图再切分(已出的跳过;--keys 指定则不论状态)")
        ap.add_argument("--keys", default="", help="--grid 时指定草图键,逗号分隔(≤9,如 S01-01,S01-02);宿主后台分批用")
        ap.add_argument("--note", default="", help="修改意见/补充描述:拼进提示词并写台账 note(空=沿用台账已有 note)")
        ap.add_argument("--clear-note", action="store_true", help="清掉台账里该镜的 note 后再出图")
        ap.add_argument("--provider", default="", help="图像渠道(须已在「生成模型」页配置);缺省见文件头")
        ap.add_argument("--model", default="", help="图像模型 id;缺省见文件头")
        ap.add_argument("--force", action="store_true", help="已出图的镜也重出(--order 单镜时默认即重出)")
        ap.add_argument("--dry-run", action="store_true", help="只打印提示词/参考图/渠道,不出图不写台账")
    args, root = parse_args("分镜草图出图", configure=configure)
    ep = args.ep
    if not (root / "directing" / ep / "storyboard.json").is_file():
        print(f"MISSING directing/{ep}/storyboard.json(分镜层尚未产出)")
        return 2
    if not args.grid and not args.scene:
        print("FAIL 须指定 --scene(或用 --grid 整集批量)")
        return 2
    catalog = sbb.asset_catalog(root)
    board = sbb.load_board(root, ep, catalog)
    names = {k: v["name"] for k, v in catalog["characters"].items()}
    names.update({k: v["name"] for k, v in catalog["creatures"].items()})
    aspect = sbb.resolve_aspect(root)
    index = sbb.load_index(root, ep)
    sys.path.insert(0, str(sbb.ROOT))

    def _is_done(key: str) -> bool:
        rec = index["shots"].get(key) or {}
        return rec.get("status") == "done" and bool(rec.get("file")) and (root / rec["file"]).is_file()

    if args.grid:
        if args.keys.strip():
            keys = [k.strip() for k in args.keys.split(",") if k.strip()]
            panels = sbb.find_shots_by_keys(board, keys)
            missing = sorted(set(keys) - {sh["key"] for _, sh in panels})
            if missing:
                print(f"FAIL 键不在 storyboard.json 里:{', '.join(missing)}")
                return 2
            if len(panels) > sbb.GRID_MAX_PANELS:
                print(f"FAIL --keys 一次最多 {sbb.GRID_MAX_PANELS} 镜(给了 {len(panels)})")
                return 2
        else:
            if args.scene and sbb.find_shot(board, args.scene)[0] is None:
                print(f"FAIL 场次 {args.scene} 不在 storyboard.json 里(有:{', '.join(s['scene_no'] for s in board['scenes'])})")
                return 2
            panels = [(sc, sh) for sc, sh in sbb.episode_shots(board, args.scene or None)]
            if not args.force:
                skipped = [sh["key"] for _, sh in panels if _is_done(sh["key"])]
                panels = [(sc, sh) for sc, sh in panels if not _is_done(sh["key"])]
                if skipped:
                    print(f"SKIP 已有草图 {len(skipped)} 镜:{', '.join(skipped)}(--force 重出)")
        if not panels:
            print("OK 没有待出的镜")
            return 0
        failed = 0
        for i in range(0, len(panels), sbb.GRID_MAX_PANELS):
            batch = panels[i:i + sbb.GRID_MAX_PANELS]
            if len(batch) == 1:      # 单镜退回单张出图
                sc, sh = batch[0]
                if not _sketch_one(root, ep, sc, sh, names, catalog, aspect, args, index["shots"].get(sh["key"]) or {}):
                    failed += 1
                continue
            failed += _sketch_grid(root, ep, batch, names, catalog, aspect, args, index)
        return 1 if failed else 0

    scene, shots = sbb.find_shot(board, args.scene, args.order)
    if scene is None:
        print(f"FAIL 场次 {args.scene} 不在 storyboard.json 里(有:{', '.join(s['scene_no'] for s in board['scenes'])})")
        return 2
    if not shots:
        print(f"FAIL 场次 {args.scene} 没有序号 {args.order} 的草案镜")
        return 2
    single = args.order is not None
    failed = 0
    for shot in shots:
        key = shot["key"]
        rec = index["shots"].get(key) or {}
        if not single and not args.force and _is_done(key):
            print(f"SKIP {key} 已有草图 {rec['file']}(--force 重出)")
            continue
        if not _sketch_one(root, ep, scene, shot, names, catalog, aspect, args, rec):
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
