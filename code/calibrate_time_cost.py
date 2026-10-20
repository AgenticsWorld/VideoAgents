#!/usr/bin/env python3
"""calibrate_time_cost.py — 时间尺英文参数校准(2026-10-20,docs/time_cost.md「英文校准」)。

只测量与拟合,不改参数:拟合结果写进报告,参数由人确认后改 modules/time_cost.py 的 DEFAULT_WPM(2026-10-20 首轮 130 → 160)。

  corpus  从项目分镜表收英文台词(说话人 / 形态按对白语音库 plan 判定)+ 自写句 → 语料 JSON
  synth   逐句真合成(付费):genmedia tts 用人物声线、自然语速(不带演法 / 目标时长,情绪照对白语音库当语气传),
          按对白语音库同口径修剪首尾静音后量时长,并记句中静音段;已合成的跳过(key = 文本 + 说话人 + 形态 + 情绪 + 渠道 / 模型)
  fit     按时间尺现行结构(起止 0.7s + 词数 ÷ 词速 + 0.4s × 句中停顿)求默认词速(最小二乘 / 中位无偏),报告校准前 130 wpm、
          现行默认、有人物卡语速时各自的误差与逐人物偏差;另给诊断——按词 / 按音节 / 词 + 音节、停顿分句间句内的自由拟合
          及留一交叉验证(2026-10-20 首轮:按音节、停顿分类都没有比按词更准)
  native  只读:已出片组里只有一句画内台词的镜,量原生音轨人声起止跨度(自相关有声段,不转写),与估时对照,只作参考

用法:
  python3 code/calibrate_time_cost.py corpus --from alices:ep01 --from jidi:ep01 \\
      --extra docs/time_cost_calibration/en_extra_lines.json --out docs/time_cost_calibration/en_corpus.json
  python3 code/calibrate_time_cost.py synth --corpus docs/time_cost_calibration/en_corpus.json \\
      --samples docs/time_cost_calibration/en_samples.json [--limit N] [--dry-run]
  python3 code/calibrate_time_cost.py fit --samples docs/time_cost_calibration/en_samples.json [--library] [--report PATH]
  python3 code/calibrate_time_cost.py native --project alices --ep ep01
音频落 data/calibration/time_cost/<渠道>-<模型>/(不入库);样本 JSON 只存文本、时长与静音段。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics as st
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DATA_DIR, REPO_ROOT  # noqa: E402

from modules import time_cost as tc  # noqa: E402

SCHEMA = "time_cost_calibration/v1"
LEGACY_WPM = 130.0                      # 校准前(#124,2026-10-09)的默认词速与 cpm 换算,复现旧估时作对照


def _read(p: Path):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _write(p: Path, obj) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)


def _project(slug: str) -> Path:
    return DATA_DIR / "projects" / slug


def _rel(p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


# ---------------------------------------------------------------- corpus
def cmd_corpus(args) -> int:
    from modules import dialogue_tts as dt
    lines, seen = [], set()
    for spec in args.sources or []:
        slug, _, ep = spec.partition(":")
        base = _project(slug)
        plan = dt.plan(base, ep or "ep01")
        for e in plan["lines"]:
            text = e["text"]
            if not _is_english(text) or e["status"] == "unbound" or not e["speaker"]:
                continue
            k = (slug, e["speaker"], text)
            if k in seen:
                continue
            seen.add(k)
            lines.append({"project": slug, "speaker": e["speaker"], "variant": e["variant"], "text": text,
                          "emotion": e.get("emotion") or "", "source": f"{ep}/{e['shot_id']}/l{e['idx']:02d}"})
    if args.extra:
        for x in _read(Path(args.extra)).get("lines") or []:
            k = (x["project"], x["speaker"], x["text"])
            if k not in seen:
                seen.add(k)
                lines.append({"project": x["project"], "speaker": x["speaker"], "variant": x.get("variant") or "default",
                              "text": x["text"], "emotion": x.get("emotion") or "", "source": "extra"})
    for i, x in enumerate(lines):
        x["id"] = f"en{i + 1:03d}"
    _write(Path(args.out), {"schema": SCHEMA + "#corpus", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "sources": args.sources or [], "extra": args.extra or "", "lines": lines})
    print(f"语料 {len(lines)} 句 → {args.out}")
    return 0


# ---------------------------------------------------------------- synth
def _key(x: dict, provider: str, model: str) -> str:
    raw = json.dumps([x["text"], x["speaker"], x.get("variant") or "default", x.get("emotion") or "", provider, model],
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _internal_silences(path: Path) -> list[list[float]]:
    from modules import dialogue_tts as dt
    total = dt.probe_duration(path) or 0.0
    out = []
    for a, b in dt.detect_silences(path):
        if a > 0.05 and b < total - 0.05:
            out.append([round(a, 3), round(b, 3)])
    return out


def cmd_synth(args) -> int:
    from modules import dialogue_tts as dt
    corpus = _read(Path(args.corpus))
    provider, model = dt.tts_channel()
    sp = Path(args.samples)
    data = _read(sp) if sp.is_file() else {"schema": SCHEMA, "samples": []}
    done = {s["key"]: s for s in data.get("samples") or [] if s.get("duration_s")}
    adir = DATA_DIR / "calibration" / "time_cost" / f"{provider}-{model}"
    todo = [x for x in corpus["lines"] if _key(x, provider, model) not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"渠道 {provider} / {model};语料 {len(corpus['lines'])} 句,已有 {len(done)},本次合成 {len(todo)} 句 → {_rel(adir)}")
    if args.dry_run:
        for x in todo:
            print(f"  [dry-run] {x['id']} {x['speaker']}: {x['text']}")
        return 0
    from modules.genmedia import generate_tts
    fails = 0
    for x in todo:
        key = _key(x, provider, model)
        raw = adir / "_raw" / f"{x['id']}_{key}.mp3"
        out = adir / f"{x['id']}_{key}.mp3"
        raw.parent.mkdir(parents=True, exist_ok=True)
        var = "" if (x.get("variant") or "default") == "default" else x["variant"]
        try:
            generate_tts(x["text"], str(raw), "", None, x.get("emotion") or "", x["speaker"], var, str(_project(x["project"])))
            trim = dt.trim_file(raw, out)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"  FAIL {x['id']} {x['speaker']}: {e}")
            continue
        s = {**{k: x[k] for k in ("id", "project", "speaker", "variant", "text", "emotion", "source")},
             "key": key, "provider": provider, "model": model, "file": _rel(out),
             "raw_s": round(dt.probe_duration(raw) or 0.0, 3), "duration_s": round(dt.probe_duration(out) or 0.0, 3),
             "trim": {k: trim.get(k) for k in ("lead_s", "tail_s", "peak_db", "noise_db_used") if k in trim},
             "silences": _internal_silences(out), "synthesized_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        done[key] = s
        data.update(schema=SCHEMA, provider=provider, model=model, trim_params=dt.trim_params(0.0),
                    samples=sorted(done.values(), key=lambda r: r["id"]))
        _write(sp, data)
        print(f"  {x['id']} {s['duration_s']:.2f}s(原始 {s['raw_s']:.2f}s,句中静音 {len(s['silences'])} 段)  {x['text'][:60]}")
    print(f"完成:成功 {len(todo) - fails},失败 {fails};样本 {len(done)} 句 → {args.samples}")
    return 1 if fails else 0


# ---------------------------------------------------------------- 诊断用:音节与停顿分类
# 2026-10-20 首轮校准用来比较「按音节 / 停顿分句间句内」与时间尺现行按词口径;数据上没有更准(长的多音节词念得快),
# 时间尺仍按词计。这里保留做诊断,换渠道 / 拿到原生视频实测时可重比。
_SYL_EXCEPT = {
    "every": 2, "everything": 3, "everybody": 4, "everywhere": 3, "evening": 2, "business": 2, "people": 2,
    "idea": 3, "ideas": 3, "area": 3, "create": 2, "created": 3, "lion": 2, "quiet": 2, "quietly": 3, "science": 2,
    "poem": 2, "poet": 2, "ruin": 2, "fire": 2, "hour": 2, "hours": 2, "tired": 2, "naive": 2, "society": 4,
    "diet": 2, "someone": 2, "anyone": 3, "something": 2, "sometimes": 2, "somewhere": 2, "somehow": 2,
    "somebody": 3, "maybe": 2, "rhythm": 2, "hundred": 2, "empire": 3, "toward": 1, "naked": 2, "wicked": 2,
    "cruel": 2, "theater": 3, "museum": 3, "reality": 4, "realize": 3, "theory": 2, "chocolate": 2,
    "interesting": 3, "comfortable": 3, "temperature": 3, "experience": 4, "ok": 2, "okay": 2,
}
_NUM_SYL = (2, 1, 1, 1, 1, 1, 1, 2, 1, 1, 1, 3, 1, 2, 2, 2, 2, 3, 2, 2)
_TENS_SYL = {2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 3, 8: 2, 9: 2}
_ABBR_RE = re.compile(r"\b(?:(Dr|Mr|Mrs|Ms|St|Jr|Sr|Prof|Capt|Col|Gen|Lt|Sgt|Rev|vs|etc)\.(?=\s|$|[,;:!?])|(No)\.(?=\s*\d))", re.I)
_ABBR_SYL = {"dr": 2, "mr": 2, "mrs": 2, "ms": 1, "st": 1, "jr": 2, "sr": 2, "prof": 3, "capt": 2, "col": 2, "gen": 3,
             "lt": 3, "sgt": 2, "rev": 3, "vs": 2, "etc": 4, "no": 2}
_TOKEN_RE = re.compile(r"\d[\d,]*(?:[.:]\d+)?(?:st|nd|rd|th)?%?|[A-Za-z]+(?:['’][A-Za-z]+)*(?:-[A-Za-z]+)*")
_SENT_RE = re.compile(r"[.!?…]+[\"'’”)\]]*(?=\s+\S)|…+(?=\S)")
_CLAUSE_RE = re.compile(r"[,;:]+|\s[-–—]{1,2}\s|[—–]+")


def _is_english(text: str) -> bool:
    return tc.is_word_text(text) and all(ord(ch) < 128 for ch in str(text) if ch.isalpha())


def _cardinal_syl(n: int) -> int:
    if n < 20:
        return _NUM_SYL[n]
    if n < 100:
        return _TENS_SYL[n // 10] + (_NUM_SYL[n % 10] if n % 10 else 0)
    if n < 1000:
        return _NUM_SYL[n // 100] + 2 + (_cardinal_syl(n % 100) if n % 100 else 0)
    for div in (10 ** 9, 10 ** 6, 1000):
        if n >= div:
            return _cardinal_syl(n // div) + 2 + (_cardinal_syl(n % div) if n % div else 0)
    return 1


def _number_syl(tok: str) -> int:
    t = re.sub(r"(?:st|nd|rd|th)?%?$", "", tok.replace(",", ""))
    extra = 2 if tok.endswith("%") else 0
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", t)
    if m:
        mi = int(m.group(2))
        return extra + _cardinal_syl(int(m.group(1))) + (0 if mi == 0 else 1 + _NUM_SYL[mi] if mi < 10 else _cardinal_syl(mi))
    m = re.fullmatch(r"(\d+)\.(\d+)", t)
    if m:
        return extra + _number_syl(m.group(1)) + 1 + sum(_NUM_SYL[int(d)] or 1 for d in m.group(2))
    if not t.isdigit() or len(t) > 12:
        return extra + sum(_NUM_SYL[int(d)] for d in re.sub(r"\D", "", t))
    n = int(t)
    if len(t) == 4 and (1100 <= n <= 1999 or 2010 <= n <= 2099):
        hi, lo = divmod(n, 100)
        return extra + _cardinal_syl(hi) + (2 if lo == 0 else 1 + _NUM_SYL[lo] if lo < 10 else _cardinal_syl(lo))
    return extra + _cardinal_syl(n)


def word_syllables(word: str) -> int:
    """英文词音节数(规则 + 例外表;《爱丽丝》原著全文按词频与 CMU 发音词典一致 99.7%)。"""
    w = word.lower().replace("’", "'").strip("'")
    if w in _SYL_EXCEPT:
        return _SYL_EXCEPT[w]
    if "-" in w:
        return sum(word_syllables(p) for p in w.split("-") if p)
    for suf in ("n't", "'ll", "'ve", "'re", "'d", "'m", "'s"):
        if w.endswith(suf) and len(w) > len(suf):
            stem = w[: -len(suf)]
            extra = (0 if stem in ("ca", "wo", "do", "ai", "are", "were", "sha") else 1) if suf == "n't" else \
                (1 if suf == "'ll" and re.search(r"[^aeiouy]$", stem) else 0) if suf == "'ll" else \
                (1 if suf == "'s" and re.search(r"(?:[sxz]|ch|sh|ce|ge|se|ze)$", stem) else 0)
            return word_syllables(stem) + extra
    w = re.sub(r"[^a-z]", "", w)
    if not w:
        return 0
    n = len(re.findall(r"[aeiouy]+", w[1:] if re.match(r"y[aeiou]", w) else w))
    n += len(re.findall(r"(?<![cgstxlh])i[ao](?!u)", w)) + len(re.findall(r"(?<![cgstxql])io(?=us)", w))
    n += len(re.findall(r"(?<![qg])ua(?!r)", w)) + len(re.findall(r"[aeiouy]ing", w))
    n += len(re.findall(r"(?<=[^aeiou])i(?=er$|est$)", w)) + len(re.findall(r"(?<![gc])eo(?!u)", w))
    n += (1 if re.search(r"[^aeiouy]ism$", w) else 0) + (1 if re.search(r"[^aeiouylrw]lings?$", w) else 0)
    n += len(re.findall(r"(?<=[aeo])y(?=er|a|o)", w))
    n -= len(re.findall(r"[aeiouy][^aeiouy]e(?=ly$|ful$|fully$|ness$|less$|ment$|ments$)", w))
    n -= 1 if re.search(r"[^r]gue$", w) else 0
    if n > 1 and not re.search(r"[^aeiouylrw]l(?:e|es|ed)$", w):
        if re.search(r"[^aeiouy]e$", w) or (re.search(r"[^aeiouy]es$", w) and not re.search(r"(?:[sxz]|ch|sh|c|g)es$", w)) \
                or re.search(r"[^aeiouytd]ed$", w):
            n -= 1
    return max(1, n)


def syllables(text: str) -> int:
    """一句英文的音节数:数字按读法、Dr. 等缩写按全称、≤ 3 字母的全大写词按字母读。"""
    t = str(text or "").replace("’", "'")
    total = sum(_ABBR_SYL.get((m.group(1) or m.group(2)).lower(), 2) for m in _ABBR_RE.finditer(t))
    shouting = all(ch.isupper() for ch in t if ch.isalpha())
    for tok in _TOKEN_RE.findall(_ABBR_RE.sub(" ", t)):
        if tok[0].isdigit():
            total += _number_syl(tok)
        elif not shouting and 2 <= len(tok) <= 3 and tok.isupper() and tok.lower() not in ("no", "ok", "go", "so", "we", "me", "us", "oh", "hi", "yes", "you", "and", "the", "not", "out", "run", "now"):
            total += sum(3 if ch == "W" else 1 for ch in tok)
        else:
            total += word_syllables(tok)
    return total


def pause_split(text: str) -> tuple[int, int]:
    """(句间, 句内) 停顿数:句间 = 后面还有下文的 . ! ? …;句内 = , ; : 与破折号。"""
    s = _ABBR_RE.sub(lambda m: m.group(0)[:-1], str(text or "")).strip()
    s = re.sub(r"(?<=\d)[.,:](?=\d)", "", s)
    s = re.sub(r"[,;:—–\-\s]+$", "", s)
    return len(_SENT_RE.findall(s)), len(_CLAUSE_RE.findall(s))


# ---------------------------------------------------------------- fit
def _legacy_124(text: str, wpm: float | None = None, cpm: float | None = None) -> float:
    """#124(2026-10-09)口径、默认 130 wpm(校准前):0.7 + 词数 ÷ 词速 + 0.4 × 句中停顿;cpm 按 130/252 换算。"""
    w = wpm if wpm and wpm > 0 else (cpm / tc.LETTERS_PER_WORD if cpm and cpm > tc.LETTER_CPM_MIN else
                                      cpm * LEGACY_WPM / tc.DEFAULT_CPM if cpm and cpm > 0 else LEGACY_WPM)
    return round(max(tc.MIN_LINE_S, 0.7 + tc.word_count(text) / (w / 60.0) + 0.4 * tc.inner_pauses(text)), 2)


