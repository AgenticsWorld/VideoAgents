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
                         "只做内部一致性核对,请用 --expect 指定基准或先经控制台保存一次分镜组设置")
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
    for f in files:
        n += 1
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            errs.append(f"{f.name}: JSON 解析失败 {e}")
            continue
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
            errs.append(f"{f.name}: skill_applied.id={sid!r},项目设定要求 {expected}")
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

    if args.strict:
        errs.extend(warns)
        warns = []
    for w in warns:
        print("WARN", w)
    for e in errs:
        print("FAIL", e)
    print(f"[{CHECK}] {args.project}/{args.ep}: 基准={basis} 期望={expected if expected else ('无技能' if expected == '' else '未知')}; "
          f"{n} 组核对, 违规 {len(errs)} 条, WARN {len(warns)} 条 -> {'FAIL' if errs else 'PASS'}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
