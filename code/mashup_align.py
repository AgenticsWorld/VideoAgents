#!/usr/bin/env python3
"""mashup_align.py — mashup 拍时间轴 + 逐字 word_track 一条命令产两件套(v4 声画对齐)。

v3 及以前拍边界靠「字数比例估时 + 停顿盲吸」(avsync.char_rate_boundaries+snap),
leijun 实测 42% 边界误差 >0.5s、73% 多镜拍被均分——「切点压焦点词」无数据支撑。
v4 改为 ASR 实测:faster-whisper 逐词时间戳 → difflib 对齐文稿(台本是唯一文本
事实源,ASR 只提供时间)→ 句边界取实测词界/停顿中点(speechalign.beats_from_asr),
同一份 ASR 顺手装配 edit/{ep}/word_track.json(shot-designer 切镜吸词、cutter 入点
公式、字幕微调、机检 shot_cut_on_word 全靠它)。

用法:
  python3 code/mashup_align.py build --project <slug> --ep ep01 \
      --transcript refs/text/xxx.md [--backend auto|asr|char_rate] \
      [--model small] [--language zh] [--fps 24] [--width 1920] [--height 1080]

行为:
  - 读 mashup/audio_map.json 取母带路径/实测时长/停顿清单(不重测;缺失即 FAIL,
    先跑 mx0-ingest)。
  - backend=auto:能 import faster_whisper 则 asr,否则降级 char_rate 并在 stdout
    明示(降级 = v3 现状:char_rate_boundaries+snap,不比从前差)。
  - asr 后端质量门:match_ratio < 0.6 时整体降级 char_rate(真人录音噪底大/文稿
    与语音不一致时的兜底),beat_track.source.backend 如实登记。
  - 拍长纪律不变:一句一拍,缺省界 [1,20]s;<1s 短拍并入相邻较短拍;>20s 长拍在
    句内次级停顿(audio_map 停顿中点)再切,文本在时间占比最近的句内标点处分割。
  - 产物:mashup/beat_track.json(schema mashup/beat_track@v2 兼容扩展:
    boundary_src 扩 asr_word|asr_silence_mid|interp,source.backend/source.asr,
    顶层 word_track 指针)+ edit/{ep}/word_track.json(wordtrack.v1)。
    footage_sync 盖章仍归 code/check_footage.py --stamp,本工具不写。

机检衔接:word_track_fresh / beat_boundary_on_word(check_footage timeline 段)。
"""
import json
import re
import sys
from pathlib import Path

from _common import parse_args

import avsync
import speechalign as sa

MIN_BEAT_S, MAX_BEAT_S = 1.0, 20.0
MATCH_RATIO_GATE = 0.6
_SPLIT_PUNCT = "，,、；;:："


def _fail(msg: str):
    print(f"[FAIL] {msg}", file=sys.stderr)
    raise SystemExit(1)


def _load_audio_map(proj: Path) -> dict:
    p = proj / "mashup" / "audio_map.json"
    if not p.is_file():
        _fail(f"缺 {p}(先跑 mx0-ingest:母带入册与实测)")
    return json.loads(p.read_text(encoding="utf-8"))


def _read_transcript(proj: Path, rel: str) -> str:
    p = Path(rel)
    p = p if p.is_absolute() else proj / p
    if not p.is_file():
        _fail(f"文稿不存在:{p}")
    return p.read_text(encoding="utf-8")


def _split_long(sent: str, t_in: float, t_out: float,
                silences: list[tuple[float, float]]) -> list[tuple[str, float, float, str]]:
    """>MAX_BEAT_S 的拍在句内停顿中点递归再切;文本在时间占比最近的句内标点处分割。

    返回 [(text, t_in, t_out, boundary_src_of_left_cut), ...];切不动(窗口内无停顿或
    文本无句内标点)时原样返回单条,由拍长机检 beat_span_in_range 兜底上报。
    """
    dur = t_out - t_in
    if dur <= MAX_BEAT_S:
        return [(sent, t_in, t_out, "")]
    mids = [(s + e) / 2 for s, e in silences
            if t_in + MIN_BEAT_S < (s + e) / 2 < t_out - MIN_BEAT_S]
    puncts = [m.start() for m in re.finditer(f"[{_SPLIT_PUNCT}]", sent[:-1])]
    if not mids or not puncts:
        return [(sent, t_in, t_out, "")]
    cut_t = min(mids, key=lambda m: abs(m - (t_in + dur / 2)))
    ratio = (cut_t - t_in) / dur
    cut_i = min(puncts, key=lambda i: abs((i + 1) / len(sent) - ratio)) + 1
    left, right = sent[:cut_i], sent[cut_i:]
    if not left.strip() or not right.strip():
        return [(sent, t_in, t_out, "")]
    return (_split_long(left, t_in, cut_t, silences)
            + [(t[0], t[1], t[2], t[3] or ("silence_mid" if i == 0 else t[3]))
               for i, t in enumerate(_split_long(right, cut_t, t_out, silences))])


