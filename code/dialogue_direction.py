#!/usr/bin/env python3
"""台词演法宿主 CLI(2026-10-02;实现 modules/dialogue_direction.py,工位 07-directing/dialogue-direction,workflow.yaml p6-dialogue-direction)。

给分镜表每句对白写「导演式」演法与目标时长,对白语音库(code/dialogue_tts.py)按它合成:情绪到位、时长落在镜长内。
只动 directing/<ep>/shot_list.json 里 dialogue_lines 的 emotion / delivery 两个键;工位不得直接编辑 shot_list。

用法:
  python3 code/dialogue_direction.py plan  --project <slug> --ep ep01           # 还没写 / 已作废的句子及其上下文(JSON);--all 连已写的一起列
  python3 code/dialogue_direction.py apply --project <slug> --ep ep01 --file directions.json   # 批量写入(--file - 读标准输入)
        directions.json:[{"shot_id":"sh057","idx":0,"direction":"…","scene":"…","pace":"fast"}, …]
        direction 演法:状态 + 怎么演 + 语速 / 音量 / 停顿;scene 场景与对象:在哪、对谁说、刚发生了什么(转述,不用引号引整句话);
        pace 语速档 fast|medium|slow(宿主据此按字数算目标时长并按镜长收口),确有把握可直接给 target_s 秒数
  python3 code/dialogue_direction.py set   --project <slug> --ep ep01 --shot sh057 [--idx 0] --direction "…" [--scene "…"] [--pace fast | --target 4.2]
  python3 code/dialogue_direction.py clear --project <slug> --ep ep01 --shot sh057 [--idx 0]   # 摘掉一句的演法
  python3 code/dialogue_direction.py check --project <slug> --ep ep01           # 机检 dialogue_direction_bound
退出码:0 完成 / PASS、1 机检 FAIL 或写入被拒、2 shot_list 缺失。
"""
import json
import sys

from _common import parse_args  # noqa: F401  (副作用:modules/ 与仓库根入 sys.path)

from modules import dialogue_direction as dd


def main(argv=None) -> int:
    def configure(ap):
        ap.add_argument("cmd", choices=("plan", "apply", "set", "clear", "check"))
        ap.add_argument("--all", action="store_true", help="plan:连已写好的句子一起列")
        ap.add_argument("--file", help="apply:演法 JSON 文件(- = 标准输入)")
        ap.add_argument("--shot", help="set / clear:镜号")
        ap.add_argument("--idx", type=int, default=0, help="set / clear:镜内第几句台词(0 起,只数有台词的句子)")
        ap.add_argument("--direction", default="", help="set:演法")
        ap.add_argument("--scene", default="", help="set:场景与对象")
        ap.add_argument("--pace", default="", help="set:语速档 fast|medium|slow")
        ap.add_argument("--target", type=float, default=None, help="set:目标时长(秒);给了就不看 --pace")
        ap.add_argument("--by", default="", help="操作方(缺省 07-directing/dialogue-direction)")

    args, root = parse_args(__doc__.splitlines()[0], configure=configure, argv=argv)
    ep = args.ep
    if not (root / "directing" / ep / "shot_list.json").is_file():
        print(f"MISSING directing/{ep}/shot_list.json")
        return 2
    if args.cmd == "plan":
        rows = dd.context(root, ep)
        todo = rows if args.all else [r for r in rows if r["speaker"] and r["status"] != "ok"]
        print(json.dumps({"ep": ep, "total": sum(1 for r in rows if r["speaker"]),
                          "done": sum(1 for r in rows if r["speaker"] and r["status"] == "ok"),
                          "paces": dd.PACES, "lines": todo}, ensure_ascii=False, indent=1))
        return 0
    if args.cmd in ("apply", "set"):
        if args.cmd == "apply":
            if not args.file:
                print("apply 需要 --file <演法 JSON>(- = 标准输入)")
                return 1
            raw = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
            try:
                items = json.loads(raw)
            except ValueError as err:
                print(f"演法 JSON 解析失败:{err}")
                return 1
            items = items.get("lines") if isinstance(items, dict) else items
        else:
            if not args.shot:
                print("set 需要 --shot")
                return 1
            items = [{"shot_id": args.shot, "idx": args.idx, "direction": args.direction, "scene": args.scene,
                      "pace": args.pace, "target_s": args.target}]
        res = dd.apply(root, ep, items or [], by=args.by)
        for e in res.get("errors") or []:
            print(f"[FAIL ] {e}")
        if res.get("errors"):
            print("整批未写入:改好再提交")
            return 1
        for n in res.get("notes") or []:
            print(f"[NOTE ] {n}")
        print(f"OK 写入 {res['written']} 句演法" + (f",补回 {res['backfilled_emotion']} 句情绪标注" if res.get("backfilled_emotion") else ""))
        return 0
    if args.cmd == "clear":
        if not args.shot:
            print("clear 需要 --shot")
            return 1
        print("OK 已摘掉" if dd.clear(root, ep, args.shot, args.idx) else "这句本来就没有演法")
        return 0
    res = dd.check(root, ep)
    for f in res["fails"]:
        print(f"[FAIL ] {f}")
    for w in res["warns"]:
        print(f"[WARN ] {w}")
    print(f"[{'PASS' if res['ok'] else 'FAIL'} ] dialogue_direction_bound:{res['bound']}/{res['total']} 句有演法")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
