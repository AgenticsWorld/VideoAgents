#!/usr/bin/env python3
"""对白语音库宿主 CLI(2026-09-13,输出设置「生成对白语音」output.dialogue_tts;实现 modules/dialogue_tts.py)。

按 directing/<ep>/shot_list.json 的对白逐句、用人物嗓音模板(casting.json / 声纹卡 / voiceprint 样本,走 genmedia tts)
合成自然语速语音,落 assets/audio/voice/<ep>/tts/<shot>_l<idx>_<CHAR>.mp3 + tts_manifest.json。
惰性同步:只补 key(台词/音色/样本/渠道)变了或缺失的句子;台词删掉的移到 _prev/。未选角的句子跳过并 WARN,不阻断。

用法:
  python3 code/dialogue_tts.py --project <slug> --ep ep01            # 同步(缺/过期才合成)
  python3 code/dialogue_tts.py --project <slug> --ep ep01 --status   # 只看现状(不合成),JSON
  python3 code/dialogue_tts.py --project <slug> --ep ep01 --plan     # 逐句计划(不合成)
  python3 code/dialogue_tts.py --project <slug> --ep ep01 --force    # 全部重出
  python3 code/dialogue_tts.py ... --ignore-setting                  # 开关关闭也执行(验证/对拍用)
  python3 code/dialogue_tts.py ... --speed 1.2                       # 本次默认语速倍率(覆盖 output.dialogue_tts_speed;
                                                                     #   casting 条目有数字 speed 的句子仍用条目值)
  python3 code/dialogue_tts.py ... --max-pause 0.5                   # 句中停顿压到 ≤0.5s(覆盖 output.dialogue_tts_max_pause)
  python3 code/dialogue_tts.py ... --no-trim                         # 不裁首尾静音(默认裁:首留 0.10s、尾留 0.15s,原声在 _raw/)
修剪是后处理不进 key:参数变了只从 _raw/ 重裁已有句子,不重新调 TTS;--speed 进 key,改了才重出。
退出码:0 完成、2 shot_list 缺失、3 开关关闭且未 --ignore-setting、4 有句子合成失败(其余已落盘)。
"""
import json
import sys

from _common import parse_args  # noqa: F401  (副作用:modules/ 与仓库根入 sys.path)

from modules import dialogue_tts as dt


def main() -> int:
    def configure(ap):
        ap.add_argument("--status", action="store_true", help="只输出现状 JSON,不合成")
        ap.add_argument("--plan", action="store_true", help="输出逐句计划 JSON,不合成")
        ap.add_argument("--force", action="store_true", help="忽略 key 全部重出")
        ap.add_argument("--ignore-setting", action="store_true", help="项目未开启「生成对白语音」也执行")
        ap.add_argument("--shots", default="", help="只同步这些镜(逗号分隔),其余沿用旧台账")
        ap.add_argument("--speed", type=float, default=None,
                        help="本次默认语速倍率 0.5–2.0(默认取项目 output.dialogue_tts_speed;casting 数字 speed 优先)")
        ap.add_argument("--max-pause", type=float, default=None,
                        help="句中停顿上限秒,0=不压缩(默认取项目 output.dialogue_tts_max_pause)")
        ap.add_argument("--no-trim", action="store_true", help="不裁首尾静音")
    args, root = parse_args("对白语音库:按人物嗓音模板逐句合成对白 TTS(惰性同步)", configure=configure)
    ep = args.ep
    if not (root / "directing" / ep / "shot_list.json").is_file():
        print(f"MISSING directing/{ep}/shot_list.json")
        return 2
    if args.status:
        print(json.dumps(dt.status(root, ep), ensure_ascii=False, indent=1))
        return 0
    if args.plan:
        p = dt.plan(root, ep, speed=args.speed)
        out = {"provider": p["provider"], "model": p["model"], "default_speed": p["speed"], "removed": p["removed"],
               "lines": [{k: v for k, v in e.items() if k != "speaker_raw"} for e in p["lines"]]}
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    if not dt.enabled(root) and not args.ignore_setting:
        print("SKIP 项目未开启「生成对白语音」(输出设置 output.dialogue_tts);--ignore-setting 可强制执行")
        return 3
    only = {s.strip() for s in args.shots.split(",") if s.strip()} or None
    if args.speed is not None and dt.num_speed(args.speed) is None:
        print(f"ERROR --speed 须在 {dt.SPEED_RANGE[0]}–{dt.SPEED_RANGE[1]} 之间")
        return 2
    m = dt.sync(root, ep, force=args.force, only_shots=only, speed=args.speed,
                trim=not args.no_trim, max_pause=args.max_pause)
    s = m["summary"]
    print(f"OK {dt.LIB_REL.format(ep=ep)} 共 {s['total']} 句:合成 {m['synthesized']} · 重裁 {m['retrimmed']} · 可用 {s['ok']}"
          f" · 未选角 {s['unbound']} · 失败 {s['failed']} · 移除 {s['removed']}"
          f"  ({m['sync_seconds']}s, {m['tts_provider']}/{m['tts_model']}, 默认语速 x{m['default_speed']:g},"
          f" 修剪={'on' if m['trim']['enabled'] else 'off'} max_pause={m['trim']['max_pause']:g})")
    if m["checks"]["est_vs_actual"]:
        print(f"WARN {len(m['checks']['est_vs_actual'])} 句实测时长与 est_duration_s 偏差 >30%(见 tts_manifest.json checks.est_vs_actual)")
    return 4 if s["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
