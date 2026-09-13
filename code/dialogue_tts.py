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
    args, root = parse_args("对白语音库:按人物嗓音模板逐句合成对白 TTS(惰性同步)", configure=configure)
    ep = args.ep
    if not (root / "directing" / ep / "shot_list.json").is_file():
        print(f"MISSING directing/{ep}/shot_list.json")
        return 2
    if args.status:
        print(json.dumps(dt.status(root, ep), ensure_ascii=False, indent=1))
        return 0
    if args.plan:
        p = dt.plan(root, ep)
        out = {"provider": p["provider"], "model": p["model"], "removed": p["removed"],
               "lines": [{k: v for k, v in e.items() if k != "speaker_raw"} for e in p["lines"]]}
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    if not dt.enabled(root) and not args.ignore_setting:
        print("SKIP 项目未开启「生成对白语音」(输出设置 output.dialogue_tts);--ignore-setting 可强制执行")
        return 3
    only = {s.strip() for s in args.shots.split(",") if s.strip()} or None
    m = dt.sync(root, ep, force=args.force, only_shots=only)
    s = m["summary"]
    print(f"OK {dt.LIB_REL.format(ep=ep)} 共 {s['total']} 句:合成 {m['synthesized']} · 可用 {s['ok']} · 未选角 {s['unbound']}"
          f" · 失败 {s['failed']} · 移除 {s['removed']}  ({m['sync_seconds']}s, {m['tts_provider']}/{m['tts_model']})")
    if m["checks"]["est_vs_actual"]:
        print(f"WARN {len(m['checks']['est_vs_actual'])} 句实测时长与 est_duration_s 偏差 >30%(见 tts_manifest.json checks.est_vs_actual)")
    return 4 if s["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
