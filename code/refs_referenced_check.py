#!/usr/bin/env python3
"""refs_all_referenced 机检:组 prompt refs / audio_refs 里挂的每一份素材,正文必须至少引用一次。

规则(WORKFLOW §7A / prompt SOUL 素材引用,2026-08-27):
  - refs_all_referenced:refs[i-1] 必须在 video_prompt 以 `[Image i]` 或 `<主体>@Image i` 至少出现一次。
    genmedia 把 refs 逐张作为 image_url 全部发给模型,正文不点名的图是一张「无说明的参考」——
    模型可能忽略它(白占参考图名额,前科 polan3 grp008/009 满员挂不上尾帧),也可能误用它
    (道具比例锚图里的木板/碗漏进画面);官方指南:每次涉及主体都要明确指代,避免省略。
  - audioref_all_referenced:audio_refs[i-1] 同理须以 `[Audio i]` / `@Audio i` 引用。
  - prop_ref_bound:`assets/concepts/props/<PROP-id>/` 下的道具图除被引用外,须以绑定句
    `<道具名>@Image i` 形式绑定(道具首现 Shot 段,后接 scale.prompt_token);只写 `[Image i]`
    不带 `@` 按 WARN,--strict 时按违规。
  - tailframe_declared:refs 含 `*.last_frame.png`(前组尾帧)时,正文必须有开场声明句
    `opening continues from [Image i]` 或改写句 `same location and lighting as [Image i]`
    (SOUL:有前组尾帧锚时开头声明;首镜含尾帧里不存在的角色时用改写句);缺句=尾帧白挂,
    且模型会不受控地延续尾帧构图。
  - 越界引用(`[Image N]` / `[Audio N]` 的 N 超过数组长度)一并按违规报(与 imageref_bound 重叠,顺手核)。

用法:python3 code/refs_referenced_check.py --project <slug> --ep ep01            # 查全批
     python3 code/refs_referenced_check.py --project <slug> --ep ep01 grp002 …   # 只查指定组
退出码:0=通过(可含 WARN),1=有违规(逐条打印)。
prompt 批产出后必须全批跑一遍;video-generation 开跑前对单组复核。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args

# 主体绑定:`<主体>@Image N`(主体名不含空白/标点/@/方括号)
BIND_RE = re.compile(r"([^\s,，。:：;；、@\[\]()（）]+)@Image\s*(\d+)(?!\d)")
IMG_RE = re.compile(r"(?:\[Image\s*(\d+)\]|@Image\s*(\d+))(?!\d)")
AUD_RE = re.compile(r"(?:\[Audio\s*(\d+)\]|@Audio\s*(\d+))(?!\d)")
TAIL_RE = re.compile(r"(?:opening continues from|same location and lighting as)\s*\[Image\s*(\d+)\]")


def _nums(rx, text):
    return {int(a or b) for a, b in rx.findall(text)}


def check_group(pf: Path, strict: bool):
    pj = json.loads(pf.read_text())
    gid = pj.get("group_id", pf.stem)
    vp = pj.get("video_prompt", "") or ""
    refs = [r for r in (pj.get("refs") or []) if isinstance(r, str)]
    arefs = [r for r in (pj.get("audio_refs") or []) if isinstance(r, str)]
    errs, warns = [], []
    used_img = _nums(IMG_RE, vp)
    used_aud = _nums(AUD_RE, vp)
    bound = {int(n) for _, n in BIND_RE.findall(vp)}
    tail_declared = {int(n) for n in TAIL_RE.findall(vp)}
    for n in sorted(used_img):
        if n > len(refs):
            errs.append(f"{gid}: [Image {n}] 越界(refs 仅 {len(refs)} 张)")
    for n in sorted(used_aud):
        if n > len(arefs):
            errs.append(f"{gid}: [Audio {n}] 越界(audio_refs 仅 {len(arefs)} 段)")
    for i, r in enumerate(refs, start=1):
        short = "/".join(r.split("/")[-2:])
        is_tail = r.endswith(".last_frame.png")
        is_prop = "/concepts/props/" in r
        if i not in used_img:
            what = "前组尾帧" if is_tail else "道具图" if is_prop else "参考图"
            errs.append(f"{gid}: [Image {i}] {short} 正文未引用({what}白挂,模型不知其职责)"
                        + ("——须写开场声明句 opening continues from / same location and lighting as [Image %d]" % i
                           if is_tail else
                           "——首现 Shot 段写 `<道具名>@Image %d:` 绑定句后接 scale.prompt_token" % i
                           if is_prop else ""))
            continue
        if is_tail and i not in tail_declared:
            errs.append(f"{gid}: [Image {i}] {short} 是前组尾帧但无开场声明句"
                        f"(opening continues from [Image {i}] / same location and lighting as [Image {i}])")
        if is_prop and i not in bound:
            msg = f"{gid}: [Image {i}] {short} 道具图只写了 [Image {i}]、无 `<道具名>@Image {i}` 绑定句"
            (errs if strict else warns).append(msg)
    for i, r in enumerate(arefs, start=1):
        if i not in used_aud:
            errs.append(f"{gid}: [Audio {i}] {Path(r).name} 正文未引用(须 `<角色>@Audio {i}` 绑定句)")
    return errs, warns


def main():
    args, proj_root = parse_args(
        "refs_all_referenced 机检:组 prompt refs/audio_refs 每份素材正文至少引用一次;道具图须 @Image 绑定;尾帧须开场声明句",
        configure=lambda ap: (
            ap.add_argument("groups", nargs="*", help="只查指定组(如 grp002),缺省全批"),
            ap.add_argument("--strict", action="store_true",
                            help="道具图只有 [Image N] 无 @Image 绑定句也按违规计(新产出批次用)"),
        ),
    )
    files = sorted((proj_root / "assets" / "prompts" / args.ep).glob("grp*.json"))
    files = [f for f in files if not f.name.endswith(".meta.json")]
    if args.groups:
        only = set(args.groups)
        files = [f for f in files if f.stem in only]
    all_errs, all_warns = [], []
    # Independently derive occupants; do not validate only characters already
    # listed in the prompt, which would miss a silently dropped listener.
    from scene_cast import scene_reference_rows, read_json, check_prompt_cast
    source = read_json(proj_root/'directing'/args.ep/'shot_list.json', {})
    cast_refs = scene_reference_rows(proj_root, args.ep, source) if source else {}
    for f in files:
        errs, warns = check_group(f, args.strict)
        errs.extend(f'{f.stem}: {e}' for e in check_prompt_cast(json.loads(f.read_text()), cast_refs.get(f.stem, [])))
        all_errs += errs
        all_warns += warns
    for w in all_warns:
        print("WARN", w)
    for e in all_errs:
        print("VIOLATION", e)
    print(f"[refs_all_referenced] {args.project}/{args.ep}: {len(files)} 组核对, "
          f"违规 {len(all_errs)} 条, WARN {len(all_warns)} 条 -> {'FAIL' if all_errs else 'PASS'}")
    sys.exit(1 if all_errs else 0)


if __name__ == "__main__":
    main()