def _with_wpm(text: str, wpm: float) -> float:
    """时间尺现行结构(起止 ONSET_S + 词数 ÷ 词速 + PAUSE_S × 句中停顿),词速取给定值。"""
    return max(tc.MIN_LINE_S, tc.ONSET_S + tc.word_count(text) * 60.0 / wpm + tc.PAUSE_S * tc.inner_pauses(text))


def _nnls(rows, ys) -> list[float]:
    import numpy as np
    from scipy.optimize import nnls
    coef, _ = nnls(np.array(rows, dtype=float), np.array(ys, dtype=float))
    return [float(c) for c in coef]


def _err(pairs: list[tuple[float, float]]) -> dict:
    """pairs = [(实测, 估时)]:平均偏差(实测 − 估时)、平均绝对误差、比值中位、|误差| > 20% 的句数。"""
    d = [a - e for a, e in pairs]
    return {"n": len(pairs), "bias_s": round(st.mean(d), 3), "mae_s": round(st.mean(abs(x) for x in d), 3),
            "ratio_median": round(st.median(a / e for a, e in pairs if e > 0), 3),
            "over_20pct": sum(1 for a, e in pairs if e > 0 and abs(a / e - 1) > 0.2)}


def _free_fit(rows: list[list[float]], ys: list[float]) -> dict:
    """自由线性拟合(非负)+ 逐一留出交叉验证。"""
    coef = _nnls(rows, ys)
    fit = [(y, max(tc.MIN_LINE_S, sum(c * x for c, x in zip(coef, r)))) for r, y in zip(rows, ys)]
    loo = []
    for i in range(len(rows)):
        ci = _nnls(rows[:i] + rows[i + 1:], ys[:i] + ys[i + 1:])
        loo.append((ys[i], max(tc.MIN_LINE_S, sum(c * x for c, x in zip(ci, rows[i])))))
    return {"coef": [round(c, 4) for c in coef], "fit": _err(fit), "leave_one_out": _err(loo)}


