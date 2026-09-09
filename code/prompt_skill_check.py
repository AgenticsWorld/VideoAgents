#!/usr/bin/env python3
"""机检 prompt_skill_applied(2026-08-28):组级视频 prompt 是否按项目「提示词技能」设定套用了技能。

对照基准 = 项目 settings.json#prompt_skill.effective(运行时解析快照:派 prompt 工单 / 保存设置 /
H3A 签字时刷新)。逐个 assets/prompts/<ep>/grp*.json 核对回执字段 skill_applied:

  快照要求套用技能 X 时:
    ① skill_applied 存在且为对象;
    ② skill_applied.id == X;
    ③ skill_applied.sha256 == 当前 agents/<X>/SKILL.md 的 sha256(技能文件更新过 = 须按新版重做);
    ④ skill_applied.checklist 为 ≥3 条 {item, pass} 且无 pass=false(false 表示明知未按要点执行);
  快照为不套用(mode=off / auto 未匹配 / 技能被禁用 / 指定技能未安装)时:
    ⑤ skill_applied.id 必须为 null,reason 与快照 reason 一致(不一致 WARN)。

快照缺失(项目从未经运行时解析过)时以 --expect 指定基准;两者都没有 = 只做 ①③④ 内部一致性并 WARN。
注意:本脚本核的是产物回执,「运行中是否真的 Read 过 SKILL.md」由宿主运行时按工具活动记录
另行核验(prompt_skill_read,漏读的运行直接 error 退回),两道互补。

用法:
  python3 code/prompt_skill_check.py --project <slug> --ep ep01 [grp002 grp003 …]
  python3 code/prompt_skill_check.py --project <slug> --ep ep01 --expect 08-video-gen/prompt/sd20-prompt-writing
  python3 code/prompt_skill_check.py --project <slug> --ep ep01 --expect none
退出码:0 = PASS(允许 WARN),1 = FAIL。
"""
import hashlib
import json
import sys
from pathlib import Path

from _common import REPO_ROOT, parse_args

CHECK = "prompt_skill_applied"
NONE_REASONS = ("user_skipped", "no_match", "disabled", "missing")
STRUCT_CHECK = "sd25_prompt_structure"   # 2026-09-09:生效技能为 sd25-pe 时,正文必须按 Seedance 2.5 官方结构写


def sd25_structure_errors(d: dict, name: str) -> list[str]:
    """Seedance 2.5 结构机检(sd25_prompt_structure):dzg6 grp010 实测——同一组多张场景图,2.0 式自由文本绑定对 2.5 无效,
    按官方「类型分组 + 逐镜使用/不采用 + 未采用素材」写才逐镜切换。要求(团队锚点 Overall visual style:/Shot N/[Image N]/
    Global constraints: 照旧保留,与本结构并存):
      ① 素材职责分组:【人物】(或【参考素材职责】)且每张角色/生物图有 `<名>@Image N` 绑定;有 video_refs/audio_refs 时
         【动作与声音】(或【参考素材职责】)逐份写 `[Video N] 用于…` / `[Audio N] 用于…`;
      ② 每个 Shot 段(Shot k: / Shot k｜标题。)含「使用：」与「不采用：」清单(逐镜点名激活);
      ③ 【未采用素材】段(无则写「无」);④ 【保持一致】(或「保持一致：」)。
    【场景】槽位与逐镜「场景激活：」句由 code/sync_shot_plates.py 按背景图机器写入,机检在 shot_plate_bound。"""
    import re
    vp = str(d.get("video_prompt") or "")
    errs = []
    refs = [r for r in (d.get("refs") or []) if isinstance(r, str)]
    has_people = "【人物】" in vp or "【参考素材职责】" in vp
    if not has_people:
        errs.append(f"{name}: 缺【人物】(或【参考素材职责】)分组段——2.5 规范要求逐份素材职责,不得用总括句")
    for i, r in enumerate(refs, 1):
        if r.startswith("assets/concepts/characters/") or r.startswith("assets/concepts/creatures/"):
            if not re.search(r"[^\s@]+\s*@\s*Image\s*%d(?!\d)" % i, vp):
                errs.append(f"{name}: refs[{i-1}] 角色/生物图缺 `<名>@Image {i}` 绑定句")
    vrefs = [v for v in (d.get("video_refs") or []) if isinstance(v, str)]
    arefs = [a for a in (d.get("audio_refs") or []) if isinstance(a, str)]
    if (vrefs or arefs) and not ("【动作与声音】" in vp or "【参考素材职责】" in vp):
        errs.append(f"{name}: 有参考视频/音频但缺【动作与声音】(或【参考素材职责】)分组段")
    for i in range(1, len(vrefs) + 1):
        if not re.search(r"\[Video\s*%d\][^。\n]{0,40}用于" % i, vp):
            errs.append(f"{name}: 缺 `[Video {i}] 用于…` 职责句")
    for i in range(1, len(arefs) + 1):
        if not re.search(r"\[Audio\s*%d\][^。\n]{0,60}(用于|只用于|是)" % i, vp):
            errs.append(f"{name}: 缺 `[Audio {i}] 用于…` 职责句")
    heads = list(re.finditer(r"Shot\s*(\d+)\s*(?:[:：]|[｜|][^。\n]*。)", vp))
    if not heads:
        errs.append(f"{name}: 正文无 Shot N 段")
    for k, h in enumerate(heads):
        end = heads[k + 1].start() if k + 1 < len(heads) else (vp.find("Global constraints:") if "Global constraints:" in vp else len(vp))
        body = vp[h.end():end]
        if "使用：" not in body:
            errs.append(f"{name}: Shot {h.group(1)} 段缺「使用：」激活清单(本镜激活的人物/场景/动作/声音)")
        if "不采用：" not in body:
            errs.append(f"{name}: Shot {h.group(1)} 段缺「不采用：」清单(本镜不激活的素材;无则写「无」)")
    if "【未采用素材】" not in vp:
        errs.append(f"{name}: 缺【未采用素材】段(全部素材都用到也要写「无」)")
    if "【保持一致】" not in vp and not re.search(r"保持一致\s*[:：]", vp):
        errs.append(f"{name}: 缺【保持一致】段")
    return errs


