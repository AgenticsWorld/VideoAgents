#!/usr/bin/env python3
"""场间衔接句机检 / 回写(scene_link_bound,二期 2026-10-10;docs/scene_links.md)。

导演采纳的剧本场间衔接由宿主登记在 directing/<ep>/shot_list.json 组入口 `transition_in.link {kind, out, in, out_shot, in_shot, …}`。
本 CLI 把登记卡写成两句固定标记句进两侧组的 video_prompt(modules/scene_link_prompts.py):前组**最后一个** Shot 段写
「【场间衔接】本镜最后一个画面落在「…」上…」,本组**第一个** Shot 段写「【场间衔接】本镜第一帧就是「…」…」(非中文界面为 `Scene link:` 英文句)。
幂等:先剔除旧句再写;登记卡撤了就只剔除。句子总是插在同段【运镜对接】【原生先入】句之前,与 sync_motion_pairs / sync_native_leads 先后顺序无关。
机检:
  scene_link_bound      带登记卡的边界两侧组 prompt 都含与当前登记卡一致的句子且落在正确的 Shot 段;残留过期句 = 违规;组 prompt 未写 = skipped
  script_links_landed   对账:导演采纳 / 修改的剧本衔接是否已登记进 shot_list(modules/scene_links.verify_landed);到写提示词这一步
                        登记卡还没写也算违规(上报 orchestrator 让宿主跑 `transition_design.py propose`,不要手改 shot_list)
不带 --write 只机检(退出码 1 = 有违规);--write 回写 + 机检(原 prompt 首次备份到 directing/<ep>/whitebox/prompt_backups/)。

用法:python3 code/sync_scene_links.py --project <slug> --ep ep01 [grp…]            # 机检
     python3 code/sync_scene_links.py --project <slug> --ep ep01 [grp…] --write    # 回写 + 机检
     VIDEOAGENTS_UI_LANG=en …                                                    # 强制句子语言(测试用)
宿主 CLI,Agent 只准调用,禁止复制/改写到项目 code/。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402
from modules import scene_links as sl  # noqa: E402
from modules.scene_link_prompts import sync_episode  # noqa: E402

CHECK = "scene_link_bound"


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8") if p.is_file() else ""
    except OSError:
        return ""


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except (OSError, ValueError):
        return None


def landed(base: Path, ep: str) -> dict:
    """对账 script_links_landed;到提示词阶段「只落了类型、登记卡没写」(WARN)也按违规报。"""
    res = sl.verify_landed(_read_text(base / "story" / "episodes" / ep / "screenplay.md"),
                           _read_text(base / "directing" / ep / "directing_plan.md"),
                           _read_json(base / "directing" / ep / "shot_list.json"),
                           _read_json(base / "directing" / ep / "transition_design.json"))
    return {"errors": res["errors"] + [w for w in res["warns"] if "登记卡" in w], "rows": res["rows"]}


def main():
    args, base = parse_args(__doc__, configure=lambda p: (
        p.add_argument("groups", nargs="*"),
        p.add_argument("--write", action="store_true", help="把场间衔接句写进两侧组 prompt(幂等)")))
    result = sync_episode(base, args.ep, args.groups, args.write)
    land = landed(base, args.ep)
    for p in result["pairs"]:
        lk = p["link"]
        print(f"LINK {p['from_group']} → {p['to_group']}: {sl.kind_label(lk['kind'])} 出「{lk['out']}」→ 入「{lk['in']}」")
    for r in result["groups"]:
        if r["skipped"]:
            print(f"SKIP {r['group_id']}: {r['skipped']}")
        elif r["updated"]:
            print(f"WRITE {r['group_id']} ({r['role']})")
    for e in result["errors"]:
        print("VIOLATION", e)
    for e in land["errors"]:
        print("VIOLATION script_links_landed", e)
    n_err = len(result["errors"]) + len(land["errors"])
    print(json.dumps({"links": len(result["pairs"]), "updated_prompts": result["updated_prompts"]}, ensure_ascii=False))
    print(f"[{CHECK}] {args.project}/{args.ep}: 边界 {len(result['pairs'])} 处, 违规 {n_err} 条 -> {'FAIL' if n_err else 'PASS'}")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