def _library_samples() -> list[dict]:
    """各项目对白语音库里的英文自然语速句(status ok、没有演法目标时长)。"""
    out = []
    for mp in sorted((DATA_DIR / "projects").glob("*/assets/audio/voice/*/tts/tts_manifest.json")):
        try:
            m = _read(mp)
        except (ValueError, OSError):
            continue
        slug = mp.parts[-7]
        for e in m.get("lines") or []:
            if e.get("status") == "ok" and e.get("duration_s") and not e.get("target_s") and _is_english(e.get("text") or ""):
                out.append({"id": f"lib:{slug}/{e.get('shot_id')}/l{e.get('idx')}", "project": slug, "speaker": e.get("speaker") or "",
                            "text": e["text"], "duration_s": float(e["duration_s"]), "source": "library"})
    return out


def cmd_fit(args) -> int:
    data = _read(Path(args.samples))
    samples = [s for s in data.get("samples") or [] if s.get("duration_s")]
    if args.library:
        samples += _library_samples()
    if len(samples) < 8:
        print(f"样本只有 {len(samples)} 句,不够拟合(至少 8 句)")
        return 2
    cards: dict[str, tuple[dict, dict]] = {}
    for s in samples:
        if s["project"] not in cards:
            base = _project(s["project"])
            cards[s["project"]] = (tc.character_wpm(base), tc.character_cpm(base))
    ys = [float(s["duration_s"]) for s in samples]
    texts = [s["text"] for s in samples]
    # 主结果:时间尺现行结构下的默认词速(最小二乘 + 中位无偏)
    resid = [y - tc.ONSET_S - tc.PAUSE_S * tc.inner_pauses(t) for y, t in zip(ys, texts)]
    wc = [tc.word_count(t) for t in texts]
    ls_wpm = 60.0 / (sum(r * w for r, w in zip(resid, wc)) / sum(w * w for w in wc))
    lo, hi = 60.0, 400.0
    for _ in range(60):                                           # 二分:比值中位 = 1
        mid = (lo + hi) / 2
        if st.median(y / _with_wpm(t, mid) for y, t in zip(ys, texts)) > 1:
            hi = mid
        else:
            lo = mid
    med_wpm = (lo + hi) / 2
    card_pairs = []
    for s, y in zip(samples, ys):
        wpm, cpm = cards[s["project"]][0].get(s["speaker"]), cards[s["project"]][1].get(s["speaker"])
        card_pairs.append((y, tc.line_est(s["text"], "", cpm, wpm)))
    sp = [pause_split(t) for t in texts]
    syl = [float(syllables(t)) for t in texts]
    diag = {
        "words_free": _free_fit([[1.0, w, a, b] for w, (a, b) in zip(wc, sp)], ys),
        "syllables_free": _free_fit([[1.0, n, a, b] for n, (a, b) in zip(syl, sp)], ys),
        "words_and_syllables_free": _free_fit([[1.0, w, n, a, b] for w, n, (a, b) in zip(wc, syl, sp)], ys),
    }
    by_spk = {}
    for s, (y, e) in zip(samples, card_pairs):
        by_spk.setdefault((s["project"], s["speaker"]), []).append(y / e)
    spk_rows = [{"project": p, "speaker": k, "n": len(v), "actual_over_est_median": round(st.median(v), 2),
                 "card_wpm": cards[p][0].get(k), "card_cpm": cards[p][1].get(k)} for (p, k), v in sorted(by_spk.items())]
    report = {
        "schema": SCHEMA + "#fit", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "samples": len(samples),
        "provider": data.get("provider"), "model": data.get("model"),
        "formula": f"duration = {tc.ONSET_S} + words × 60 / wpm + {tc.PAUSE_S} × inner_pauses(modules/time_cost.line_est)",
        "default_wpm": {"current": tc.DEFAULT_WPM, "least_squares": round(ls_wpm, 1), "median_unbiased": round(med_wpm, 1)},
        "errors": {
            "legacy_130wpm_default": _err([(y, _legacy_124(t)) for y, t in zip(ys, texts)]),
            "current_default": _err([(y, tc.line_est(t)) for y, t in zip(ys, texts)]),
            "median_unbiased_default": _err([(y, _with_wpm(t, med_wpm)) for y, t in zip(ys, texts)]),
            "current_with_character_cards": _err(card_pairs)},
        "diagnostics": diag, "speakers": spk_rows,
        "lines": [{"id": s["id"], "speaker": s["speaker"], "text": s["text"], "actual_s": y, "words": w,
                   "syllables": int(n), "pauses": [a, b], "current_default_s": round(tc.line_est(s["text"]), 2),
                   "current_card_s": round(e, 2)}
                  for s, y, w, n, (a, b), (_, e) in zip(samples, ys, wc, syl, sp, card_pairs)],
    }
    print(f"样本 {len(samples)} 句({report['provider']} / {report['model']});公式 {report['formula']}")
    dw = report["default_wpm"]
    print(f"默认词速:现行 {dw['current']:g} wpm;最小二乘 {dw['least_squares']} wpm;中位无偏 {dw['median_unbiased']} wpm")
    for k, v in report["errors"].items():
        print(f"  {k:30} 偏差 {v['bias_s']:+.2f}s  平均误差 {v['mae_s']:.2f}s  比值中位 {v['ratio_median']:.2f}  误差>20% {v['over_20pct']}/{v['n']}")
    print("诊断(自由拟合:截距, 单位系数…, 句间停顿, 句内停顿;平均误差 / 留一交叉验证):")
    for k, v in diag.items():
        print(f"  {k:26} {v['coef']}  {v['fit']['mae_s']:.2f}s / {v['leave_one_out']['mae_s']:.2f}s")
    print("人物(有人物卡语速时):实测 / 估时 中位")
    for r in spk_rows:
        print(f"  {r['project']}/{r['speaker']:28} n={r['n']:2}  {r['actual_over_est_median']:.2f}  卡 wpm {r['card_wpm']} cpm {r['card_cpm']}")
    if args.report:
        _write(Path(args.report), report)
        print(f"报告 → {args.report}")
    return 0


