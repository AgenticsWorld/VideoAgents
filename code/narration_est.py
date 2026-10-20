#!/usr/bin/env python3
"""narration_est.py — 旁白估时由宿主算(2026-10-20,docs/time_cost.md「旁白」):narration.md 每条 est_duration_s 按时间尺重算。

起因:旁白估时原由 narration 工位手算,中文 16 个项目 484 段旁白实测(narrator manifest)对照——手写估时平均偏长 1.72s、
平均误差 2.13s;按时间尺 line_est(起止 + 有效字数 ÷ 语速 + 停顿,英文按音节)算,比值中位 1.00、平均误差 1.67s。
英文旁白手算更没有口径。改为:narration 工位写完稿只管文本,`--write` 回写估时;只检模式是机检 narration_est_consistent。

旁白估时须偏保守:下游窗口 ≥ 估时 × 1.15、narrator 实测 ≤ 窗口 × 0.9,实测超过估时 × 1.035 就可能超窗回派。时间尺对旁白
无偏(中位 1.00),直接用 44% 的段会超(手写估时偏长,21%)。所以估时 = line_est(medium)× 项目系数:
  系数 = 本项目其它集 narrator 实测(assets/audio/narration/<ep>/manifest.json,未变速的段)的 实测 / line_est 80 分位,
         ≥ 5 段才用,限在 0.8–1.5;按集留出回测超阈 10%(16 个项目 377 段)
  没有历史 = NARRATION_DEFAULT_FACTOR 1.15(全量 25%,除去慢声线的 yddl2 后 1%;慢声线第一集出完后自动学到)

条目头 `[N-xx | anchor: … | est_duration_s: 秒 | …]`,正文 = 条目头下面到空行 / 代码栏 / 下一条为止的各行。
容差 max(0.25s, 10%)(与 check_dialogue_fit line_est_consistent 同口径);--write 只改条目头里的数字,其余一字不动。

用法:python3 code/narration_est.py --project <slug> --ep epNN [--write]
退出码:0 一致(或已回写 / 本集无旁白);1 有条目与重算值不一致(只检模式)或解析不到正文。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402

from modules import time_cost as tc  # noqa: E402

HEAD_RE = re.compile(r"^\[(N-\d+)\s*\|[^\]\n]*?est_duration_s:\s*([0-9]+(?:\.[0-9]+)?)[^\]\n]*\]\s*$", re.M)
_EST_NUM_RE = re.compile(r"(est_duration_s:\s*)([0-9]+(?:\.[0-9]+)?)")
_STOP_RE = re.compile(r"^\s*(?:$|```|\[N-\d+|#)")
TOL_S, TOL_RATIO = 0.25, 0.10


NARRATION_DEFAULT_FACTOR = 1.15
FACTOR_MIN, FACTOR_MAX, FACTOR_MIN_SEGS = 0.8, 1.5, 5


def narration_factor(base: Path, ep: str) -> tuple[float, str]:
    """(系数, 来源说明):本项目其它集 narrator 实测 / line_est 的 80 分位;不够 5 段用默认。"""
    import statistics as st
    ratios = []
    for mp in sorted((Path(base) / "assets" / "audio" / "narration").glob("*/manifest.json")):
        if mp.parent.name == ep:
            continue
        try:
            segs = json.loads(mp.read_text(encoding="utf-8")).get("segments") or []
        except (OSError, ValueError):
            continue
        for s in segs:
            text, dur = str(s.get("text") or "").strip(), s.get("duration_s")
            if not text or not isinstance(dur, (int, float)) or dur <= 0 or s.get("tempo"):
                continue
            est = tc.line_est(text, "medium")
            if est > 0:
                ratios.append(float(dur) / est)
    if len(ratios) < FACTOR_MIN_SEGS:
        return NARRATION_DEFAULT_FACTOR, f"默认 {NARRATION_DEFAULT_FACTOR:g}(其它集旁白实测 {len(ratios)} 段,不足 {FACTOR_MIN_SEGS})"
    k = st.quantiles(ratios, n=5)[3]
    k = round(min(FACTOR_MAX, max(FACTOR_MIN, k)), 2)
    return k, f"按其它集旁白实测 {len(ratios)} 段的 80 分位 {k:g}"


def parse_entries(md: str) -> list[dict]:
    """[{id, est, text, line_no}]:line_no = 条目头所在行(0 起)。"""
    lines = md.split("\n")
    out = []
    for i, ln in enumerate(lines):
        m = HEAD_RE.match(ln)
        if not m:
            continue
        body = []
        for nxt in lines[i + 1:]:
            if _STOP_RE.match(nxt):
                break
            body.append(nxt.strip())
        out.append({"id": m.group(1), "est": float(m.group(2)), "text": " ".join(body).strip(), "line_no": i})
    return out


def narration_est(text: str, factor: float = NARRATION_DEFAULT_FACTOR) -> float:
    """一条旁白的估时(秒,一位小数):时间尺 line_est(语速档 medium,英文按音节)× 项目系数。"""
    return round(tc.line_est(text, "medium") * factor, 1)


def check(md: str, factor: float = NARRATION_DEFAULT_FACTOR) -> list[dict]:
    rows = []
    for e in parse_entries(md):
        new = narration_est(e["text"], factor) if e["text"] else None
        ok = new is not None and abs(new - e["est"]) <= max(TOL_S, TOL_RATIO * new)
        rows.append({**e, "computed": new, "ok": ok})
    return rows


def rewrite(md: str, rows: list[dict]) -> str:
    lines = md.split("\n")
    for r in rows:
        if r["computed"] is not None:
            lines[r["line_no"]] = _EST_NUM_RE.sub(lambda m: f"{m.group(1)}{r['computed']:g}", lines[r["line_no"]], count=1)
    return "\n".join(lines)


def main(argv=None):
    def cfg(ap):
        ap.add_argument("--write", action="store_true", help="按重算值回写 narration.md 条目头的 est_duration_s")
    args, base = parse_args("旁白估时由宿主算 narration_est_consistent", configure=cfg, argv=argv)
    path = base / "story" / "episodes" / args.ep / "narration.md"
    if not path.is_file():
        print(f"SKIP narration_est_consistent: {path.relative_to(base)} 不存在")
        return 0
    md = path.read_text(encoding="utf-8")
    factor, source = narration_factor(base, args.ep)
    rows = check(md, factor)
    if not rows:
        print("SKIP narration_est_consistent: 本集无旁白条目")
        return 0
    empty = [r["id"] for r in rows if not r["text"]]
    bad = [r for r in rows if r["text"] and not r["ok"]]
    speed = f"系数 {source}"
    if args.write:
        new_md = rewrite(md, rows)
        if new_md != md:
            path.write_text(new_md, encoding="utf-8")
        changed = sum(1 for r in rows if r["computed"] is not None and r["computed"] != r["est"])
        print(f"narration_est: {len(rows)} 条,回写 {changed} 条({speed});总估时 "
              f"{sum(r['computed'] or 0 for r in rows):.1f}s")
        for e in empty:
            print(f"FAIL {e} narration_est_consistent: 条目头下面没有旁白正文")
        return 1 if empty else 0
    for r in bad:
        print(f"FAIL {r['id']} narration_est_consistent: 记录 {r['est']:g}s ≠ 重算 {r['computed']:g}s"
              f"(容差 max({TOL_S}s, {TOL_RATIO:.0%});跑 --write 回写)  {r['text'][:40]}")
    for e in empty:
        print(f"FAIL {e} narration_est_consistent: 条目头下面没有旁白正文")
    print(f"narration_est_consistent: {len(rows)} 条,不一致 {len(bad)},无正文 {len(empty)}({speed})")
    return 1 if bad or empty else 0


if __name__ == "__main__":
    sys.exit(main())
