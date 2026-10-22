#!/usr/bin/env python3
"""演示视频(项目类型 code-video)宿主 CLI:python3 code/codevideo.py <子命令> --project <项目> [选项]

不用视频生成模型:片子是 codevideo/index.html 里的 window.render(t),本 CLI 用无头浏览器逐帧截图、ffmpeg 出片。
页面契约、目录布局与流程见 agents/workflows/codevideo.md。

子命令(退出码:0 成功;1 失败,原因打印在 stderr;2 用法错误;demo 未写 TREATMENT.md 时 3)
  doctor               宿主工具链自检(Chromium / WebGL / ffmpeg / Python 依赖 / 宿主库 / 风格库)
  style [--slug S]     锁定风格:完整风格复制 STYLE.md / style.json 进 codevideo/style/(DEMO.md 由 demo 在方案写成后放出);
                       轻量风格(o98-NN)按风格提示生成 STYLE.md / style.json;都记指纹
  demo [--slug S]      写完 TREATMENT.md 后按需下载本风格样片源码与海报(只读参考,打印路径);轻量风格没有样片,
                       须 --slug 指定一个完整风格(锁定时记了 3 个相近的)
  sync                 校验 codevideo/timeline.json → 写 directing/ep01/shot_list.json + storyboard.json
  still T [T…] [--range a:b:step] [--sheet] [--prefix p] [--out DIR]
                       渲静帧到 codevideo/stills/(--sheet 另拼联系表到 codevideo/sheets/)
  board [--keys id,…]  关键镜(timeline shots[].key;没标就均匀取 ≤ 9 镜)用真代码渲进故事板页 + 联系表
  events               导出 window.EV → codevideo/events.json
  readcheck            页面文字阅读时长(window.TEXTS)
  voice [--force]      codevideo/lines.json 逐句 TTS(旁白声线卡)→ codevideo/voices/<id>.wav + dur.json
  render [--section ID …] [--force] [--workers N]
                       按段渲染到 assets/clips/ep01/<组号>.mp4;画面指纹没变的段跳过
  mix                  跑 codevideo/mix.py → codevideo/out/mix.wav + out/stems/*.wav
  master [--grain N]   拼段 + 两遍 loudnorm(−14 LUFS)→ edit/ep01/final.mp4 + subtitles.srt + poster.jpg + build.sh
  music-fit (--track MUS-NNNN | --file <音频>) [--start auto|秒] [--fade 1.5] [--mood 文本]
                       外来配乐(项目音乐库 / 用户音乐 / 音乐模型生成的原始文件)适配到片长 → codevideo/music/score.wav
                       + beats.json(节拍网格),登记 cue sheet 并同步项目音乐库
  beats [--audio <文件>] 只测节拍(默认 codevideo/music/score.wav)→ codevideo/music/beats.json
  snap [--sections downbeat|beat|off] [--shots beat|off] [--hits beat|off] [--dry-run]
                       把 timeline.json 对到节拍网格(段边界 → 强拍、切点与击点 → 拍点;段内时刻随段边界线性平移,
                       片长不变;改前备份 timeline.before-snap-*.json,改后自动 sync)
  check [--stage contract|timeline|beats|clips|final|delivery|all]
  build [--workers N]  sync → events → voice → score(有 music/score.py 才跑)→ render → mix → master;
                       外来配乐不重跑 music-fit / snap(换了曲子先手动跑这两步)
  info                 状态总览(JSON)
  packs [--fetch NAME | --remove NAME]   采样库 / HDRI 包状态与下载
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import reexec_with_host_python  # noqa: E402  (同时把仓库根与 modules/ 放进 sys.path)

from modules.codevideo import CodeVideoError, NS, project_base  # noqa: E402


def _log(msg: str):
    print(msg, file=sys.stderr, flush=True)


def _emit(obj, as_json: bool):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=1))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            print(f"{k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}")
    else:
        print(obj)


def _times(a) -> list[float]:
    ts = [float(t) for t in a.times]
    if a.range:
        try:
            x0, x1, st = (float(v) for v in a.range.split(":"))
        except ValueError:
            raise SystemExit(f"--range 写法 a:b:step,如 0:50:2.5(收到 {a.range})")
        if st <= 0 or x1 < x0:
            raise SystemExit("--range 需 step > 0 且 b ≥ a")
        x = x0
        while x <= x1 + 1e-9:
            ts.append(round(x, 3))
            x += st
    if not ts:
        raise SystemExit("still 需要时间点或 --range")
    return ts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="codevideo.py", description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n", 2)[2])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def p(name, **kw):
        sp = sub.add_parser(name, **kw)
        sp.add_argument("--project", default=os.environ.get("VIDEOAGENTS_PROJECT", ""), help="项目名(缺省 $VIDEOAGENTS_PROJECT)")
        sp.add_argument("--json", action="store_true", help="结果以 JSON 打印")
        return sp

    d = p("doctor")                  # 与其它子命令一致接受 --project(自检不看项目,忽略)
    d.add_argument("--no-gl", action="store_true", help="不启动浏览器测 WebGL")
    p("style").add_argument("--slug", default="")
    p("demo").add_argument("--slug", default="", help="轻量风格须指定一个完整风格拉它的样片学技法")
    p("sync")
    s = p("still")
    s.add_argument("times", nargs="*")
    s.add_argument("--range", default="")
    s.add_argument("--prefix", default="t_")
    s.add_argument("--out", default="")
    s.add_argument("--sheet", action="store_true")
    b = p("board")
    b.add_argument("--keys", default="")
    p("events")
    p("readcheck")
    v = p("voice")
    v.add_argument("--force", action="store_true")
    r = p("render")
    r.add_argument("--section", nargs="*", default=[])
    r.add_argument("--force", action="store_true")
    r.add_argument("--workers", type=int, default=None)
    p("mix")
    m = p("master")
    m.add_argument("--grain", type=float, default=0)
    mf = p("music-fit")
    mf.add_argument("--track", default="")
    mf.add_argument("--file", default="")
    mf.add_argument("--start", default="auto")
    mf.add_argument("--fade", type=float, default=1.5)
    mf.add_argument("--mood", default="")
    bt = p("beats")
    bt.add_argument("--audio", default="")
    sn = p("snap")
    sn.add_argument("--sections", default="downbeat", choices=("downbeat", "beat", "off"))
    sn.add_argument("--shots", default="beat", choices=("beat", "off"))
    sn.add_argument("--hits", default="beat", choices=("beat", "off"))
    sn.add_argument("--dry-run", action="store_true")
    c = p("check")
    c.add_argument("--stage", default="all")
    bd = p("build")
    bd.add_argument("--workers", type=int, default=None)
    p("info")
    pk = sub.add_parser("packs")
    pk.add_argument("--fetch", default="")
    pk.add_argument("--remove", default="")
    pk.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd not in ("packs", "sync", "info", "style", "music-fit", "beats", "snap"):
        reexec_with_host_python()        # 当前解释器缺 Playwright 时换宿主解释器重跑

    from modules.codevideo import audio as A
    from modules.codevideo import checks as C
    from modules.codevideo import fetch as F
    from modules.codevideo import project as P
    from modules.codevideo import render as R
    from modules.codevideo import timeline as TL

    try:
        if a.cmd == "doctor":
            res = P.doctor(gl=not a.no_gl)
            if a.json:
                _emit(res, True)
            else:
                for it in res["items"]:
                    mark = "✓" if it["ok"] else ("✗" if it["fatal"] else "!")
                    print(f"{mark} {it['id']}  {it['detail']}")
                print("工具链可用" if res["ok"] else "工具链不完整:按上面 ✗ 项处理后再跑")
            return 0 if res["ok"] else 1
        if a.cmd == "packs":
            if a.fetch:
                def prog(done, size):
                    _log(f"  {done / 1e6:.0f}/{(size or 0) / 1e6:.0f} MB")
                _emit(F.fetch_pack(a.fetch, prog), a.json)
            elif a.remove:
                _emit(F.remove_pack(a.remove), a.json)
            else:
                rows = F.pack_status()
                if a.json:
                    _emit(rows, True)
                else:
                    for row in rows:
                        print(f"{'✓' if row['installed'] else '·'} {row['name']:24} {row['size'] / 1e6:6.0f} MB")
            return 0
        if not a.project:
            raise SystemExit("缺 --project")
        base = project_base(a.project)
        if a.cmd == "style":
            _emit(P.lock_style(base, a.slug or None), a.json)
        elif a.cmd == "demo":
            try:
                _emit(P.demo(base, lambda n, total: _log(f"  {n}/{total}"), slug=a.slug), a.json)
            except P.DemoBeforeTreatment as e:
                _log(f"✗ {e}")
                return 3
        elif a.cmd == "sync":
            _emit(TL.sync(base), a.json)
        elif a.cmd == "still":
            out = Path(a.out) if a.out else None
            paths = R.stills(base, _times(a), out_dir=out, prefix=a.prefix)
            res = {"stills": [str(x.relative_to(base)) if x.is_relative_to(base) else str(x) for x in paths]}
            if a.sheet:
                sh = R.sheet(base / NS / "sheets" / f"{a.prefix}sheet.jpg", paths,
                             labels=[x.stem[len(a.prefix):] + " s" for x in paths])
                res["sheet"] = str(sh.relative_to(base))
            _emit(res, a.json)
        elif a.cmd == "board":
            _emit(R.board(base, [k for k in a.keys.split(",") if k] or None, log=_log), a.json)
        elif a.cmd == "events":
            _emit(R.events(base), a.json)
        elif a.cmd == "readcheck":
            res = R.readcheck(base)
            if not res["checked"]:
                _log(f"readcheck 没能运行:{res['reason']}(这不算通过)")
                return 2
            if a.json:
                _emit(res, True)
            else:
                for row in res["bad"]:
                    print(f"BAD {row['id']:<16} {row['t0']:>7}s need {row['need']} got {row['got']} "
                          f"{row.get('reason', '')} {row['text']!r}")
                print(f"{len(res['ok'])} 段文字够读,{len(res['bad'])} 段不够")
            return 1 if res["bad"] else 0
        elif a.cmd == "voice":
            _emit(A.voice(base, force=a.force, log=_log), a.json)
        elif a.cmd == "render":
            _emit(R.render(base, sections=a.section or None, force=a.force, workers=a.workers, log=_log), a.json)
        elif a.cmd == "mix":
            _emit(A.mix(base, log=_log), a.json)
        elif a.cmd == "master":
            _emit(A.master(base, grain=a.grain, log=_log), a.json)
        elif a.cmd == "music-fit":
            from modules.codevideo import beats as BT
            start = a.start if a.start == "auto" else float(a.start)
            _emit(BT.fit_project(base, a.track, a.file, start, a.fade, a.mood, log=_log), a.json)
        elif a.cmd == "beats":
            from modules.codevideo import beats as BT
            audio = Path(a.audio) if a.audio else base / NS / "music" / "score.wav"
            audio = audio if audio.is_absolute() else base / audio
            if not audio.is_file():
                raise CodeVideoError(f"音频不存在:{audio}")
            meter = int((TL.load(base).get("meter") if (base / NS / "timeline.json").is_file() else 4) or 4)
            grid = BT.analyze(audio, meter)
            grid["audio_sha256"] = BT.sha256(audio)
            BT.write_beats(base, grid)
            _emit({k: grid[k] for k in ("bpm", "meter", "confidence")} | {"beats": len(grid["beats"]),
                   "downbeats": len(grid["downbeats"])}, a.json)
        elif a.cmd == "snap":
            from modules.codevideo import beats as BT
            res = BT.snap_project(base, a.sections, a.shots, a.hits, a.dry_run)
            if a.json:
                _emit(res, True)
            else:
                for k in ("sections", "shots", "hits"):
                    for r in res[k]:
                        print(f"{k[:-1]:8} {r['id']:<16} {r['from']:>8.3f} → {r['to']:>8.3f}  ({r['to'] - r['from']:+.3f} s)")
                print(f"bpm {res['bpm']} · 最大移动 {res['max_shift_s']} s" + (" · 预演,未写盘" if a.dry_run
                      else f" · 已写 timeline.json(备份 {res['backup']})"))
        elif a.cmd == "check":
            stages = list(C.STAGES) if a.stage == "all" else [x.strip() for x in a.stage.split(",") if x.strip()]
            bad = [x for x in stages if x not in C.STAGES]
            if bad:
                raise SystemExit(f"--stage 只能是 {', '.join(C.STAGES)} 或 all(收到 {', '.join(bad)})")
            res = C.run(base, stages)
            if a.json:
                _emit(res, True)
            else:
                for st, items in res["stages"].items():
                    for it in items:
                        print(f"{'PASS' if it['ok'] else 'FAIL'} {st}/{it['id']}  {it['detail']}")
                print("全部通过" if res["ok"] else "有未通过项")
            return 0 if res["ok"] else 1
        elif a.cmd == "build":
            _emit(P.build(base, workers=a.workers, log=_log), a.json)
        elif a.cmd == "info":
            _emit(P.info(base), True)
        return 0
    except CodeVideoError as e:
        _log(f"✗ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