def cmd_build(args, proj: Path):
    ep = args.ep
    am = _load_audio_map(proj)
    master_rel = am.get("master")
    total_s = float(am.get("master_duration_s") or 0)
    if not master_rel or total_s <= 0:
        _fail("audio_map.json 缺 master/master_duration_s")
    audio = proj / master_rel
    if not audio.is_file():
        _fail(f"母带不存在:{audio}")
    silences = [(float(s), float(e)) for s, e in (am.get("silences") or [])]

    text = _read_transcript(proj, args.transcript)
    sentences = avsync.split_sentences(text)
    if not sentences:
        _fail("文稿切句结果为空")

    backend = args.backend
    if backend == "auto":
        try:
            import faster_whisper  # noqa: F401
            backend = "asr"
        except ImportError:
            backend = "char_rate"
            print("[WARN ] faster-whisper 未安装,降级 char_rate 后端"
                  "(pip install faster-whisper 可启用 ASR 实测对齐)")

    asr_words: list[dict] = []
    srcs: list[str] = []
    asr_meta: dict = {}
    if backend == "asr":
        prompt = "".join(sentences)[:200]
        asr_model = sa.asr_model(args.model)
        print(f"[INFO ] faster-whisper({asr_model}) 转写母带 {audio.name}"
              f"({total_s:.1f}s,词级时间戳)…")
        asr_words = sa.run_whisper(audio, model_size=asr_model,
                                   language=args.language or None, initial_prompt=prompt)
        bounds, srcs, stats = sa.beats_from_asr(sentences, asr_words, total_s, silences)
        asr_meta = {"model": f"faster-whisper:{asr_model}",
                    "match_ratio": stats["match_ratio"], "anchored": stats["anchored"]}
        if stats["match_ratio"] < MATCH_RATIO_GATE:
            print(f"[WARN ] ASR 对齐命中率 {stats['match_ratio']} < {MATCH_RATIO_GATE},"
                  f"整体降级 char_rate(文稿与语音是否一致?)")
            backend = "char_rate"
    if backend == "char_rate":
        raw = avsync.char_rate_boundaries(sentences, total_s)    # 各句末边界(不含起点 0)
        ends = avsync.snap(raw, avsync.midpoints(silences), tol_s=1.5)
        bounds = [0.0] + list(ends)
        bounds[-1] = total_s                                     # 末边界钉死实测总长
        # 如实标注:snap 实际移动过的边界才是 silence_mid,原值保留的是纯估计(char_rate)
        srcs = ["silence_mid" if abs(ends[i] - raw[i]) > 1e-9 else "char_rate"
                for i in range(len(sentences) - 1)]

    # 组拍:<1s 并入相邻较短拍;>20s 句内停顿再切
    beats: list[dict] = []
    i = 0
    while i < len(sentences):
        t_in, t_out, txt = bounds[i], bounds[i + 1], sentences[i]
        src = srcs[i - 1] if i > 0 and i - 1 < len(srcs) else "start"
        while t_out - t_in < MIN_BEAT_S and i + 1 < len(sentences):
            i += 1
            t_out, txt = bounds[i + 1], txt + sentences[i]
        for j, (st, si, so, cut_src) in enumerate(_split_long(txt, t_in, t_out, silences)):
            beats.append({"t_in": round(si, 3), "t_out": round(so, 3), "text": st,
                          "boundary_src": (src if j == 0 else cut_src or "silence_mid")})
        i += 1
    if beats and beats[0]["t_out"] - beats[0]["t_in"] < MIN_BEAT_S and len(beats) > 1:
        beats[1] = {**beats[1], "t_in": beats[0]["t_in"],
                    "text": beats[0]["text"] + beats[1]["text"],
                    "boundary_src": beats[0]["boundary_src"]}
        beats.pop(0)
    for k, b in enumerate(beats):
        b["beat_id"] = f"bt{k + 1:03d}"
        b["snapped"] = b["boundary_src"] in ("asr_word", "asr_silence_mid", "silence_mid")  # char_rate/interp=False
    beats[-1]["t_out"] = round(total_s, 3)
    beats[-1]["boundary_src_end"] = "master_end"

    # 逐字 word_track:与拍同一份 ASR(char_rate 降级时走 interp),指纹天然一致
    segs = [{"id": b["beat_id"], "start": b["t_in"], "end": b["t_out"], "text": b["text"]}
            for b in beats]
    if backend == "asr":
        words, wstats = sa.align_asr_to_transcript(segs, asr_words, audio)
        source = "asr_align"
        wstats["asr"] = asr_meta["model"]
    else:
        words, wstats = sa.build_interp(segs, audio)
        source = "char_rate_interp"

    wt_rel = f"edit/{ep}/word_track.json"
    bt = {
        "schema": "mashup/beat_track@v2", "ep": ep, "task_id": "mx0-align",
        "fps": args.fps, "width": args.width, "height": args.height,
        "aspect": f"{args.width}:{args.height}" if args.width * 9 != args.height * 16 else "16:9",
        "master_duration_s": total_s, "beat_count": len(beats),
        "source": {
            "text": args.transcript, "audio_map": "mashup/audio_map.json",
            "backend": "asr_word" if backend == "asr" else "char_rate_snap",
            "asr": asr_meta or None,
            "method": ("faster-whisper 逐词时间戳 → difflib 对齐文稿 → 句边界取实测词界/"
                       "停顿中点,未锚定按加权字重插值(speechalign.beats_from_asr);"
                       "<1s 并邻拍,>20s 句内停顿再切" if backend == "asr" else
                       "按句末标点切句 → 非空白字数比例估时 → 吸附停顿中点(tol 1.5s)"
                       "(ASR 不可用/命中率不足的降级路径)"),
        },
        "word_track": wt_rel,
        "beats": [{k: b[k] for k in
                   ("beat_id", "t_in", "t_out", "text", "snapped", "boundary_src")}
                  for b in beats],
    }
    out_bt = proj / "mashup" / "beat_track.json"
    out_bt.parent.mkdir(parents=True, exist_ok=True)
    out_bt.write_text(json.dumps(bt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    track = sa.assemble(ep, proj, audio, "mashup_beat_track", out_bt, segs, words,
                        source, wstats)
    out_wt = proj / wt_rel
    out_wt.parent.mkdir(parents=True, exist_ok=True)
    out_wt.write_text(json.dumps(track, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    anchored = sum(1 for b in beats if b["boundary_src"] in ("asr_word", "asr_silence_mid"))
    print(json.dumps({
        "beat_track": str(out_bt.relative_to(proj)), "word_track": wt_rel,
        "backend": bt["source"]["backend"], "beats": len(beats),
        "anchored_boundaries": anchored,
        "anchor_rate": round(anchored / max(1, len(beats) - 1), 3),
        "word_track_source": source, "word_track_confidence": track["confidence"],
        "match_ratio": (wstats.get("match_ratio") if source == "asr_align" else None),
    }, ensure_ascii=False, indent=1))
    return 0


def main(argv=None) -> int:
    def configure(ap):
        ap.add_argument("cmd", choices=["build"], help="build:产 beat_track + word_track 两件套")
        ap.add_argument("--transcript", required=True, help="文稿路径(项目内相对或绝对)")
        ap.add_argument("--backend", default="auto", choices=["auto", "asr", "char_rate"])
        ap.add_argument("--model", default=None,
                        help="faster-whisper 模型(缺省=设置→高级→语音输入选中的识别模型)")
        ap.add_argument("--language", default=None, help="whisper 语言代码(缺省自动)")
        ap.add_argument("--fps", type=int, default=24)
        ap.add_argument("--width", type=int, default=1920)
        ap.add_argument("--height", type=int, default=1080)

    args, proj = parse_args("mashup 拍时间轴 + word_track 构建(v4 声画对齐)",
                            argv=argv, configure=configure)
    return cmd_build(args, proj)


if __name__ == "__main__":
    sys.exit(main())