def skill_md_path(skill_id: str) -> Path:
    """skill_id = "<agent_id>/<技能目录>" → agents/<agent_id>/skills/<目录>/SKILL.md。"""
    agent_id, _, sdir = skill_id.rpartition("/")
    return REPO_ROOT / "agents" / agent_id / "skills" / sdir / "SKILL.md"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    def configure(ap):
        ap.add_argument("groups", nargs="*", help="只核对这些组(缺省全部 grp*.json)")
        ap.add_argument("--expect", default=None,
                        help="覆盖对照基准:技能 id(如 08-video-gen/prompt/sd20-prompt-writing)或 none")
        ap.add_argument("--strict", action="store_true", help="WARN 也按 FAIL 计")

    args, proj_root = parse_args("机检 prompt_skill_applied:组级 prompt 是否按项目提示词技能设定套用技能",
                                 configure=configure)
    warns: list[str] = []
    errs: list[str] = []
    struct_errs: list[str] = []

    # ---- 对照基准 ----
    expected: str | None      # "" = 不套用;None = 未知
    exp_reason = ""
    if args.expect is not None:
        expected = "" if args.expect.strip().lower() in ("none", "null", "off", "") else args.expect.strip()
        basis = f"--expect {args.expect}"
    else:
        try:
            ps = json.loads((proj_root / "settings.json").read_text(encoding="utf-8")).get("prompt_skill") or {}
        except Exception:
            ps = {}
        eff = ps.get("effective") if isinstance(ps.get("effective"), dict) else None
        if eff and "skill_id" in eff:
            expected = str(eff.get("skill_id") or "")
            exp_reason = str(eff.get("reason") or "")
            basis = f"settings.json#prompt_skill.effective(mode={eff.get('mode')}, resolved_from={eff.get('resolved_from')})"
        elif ps.get("mode") == "off":
            expected, exp_reason, basis = "", "user_skipped", "settings.json#prompt_skill.mode=off"
        elif ps.get("mode") == "manual" and ps.get("skill_id"):
            expected, basis = str(ps["skill_id"]), "settings.json#prompt_skill.skill_id(manual,无快照)"
        else:
            expected, basis = None, "无快照"
            warns.append("项目 settings.json 无 prompt_skill.effective 快照(从未经运行时解析);"
                         "只做内部一致性核对,请用 --expect 指定基准或先经控制台保存一次视频模型设置")
    exp_sha = ""
    if expected:
        smd = skill_md_path(expected)
        if not smd.is_file():
            errs.append(f"基准技能 {expected} 的 SKILL.md 不存在:{smd}")
        else:
            exp_sha = sha256_of(smd)

    # ---- 逐组核对 ----
    pdir = proj_root / "assets" / "prompts" / args.ep
    if not pdir.is_dir():
        print(f"FAIL 未找到 {pdir}")
        print(f"[{CHECK}] {args.project}/{args.ep}: 0 组核对, 违规 1 条 -> FAIL")
        return 1
    files = sorted(f for f in pdir.glob("grp*.json") if not f.name.endswith(".meta.json"))
    if args.groups:
        want = set(args.groups)
        files = [f for f in files if f.stem in want]
    n = 0
    proj_expected, proj_reason, proj_sha = expected, exp_reason, exp_sha
    for f in files:
        n += 1
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            errs.append(f"{f.name}: JSON 解析失败 {e}")
            continue
        # 组级覆盖(分镜预览「🎛 模型」,2026-08-30):assets/group_settings/<ep>/<grp>.json 的
        # effective 快照优先于项目级基准;--expect 显式指定时仍以 --expect 为准
        expected, exp_reason, exp_sha = proj_expected, proj_reason, proj_sha
        gsf = proj_root / "assets" / "group_settings" / args.ep / f"{f.stem}.json"
        if args.expect is None and gsf.is_file():
            try:
                geff = (json.loads(gsf.read_text(encoding="utf-8")).get("effective") or {})
            except Exception:
                geff = {}
            if isinstance(geff, dict) and "skill_id" in geff:
                expected, exp_reason = str(geff.get("skill_id") or ""), str(geff.get("reason") or "")
                exp_sha = ""
                if expected:
                    gsmd = skill_md_path(expected)
                    if not gsmd.is_file():
                        errs.append(f"{f.name}: 组级基准技能 {expected} 的 SKILL.md 不存在:{gsmd}")
                        continue
                    exp_sha = sha256_of(gsmd)
        sa = d.get("skill_applied")
        if not isinstance(sa, dict):
            errs.append(f"{f.name}: 缺 skill_applied 回执字段(须为对象:套用技能时 {{id, sha256, checklist}},"
                        "不套用时 {id: null, reason})")
            continue
        sid = sa.get("id")
        if expected == "":
            if sid:
                errs.append(f"{f.name}: 项目设定不套用技能(reason={exp_reason or '?'}),但 skill_applied.id={sid}")
            elif exp_reason and sa.get("reason") != exp_reason:
                warns.append(f"{f.name}: skill_applied.reason={sa.get('reason')!r} 与快照 {exp_reason!r} 不一致")
            continue
        if expected and sid != expected:
            errs.append(f"{f.name}: skill_applied.id={sid!r},项目/组级设定要求 {expected}")
            continue
        if not sid:
            if expected is None:
                if sa.get("reason") not in NONE_REASONS:
                    warns.append(f"{f.name}: skill_applied.id 为空且 reason={sa.get('reason')!r} 不在 {NONE_REASONS}")
            continue
        # 套用了技能:sha256 + checklist
        smd = skill_md_path(str(sid))
        if not smd.is_file():
            errs.append(f"{f.name}: skill_applied.id={sid} 对应 SKILL.md 不存在({smd})")
            continue
        want_sha = exp_sha or sha256_of(smd)
        if str(sa.get("sha256") or "").lower() != want_sha:
            errs.append(f"{f.name}: skill_applied.sha256 与当前 {smd.relative_to(REPO_ROOT)} 不一致"
                        f"(回执 {str(sa.get('sha256') or '')[:12]}… / 当前 {want_sha[:12]}…):技能文件已更新或回执造假,按当前版本重做")
        cl = sa.get("checklist")
        if not isinstance(cl, list) or len(cl) < 3:
            errs.append(f"{f.name}: skill_applied.checklist 须为 ≥3 条 {{item, pass}}(现 {len(cl) if isinstance(cl, list) else '非列表'})")
        else:
            bad = [c for c in cl if not isinstance(c, dict) or not c.get("item") or not isinstance(c.get("pass"), bool)]
            if bad:
                errs.append(f"{f.name}: checklist 有 {len(bad)} 条缺 item/pass(bool)")
            fails = [c.get("item") for c in cl if isinstance(c, dict) and c.get("pass") is False]
            if fails:
                errs.append(f"{f.name}: checklist 有 pass=false 的要点:{'; '.join(map(str, fails))[:200]}")
        # Seedance 2.5 结构机检(sd25_prompt_structure):套用 sd25-pe 时正文必须按官方结构写,自述 checklist 不算数
        if str(sid).endswith("/sd25-pe"):
            struct_errs.extend(sd25_structure_errors(d, f.name))

    if args.strict:
        errs.extend(warns)
        warns = []
    for w in warns:
        print("WARN", w)
    for e in errs:
        print("FAIL", e)
    for e in struct_errs:
        print("FAIL", e)
    print(f"[{CHECK}] {args.project}/{args.ep}: 基准={basis} 期望={expected if expected else ('无技能' if expected == '' else '未知')}; "
          f"{n} 组核对, 违规 {len(errs)} 条, WARN {len(warns)} 条 -> {'FAIL' if errs else 'PASS'}")
    if str(expected or "").endswith("/sd25-pe") or struct_errs:
        print(f"[{STRUCT_CHECK}] {args.project}/{args.ep}: 违规 {len(struct_errs)} 条 -> {'FAIL' if struct_errs else 'PASS'}")
    return 1 if (errs or struct_errs) else 0


if __name__ == "__main__":
    sys.exit(main())