# ---------------------------------------------------------------- native
def cmd_native(args) -> int:
    from voice_activity import extract_wav, runs_from_mask, voiced_mask
    base = _project(args.project)
    sl = _read(base / "directing" / args.ep / "shot_list.json")
    shots = {s["shot_id"]: s for s in sl.get("shots") or []}
    wpm_c, cpm_c = tc.character_wpm(base), tc.character_cpm(base)
    from modules import dialogue_tts as dt
    names = dt._name_index(base)
    rows = []
    for g in sl.get("generation_groups") or []:
        clip = base / "assets" / "clips" / args.ep / f"{g.get('group_id')}.mp4"
        if not clip.is_file():
            continue
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "a.wav"
            extract_wav(clip, wav)
            runs = runs_from_mask(voiced_mask(wav))
        t = 0.0
        for sid in g.get("shots") or []:
            s = shots.get(sid) or {}
            dur = float(s.get("duration_s") or 0)
            a, b = t, t + dur
            t += dur
            lines = [ln for ln in s.get("dialogue_lines") or [] if isinstance(ln, dict) and str(ln.get("text") or "").strip()
                     and str(ln.get("placement") or "on") == "on"]
            if len(lines) != 1 or not _is_english(lines[0]["text"]):
                continue
            inw = [(max(x, a - args.pad), min(y, b + args.pad)) for x, y in runs if y > a - args.pad and x < b + args.pad]
            if not inw:
                continue
            spk = dt.resolve_speaker(lines[0], names)[0]
            est = tc.line_est(lines[0]["text"], lines[0].get("pace") or "", cpm_c.get(spk), wpm_c.get(spk))
            span = inw[-1][1] - inw[0][0]
            rows.append((g["group_id"], sid, dur, est, round(span, 2), round(sum(y - x for x, y in inw), 2), lines[0]["text"]))
    for r in rows:
        print(f"{r[0]}/{r[1]} 镜长 {r[2]:g}s  估时 {r[3]:.2f}s  人声跨度 {r[4]:.2f}s(有声合计 {r[5]:.2f}s)  {r[6][:60]}")
    if not rows:
        print("没有可量的单句英文对白镜")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="时间尺英文参数校准(测量与拟合,不改参数)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("corpus", help="收语料")
    p.add_argument("--from", dest="sources", action="append", help="项目:集,如 alices:ep01(可多次)")
    p.add_argument("--extra", default="", help="自写句 JSON({lines:[{project, speaker, text}]})")
    p.add_argument("--out", required=True)
    p = sub.add_parser("synth", help="逐句真合成(付费)")
    p.add_argument("--corpus", required=True)
    p.add_argument("--samples", required=True)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("fit", help="拟合并报告误差")
    p.add_argument("--samples", required=True)
    p.add_argument("--library", action="store_true", help="另收各项目对白语音库里的英文自然语速句")
    p.add_argument("--report", default="")
    p = sub.add_parser("native", help="已出片单句对白镜的原生人声跨度(只读,参考)")
    p.add_argument("--project", required=True)
    p.add_argument("--ep", default="ep01")
    p.add_argument("--pad", type=float, default=0.3)
    args = ap.parse_args(argv)
    return {"corpus": cmd_corpus, "synth": cmd_synth, "fit": cmd_fit, "native": cmd_native}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
