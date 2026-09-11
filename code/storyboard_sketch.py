#!/usr/bin/env python3
"""分镜草图出图(故事板预览页,2026-09-11)。

按 `directing/<ep>/storyboard.json` 的草案镜出**铅笔手绘风格的小草图**,落
`assets/storyboard/<ep>/<S01-01>.png`,台账 `assets/storyboard/<ep>/index.json`(schema storyboard_sketches/1.0)。
参考图:只带出场人物 sheet(≤3),缩到 512px 长边;场景只靠文字(俯视图会误导模型),不用风格参考图;画幅取项目「输出设置」的视频画幅;渠道/模型按 --provider/--model
(缺省:台账里该镜上次用的 → 控制台故事板页保存的草图模型 state.json image_model_prefs.sketch → 全局图像渠道)。

用法:
  python3 code/storyboard_sketch.py --project <slug> --ep ep01 --scene S01            # 整场逐镜出图(已出的跳过)
  python3 code/storyboard_sketch.py --project <slug> --ep ep01 --scene S01 --order 3  # 只出/重出这一镜
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


def main() -> int:
    def configure(ap):
        ap.add_argument("--scene", required=True, help="场次号,如 S01(storyboard.json scenes[].scene_no)")
        ap.add_argument("--order", type=int, default=None, help="草案镜序号(shots_draft[].order);缺省整场")
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
    catalog = sbb.asset_catalog(root)
    board = sbb.load_board(root, ep, catalog)
    scene, shots = sbb.find_shot(board, args.scene, args.order)
    if scene is None:
        print(f"FAIL 场次 {args.scene} 不在 storyboard.json 里(有:{', '.join(s['scene_no'] for s in board['scenes'])})")
        return 2
    if not shots:
        print(f"FAIL 场次 {args.scene} 没有序号 {args.order} 的草案镜")
        return 2
    names = {k: v["name"] for k, v in catalog["characters"].items()}
    names.update({k: v["name"] for k, v in catalog["creatures"].items()})
    aspect = sbb.resolve_aspect(root)
    index = sbb.load_index(root, ep)
    single = args.order is not None
    failed = 0
    sys.path.insert(0, str(sbb.ROOT))
    from modules.genmedia import generate_image, get_config

    for shot in shots:
        key = shot["key"]
        rec = index["shots"].get(key) or {}
        if not single and not args.force and rec.get("status") == "done" and rec.get("file") \
                and (root / rec["file"]).is_file():
            print(f"SKIP {key} 已有草图 {rec['file']}(--force 重出)")
            continue
        note = "" if args.clear_note else (args.note.strip() or str(rec.get("note") or ""))
        prompt, negative = sbb.build_prompt(scene, shot, names, note)
        refs = sbb.collect_refs(root, ep, scene, shot, catalog)
        provider, model = (args.provider, args.model) if (args.provider or args.model) else _default_channel(rec)
        if provider:
            os.environ["VIDEOAGENTS_IMAGE_PROVIDER"] = provider
        else:
            os.environ.pop("VIDEOAGENTS_IMAGE_PROVIDER", None)
        if model:
            os.environ["VIDEOAGENTS_IMAGE_MODEL"] = model
        else:
            os.environ.pop("VIDEOAGENTS_IMAGE_MODEL", None)
        rel = f"{sbb.SKETCH_DIR_REL.format(ep=ep)}/{key}.png"
        try:
            cfg = get_config("image")
            eff_provider, eff_model = cfg["provider"], str(cfg.get("model") or "")
        except RuntimeError as e:
            print(f"FAIL {key} 渠道配置:{e}")
            if not args.dry_run:
                sbb.update_index(root, ep, key, {"status": "failed", "error": str(e)[:500],
                                                 "scene_no": scene["scene_no"], "order": shot["order"]})
            failed += 1
            continue
        if args.dry_run:
            print(f"[dry-run] {key} via {eff_provider} model={eff_model or '-'} aspect={aspect} size={sbb.sketch_size(eff_provider, aspect)} → {rel}")
            print(f"  prompt: {prompt}")
            print(f"  refs: {', '.join(str(r) for r in refs) or '-'}")
            continue
        sbb.update_index(root, ep, key, {
            "status": "running", "error": "", "scene_no": scene["scene_no"], "order": shot["order"],
            "shot_id": shot.get("shot_id") or "", "prompt": prompt, "note": note,
            "provider": eff_provider, "model": eff_model, "aspect": aspect,
            "refs": [str(r.relative_to(root)) if str(r).startswith(str(root)) else str(r.relative_to(sbb.ROOT)) for r in refs],
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S")})
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
            sbb.update_index(root, ep, key, {"status": "done", "file": rel, "error": "",
                                             "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")})
            print(f"OK {key} → {rel}({eff_provider}/{eff_model})")
        except Exception as e:  # noqa: BLE001
            msg = str(e).strip()[-500:] or e.__class__.__name__
            sbb.update_index(root, ep, key, {"status": "failed", "error": msg})
            print(f"FAIL {key}:{msg}")
            failed += 1
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
